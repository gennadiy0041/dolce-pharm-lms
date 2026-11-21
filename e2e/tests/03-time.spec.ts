// Учёт времени: 60 секунд изучения + 60 секунд «свёрнуто», две вкладки, явный выход, аварийное закрытие.
// Сворачивание эмулируется событием visibilitychange (см. helpers.setHidden) — на физическом телефоне
// эту же проверку нужно повторить по секундомеру.
import { expect, test, type Page } from '@playwright/test'
import { ACCOUNTS, apiGet, apiLogin, login, measure, openCourse, phone, setHidden, waitForShell } from './helpers'

const HYGIENE = 'Гигиена и переодевание при входе в чистую зону'

interface Sessions {
  sessions: { id: string; full_name: string; end_kind: string; active_seconds: number; material_seconds: number; estimated_intervals: number; login_at: string }[]
  materials: { full_name: string; course_title: string; block_title: string; seconds: number }[]
  totals: { full_name: string; platform_seconds: number; material_seconds: number }[]
}

async function timeOf(page: Page, token: string, userId: string): Promise<Sessions> {
  return apiGet<Sessions>(page, token, `/api/reports/sessions?user_id=${userId}`)
}

async function openHygiene(p: Page): Promise<void> {
  await openCourse(p, HYGIENE)
}

test('время изучения: 60 с материала, затем 60 с свёрнуто → на сервере около 60 с', async ({ browser }) => {
  test.setTimeout(300_000)
  const ctx = await phone(browser)
  const p = await ctx.newPage()
  await login(p, ACCOUNTS.smag)
  await waitForShell(p)
  const token = await apiLogin(p, ACCOUNTS.metodist)
  const users = await apiGet<{ id: string; login: string }[]>(p, token, '/api/users')
  const uid = users.find((u) => u.login === ACCOUNTS.smag.login)!.id

  await openHygiene(p)
  await p.getByRole('button', { name: 'Скачать для работы без сети' }).click()
  await expect(p.getByText('Готово без сети').first()).toBeVisible({ timeout: 60_000 })
  await p.getByRole('link', { name: /Инструкция СОП-ПР-007/ }).click()
  await expect(p.locator('.doc canvas').first()).toBeVisible()
  const started = Date.now()
  // 60 секунд изучаем: листаем документ, как это делает читающий человек
  while (Date.now() - started < 60_000) {
    await p.mouse.wheel(0, 120)
    await p.waitForTimeout(5000)
  }
  const studied = (Date.now() - started) / 1000
  await setHidden(p, true)                       // приложение свёрнуто
  await p.waitForTimeout(60_000)
  await setHidden(p, false)                      // вернулись
  await p.getByRole('link', { name: '← К курсу' }).click()          // уходим с материала — остаток интервала сохраняется
  await expect(p.getByRole('heading', { level: 1, name: HYGIENE })).toBeVisible()

  let seconds = 0
  await expect.poll(async () => {
    const data = await timeOf(p, token, uid)
    seconds = data.materials.find((m) => m.block_title.startsWith('Инструкция СОП-ПР-007'))?.seconds ?? 0
    return seconds
  }, { timeout: 90_000, intervals: [2000] }).toBeGreaterThan(50)
  await p.waitForTimeout(4000)
  const data = await timeOf(p, token, uid)
  seconds = data.materials.find((m) => m.block_title.startsWith('Инструкция СОП-ПР-007'))!.seconds
  measure('time-60-visible-60-hidden', { studied_seconds: Number(studied.toFixed(1)), hidden_seconds: 60, server_material_seconds: seconds,
    mode: 'эмуляция телефона в Chromium, сворачивание — событие visibilitychange' })
  // допуск — один интервал записи (15 с); время в свёрнутом состоянии не засчитано
  expect(seconds).toBeGreaterThanOrEqual(Math.floor(studied) - 3)
  expect(seconds).toBeLessThanOrEqual(Math.ceil(studied) + 15)
  const total = data.totals.find((t) => t.full_name.startsWith('Смагулова'))!
  expect(total.material_seconds).toBeLessThanOrEqual(total.platform_seconds)     // материал входит в общее время
  expect(total.platform_seconds).toBeLessThan(studied + 60)                      // минута «свёрнуто» не прибавилась

  // явный выход фиксируется как выход, а не как оценка
  await p.getByRole('link', { name: 'Профиль' }).click()
  await p.getByRole('button', { name: 'Выйти' }).click()
  await p.getByRole('dialog').getByRole('button', { name: 'Выйти' }).click()
  await expect(p.getByRole('button', { name: 'Войти' })).toBeVisible()
  await expect.poll(async () => (await timeOf(p, token, uid)).sessions.some((s) => s.end_kind === 'logout'), { timeout: 30_000 }).toBe(true)
  await ctx.close()
})

