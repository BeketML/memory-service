import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 120_000,         // 2 min per test — LLM extraction can take ~30s per turn
  globalTimeout: 30 * 60_000, // 30 min total
  globalSetup: './global-setup.ts',
  fullyParallel: false,     // sequential: avoid OpenAI rate-limit collisions
  workers: 1,
  reporter: [
    ['list'],
    ['html', { open: 'never', outputFolder: 'playwright-report' }],
  ],
  use: {
    baseURL: 'http://localhost:8080',
    extraHTTPHeaders: { 'Content-Type': 'application/json' },
  },
});
