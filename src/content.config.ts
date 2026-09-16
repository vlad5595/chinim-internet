import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';

// Коллекция "articles" — каждая статья это .md файл в src/content/articles/.
// Именно сюда конвейер (фаза 3) будет коммитить новые статьи через GitHub API.
const articles = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/articles' }),
  schema: z.object({
    title: z.string(),
    description: z.string(),
    pubDate: z.coerce.date(),
    service: z.string().optional(), // кластер: TikTok, Instagram, ChatGPT и т.д.
    draft: z.boolean().default(false),
  }),
});

export const collections = { articles };
