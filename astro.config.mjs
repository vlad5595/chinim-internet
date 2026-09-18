import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';

// site — реальный домен проекта. Нужен для корректных canonical и sitemap.
export default defineConfig({
  site: 'https://chinim-internet.com',
  integrations: [sitemap()],
});
