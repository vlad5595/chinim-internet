#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор статей для SEO-конвейера KIVI VPN.

Запускается из GitHub Actions раз в день:
  1. читает очередь topics_queue.csv;
  2. берёт первую тему со статусом new (по приоритету);
  3. генерит статью через DeepSeek V4-Flash;
  4. проверяет на запрещённые формулировки (281-ФЗ);
  5. пишет .md в src/content/articles/;
  6. отмечает тему done, вписывает url и дату;
  7. (опц.) пингует IndexNow.

Зависимостей нет — только стандартная библиотека Python 3.
Ключ DeepSeek берётся из переменной окружения DEEPSEEK_API_KEY (GitHub Secret).
"""

import csv
import datetime
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

# --- Пути (можно переопределить через окружение — удобно для тестов) ---
QUEUE_FILE = os.environ.get("QUEUE_FILE", "topics_queue.csv")
ARTICLES_DIR = os.environ.get("ARTICLES_DIR", "src/content/articles")

# --- DeepSeek ---
API_URL = "https://api.deepseek.com/chat/completions"
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

# --- Проект ---
SITE_BASE = "https://chinim-internet.online"
REF_LINK = os.environ.get("KIVI_REF_LINK", "https://t.me/kivi_vpn_bot?start=448587092")

SERVICE_DISPLAY = {
    "youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram",
    "chatgpt": "ChatGPT", "other": "Общее",
}

# Запрещённые формулировки: если попадут в текст — НЕ публикуем, оставляем тему new.
FORBIDDEN = [
    "обход блокир", "обойти блокир", "обход ркн", "обойти ркн",
    "роскомнадзор", "как обойти", "средство обхода",
]

SSL_CTX = ssl.create_default_context()

SYSTEM_PROMPT = """Ты — редактор информационного сайта о доступе в интернет. Пишешь полезные статьи-разборы для обычных людей, столкнувшихся с проблемой доступа к сайту или приложению.

СТРОГИЕ ЗАПРЕТЫ (юридические, нарушать нельзя):
- НЕ упоминай «обход блокировок», «обойти блокировку», «обойти РКН», «Роскомнадзор», «запрещённые сайты».
- НЕ давай инструкций по настройке VPN: никаких конфигов, протоколов настройки, QR-кодов, названий приложений для обхода.
- НЕ призывай «установить VPN, чтобы обойти».
Ты пишешь про диагностику проблемы и общие бытовые решения (сменить сервер, очистить кэш, обновить приложение, проверить скорость, понизить качество видео).

