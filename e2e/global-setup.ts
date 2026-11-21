import { execFileSync } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// Перед прогоном тестовый контур очищается и заново получает демоданные:
// результаты прогона появляются только из действий самих тестов.
export default function globalSetup(): void {
  const here = dirname(fileURLToPath(import.meta.url))
  const root = join(here, '..')
  mkdirSync(join(here, 'results', 'shots'), { recursive: true })
  execFileSync(join(root, '.venv', 'Scripts', 'python.exe'), [join(root, 'run.py'), 'reset-demo'], {
    env: { ...process.env, LMS_ENV: 'test', PYTHONIOENCODING: 'utf-8' },
    stdio: 'inherit',
  })
}
