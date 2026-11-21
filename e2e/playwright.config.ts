import { defineConfig } from '@playwright/test'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// Сквозные проверки идут в отдельном контуре «test» (порт 8100): демонстрационная база не затрагивается.
const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const python = join(root, '.venv', 'Scripts', 'python.exe')

export default defineConfig({
  testDir: './tests',
  timeout: 240_000,
  expect: { timeout: 20_000 },
  workers: 1,
  fullyParallel: false,
  retries: 0,
  // Проверка фоновой доставки при закрытом приложении в браузере с Background Sync ждёт повтора
  // до 10 минут. Там, где Background Sync нет (Chromium из Playwright), тест сразу пропускается с причиной.
  reporter: [['list'], ['json', { outputFile: 'results/report.json' }]],
  outputDir: 'results/artifacts',
  globalSetup: './global-setup.ts',
  use: {
    baseURL: 'http://localhost:8100',
    locale: 'ru-RU',
    timezoneId: 'Asia/Almaty',
    serviceWorkers: 'allow',
    actionTimeout: 30_000,
    navigationTimeout: 45_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: `"${python}" "${join(root, 'run.py')}"`,
    url: 'http://localhost:8100/api/health',
    reuseExistingServer: true,
    timeout: 180_000,
    env: { LMS_ENV: 'test', PYTHONIOENCODING: 'utf-8' },
  },
})