СТРУКТУРА (важно для поисковой выдачи):
- Первые 1–2 предложения — прямой сжатый ответ на вопрос из заголовка (для сниппетов и голосовых ассистентов).
- Затем 3–5 разделов с подзаголовками (##): причины, что делать по шагам, разделы по устройствам где уместно.
- Раздел «## Коротко» — маркированное резюме из 3–4 пунктов.
- Раздел «## Частые вопросы» — 3 пары вопрос-ответ.
- Естественно, без переспама, вплети словоформы из списка ключей в подзаголовки и текст.
- Тон спокойный, полезный, живой человеческий язык, без воды и без рекламы.
- НЕ добавляй призыв воспользоваться каким-либо сервисом и не ставь заголовок H1 (#): это добавит редактор отдельно.

ФОРМАТ ОТВЕТА — строго JSON, без markdown-обёртки и без ```:
{"title": "...", "description": "... 140-160 символов", "body": "... markdown, начинается с абзаца-ответа, дальше ## разделы"}"""


def load_rows():
    with open(QUEUE_FILE, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader), reader.fieldnames


def pick_next(rows):
    candidates = [r for r in rows if r["status"] == "new"]
    if not candidates:
        return None
    return min(candidates, key=lambda r: int(r["priority"]))


def build_messages(topic):
    user = (
        f"Тема статьи: {topic['topic_title']}\n"
        f"Сервис: {topic['service']}\n"
        f"Устройство: {topic['device']}\n"
        f"Ключевые запросы, которые статья должна закрыть (вплети естественно): "
        f"{topic['keywords']}\n\n"
        "Напиши статью строго по правилам. Объём 600–900 слов."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def call_deepseek(messages):
    """Живой вызов DeepSeek. Вынесен отдельно, чтобы тесты могли подменить."""
    payload = json.dumps({
        "model": MODEL,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},   # отключаем режим размышления (V4-синтаксис)
        "temperature": 0.6,
        "max_tokens": 4000,
    }).encode("utf-8")

    req = urllib.request.Request(
        API_URL, data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120, context=SSL_CTX) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"DeepSeek вернул HTTP {e.code}. Тело ответа:\n{body}", file=sys.stderr)
        raise
    return data["choices"][0]["message"]["content"]


def parse_article(raw):
    """Разбирает JSON-ответ модели, чистит возможную ```-обёртку."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    obj = json.loads(text)
    return obj["title"].strip(), obj["description"].strip(), obj["body"].strip()


def has_forbidden(text):
    low = text.lower()
    return [p for p in FORBIDDEN if p in low]


def assemble_md(title, description, body, service, slug):
    today = datetime.date.today().isoformat()
    disp = SERVICE_DISPLAY.get(service, "Общее")
    safe_title = title.replace('"', "'")
    safe_desc = description.replace('"', "'")
    cta = (
        "\n\n## Если ничего не помогло\n\n"
        "Если проблема повторяется на разных серверах, чаще всего дело в самом "
        "VPN-сервисе: бесплатные часто перегружены и не тянут видео. Для стабильного "
        f"и быстрого доступа можно воспользоваться сервисом [KIVI VPN]({REF_LINK}) — "
        "подключение занимает пару минут прямо в Telegram.\n"
    )
    return (
        f"---\n"
        f'title: "{safe_title}"\n'
        f'description: "{safe_desc}"\n'
        f"pubDate: {today}\n"
        f'service: "{disp}"\n'
        f"draft: false\n"
        f"---\n\n"
        f"{body}\n"
        f"{cta}"
    )


def write_article(slug, content):
    os.makedirs(ARTICLES_DIR, exist_ok=True)
    path = os.path.join(ARTICLES_DIR, f"{slug}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def update_queue(rows, fieldnames, topic, url):
    today = datetime.date.today().isoformat()
    for r in rows:
        if r["id"] == topic["id"]:
            r["status"] = "done"
            r["publish_date"] = today
            r["url"] = url
            break
    with open(QUEUE_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def ping_indexnow(url):
    key = os.environ.get("INDEXNOW_KEY", "")
    if not key:
        print("IndexNow: ключ не задан, пропускаю пинг.")
        return
    ping_url = f"https://yandex.com/indexnow?url={url}&key={key}"
    try:
        with urllib.request.urlopen(ping_url, timeout=30, context=SSL_CTX) as resp:
            print(f"IndexNow: пинг отправлен, статус {resp.status}")
    except Exception as e:
        print(f"IndexNow: пинг не прошёл ({e}) — не критично, робот дойдёт сам.")


def main():
    if not API_KEY:
        sys.exit("Нет DEEPSEEK_API_KEY в окружении.")

    rows, fieldnames = load_rows()
    topic = pick_next(rows)
    if not topic:
        print("Очередь пуста: тем со статусом new нет. Публиковать нечего.")
        return

    print(f"Беру тему #{topic['id']}: {topic['topic_title']}")
    raw = call_deepseek(build_messages(topic))
    title, description, body = parse_article(raw)

    bad = has_forbidden(title + " " + description + " " + body)
    if bad:
        # НЕ публикуем: оставляем тему new, сигналим в лог
        print(f"⚠️ СТОП: в тексте запрещённые формулировки {bad}. "
              f"Статья НЕ опубликована, тема осталась в очереди. Проверь промпт/тему.")
        sys.exit(1)

    content = assemble_md(title, description, body, topic["service"], topic["slug"])
    path = write_article(topic["slug"], content)
    url = f"{SITE_BASE}/{topic['slug']}/"
    update_queue(rows, fieldnames, topic, url)
    ping_indexnow(url)

    print(f"Готово: {path}")
    print(f"URL: {url}")


if __name__ == "__main__":
    main()
