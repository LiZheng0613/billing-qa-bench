import { defineConfig } from '@playwright/test'

/**
 * 「AI 帮我填」主链路回归。
 *
 * 本机已安装 Edge，直接用 `channel: 'msedge'` 驱动，无需下载 Chromium。
 * 用例会真实创建/删除临时用例，需后端（8000）、mock LLM（8001）与前端（5173）在跑。
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:5173',
    channel: 'msedge',
    headless: true,
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
})