test('две вкладки одновременно не удваивают время; аварийное закрытие даёт оценку по последней записи', async ({ browser }) => {
  test.setTimeout(300_000)
  const ctx = await phone(browser)
  const a = await ctx.newPage()
  await login(a, ACCOUNTS.petr)
  await waitForShell(a)
  const token = await apiLogin(a, ACCOUNTS.metodist)
  const users = await apiGet<{ id: string; login: string }[]>(a, token, '/api/users')
  const uid = users.find((u) => u.login === ACCOUNTS.petr.login)!.id
  await openHygiene(a)
  await a.getByRole('button', { name: 'Скачать для работы без сети' }).click()
  await expect(a.getByText('Готово без сети').first()).toBeVisible({ timeout: 60_000 })
  const courseUrl = a.url()

  // вторая вкладка того же сотрудника открывает тот же материал
  const b = await ctx.newPage()
  await b.goto(courseUrl)
  await a.getByRole('link', { name: /Коротко о главном/ }).click()
  await b.getByRole('link', { name: /Коротко о главном/ }).click()
  const started = Date.now()
  while (Date.now() - started < 20_000) {
    await a.mouse.wheel(0, 50)
    await b.mouse.wheel(0, 50)
    await a.waitForTimeout(2500)
  }
  const both = (Date.now() - started) / 1000
  await a.getByRole('link', { name: '← К курсу' }).click()
  await b.getByRole('link', { name: '← К курсу' }).click()
  let seconds = 0
  await expect.poll(async () => {
    seconds = (await timeOf(a, token, uid)).materials.find((m) => m.block_title === 'Коротко о главном')?.seconds ?? 0
    return seconds
  }, { timeout: 60_000, intervals: [2000] }).toBeGreaterThan(15)
  await a.waitForTimeout(3000)
  seconds = (await timeOf(a, token, uid)).materials.find((m) => m.block_title === 'Коротко о главном')!.seconds
  measure('time-two-tabs', { both_open_seconds: Number(both.toFixed(1)), server_material_seconds: seconds })
  expect(seconds).toBeLessThan(both + 6)                                         // не 2 × 20 секунд
  await b.close()

  // аварийное закрытие: вкладка падает посреди чтения, обработчики закрытия не выполняются
  await a.getByRole('link', { name: /Инструкция СОП-ПР-007/ }).click()
  await expect(a.locator('.doc canvas').first()).toBeVisible()
  await a.mouse.wheel(0, 100)
  await a.waitForTimeout(18_000)                 // успевает сохраниться хотя бы одна запись интервала (шаг 15 с)
  const cdp = await ctx.newCDPSession(a)
  const crashedEvent = a.waitForEvent('crash', { timeout: 15_000 }).catch(() => undefined)
  void cdp.send('Page.crash').catch(() => undefined)
  await crashedEvent
  const c = await ctx.newPage()
  await c.waitForTimeout(38_000)                 // незакрытый интервал считается брошенным через 35 с (два шага записи по 15 с + 5 с запаса)
  await c.goto('/')
  await expect(c.getByText(/Здравствуйте/)).toBeVisible()
  await expect.poll(async () => {
    const d = await timeOf(c, token, uid)
    return d.sessions.reduce((n, s) => n + s.estimated_intervals, 0)
  }, { timeout: 60_000, intervals: [2000] }).toBeGreaterThan(0)
  const d = await timeOf(c, token, uid)
  const crashed = d.sessions.find((s) => s.estimated_intervals > 0)!
  expect(crashed.end_kind).toBe('estimated')                                     // выход помечен как оценка
  const pdfSeconds = d.materials.find((m) => m.block_title.startsWith('Инструкция СОП-ПР-007'))?.seconds ?? 0
  measure('time-crash', { read_before_crash_seconds: 18, server_material_seconds: pdfSeconds, end_kind: crashed.end_kind })
  expect(pdfSeconds).toBeGreaterThanOrEqual(1)
  expect(pdfSeconds).toBeLessThanOrEqual(19)                                     // не больше фактически прочитанного
  await ctx.close()
})
