// Фоновая отправка из Service Worker.
//
// Тест 1 проверяет сам код фоновой отправки: страница отправлять не может (её запросы блокируются),
// очередь уходит из Service Worker — он сам читает IndexedDB, берёт токен и получает подтверждение.
//
// Тест 2 проверяет то, что зависит уже от браузера: разбудит ли он Service Worker после возврата
// связи, когда приложение закрыто (Background Sync). В Chromium, которым управляет Playwright,
// Background Sync отключён, поэтому там тест пропускается с объяснением. На физическом телефоне
// эту проверку нужно пройти руками — см. docs/ТЕЛЕФОН.md, раздел 7.
import { expect, request, test, type BrowserContext, type Page } from '@playwright/test'
import { ACCOUNTS, login, measure, openCourse, phone, waitForShell } from './helpers'

const HYGIENE = 'Гигиена и переодевание при входе в чистую зону'

async function submitOffline(ctx: BrowserContext, p: Page): Promise<void> {
  await openCourse(p, HYGIENE)
  await p.getByRole('button', { name: 'Скачать для работы без сети' }).click()
  await expect(p.getByText('Готово без сети').first()).toBeVisible({ timeout: 60_000 })
  await ctx.setOffline(true)
  await p.getByRole('button', { name: 'Перейти к тесту' }).click()
  await p.getByRole('button', { name: 'Начать тест' }).click()
  await p.getByRole('radio', { name: 'Не меньше 30 секунд' }).click()
  await p.getByRole('button', { name: 'Дальше' }).click()
  for (const name of ['Часы', 'Кольца и браслеты', 'Пропуск на шнурке поверх одежды']) await p.getByRole('checkbox', { name }).click()
  await p.getByRole('button', { name: 'Дальше' }).click()
  await p.getByRole('button', { name: 'Дальше' }).click()
  await p.getByLabel('Ваш ответ').fill('Не войду в чистую зону, заменю комбинезон, сообщу мастеру')
  await p.getByRole('button', { name: 'Проверить и сдать' }).click()
  await p.getByRole('button', { name: 'Сдать тест' }).click()
  await p.getByRole('button', { name: 'Да, сдать' }).click()
  await expect(p.getByRole('heading', { name: 'Ответы сохранены на телефоне' })).toBeVisible()
}

async function serverSide(baseURL: string | undefined) {
  const api = await request.newContext({ baseURL })
  const auth = await api.post('/api/auth/login', { data: { login: 'metodist', password: 'metod2026', device_id: 'e2e-bg', device_kind: 'pc' } })
  const headers = { Authorization: `Bearer ${(await auth.json()).token}` }
  const find = async (fullName: string) => {
    const res = await api.get('/api/reports/attempts', { headers })
    const rows = (await res.json()).rows as Record<string, any>[]
    return rows.find((r) => r.full_name === fullName && r.course_title === HYGIENE)
  }
  return { api, find }
}

test('Service Worker сам отправляет очередь: страница не участвует', async ({ browser, baseURL }) => {
  const ctx = await phone(browser)
  const p = await ctx.newPage()
  await login(p, ACCOUNTS.tul)
  await waitForShell(p)
  await submitOffline(ctx, p)
  const { api, find } = await serverSide(baseURL)
  expect(await find('Тулегенов Арман Кайратович')).toBeUndefined()

  // Запросы страницы на отправку обрываются; запросы, которые делает Service Worker, проходят.
  let pageAttempts = 0
  let workerRequests = 0
  await ctx.route('**/api/sync/events', async (route) => {
    if (route.request().serviceWorker()) {
      workerRequests += 1
      await route.continue()
      return
    }
    pageAttempts += 1
    await route.abort('internetdisconnected')
  })
  await ctx.setOffline(false)
  let row: Record<string, any> | undefined
  const replies: string[] = []
  await expect.poll(async () => {
    if (replies.length) console.log('ответ Service Worker:', replies[replies.length - 1])
    // просьба повторяется: если в этот миг очередь держит страница, Service Worker уступает и ждёт следующего раза
    const reply = await p.evaluate(() => new Promise<string>((resolve) => {
      const onMessage = (e: MessageEvent) => {
        if (e.data?.type !== 'sync-result') return
        navigator.serviceWorker.removeEventListener('message', onMessage)
        resolve(JSON.stringify(e.data.result))
      }
      navigator.serviceWorker.addEventListener('message', onMessage)
      void navigator.serviceWorker.ready.then((reg) => reg.active!.postMessage({ type: 'sync-now' }))
      setTimeout(() => resolve('нет ответа от Service Worker'), 6000)
    }))
    replies.push(reply)
    return (row = await find('Тулегенов Арман Кайратович'))
  }, { timeout: 30_000, intervals: [500], message: 'Service Worker не доставил очередь' }).toBeTruthy()
  expect(row!.sync_trigger).toBe('background-sync')                  // отправил именно Service Worker
  expect(row!.status).toBe('review')
  // очередь на устройстве очищена, экран обновился по сообщению от Service Worker
  await expect(p.getByRole('heading', { name: 'Результат получен отделом обучения' })).toBeVisible({ timeout: 30_000 })
  const left = await p.evaluate(async () => {
    const names = (await indexedDB.databases()).map((d) => d.name!).filter((n) => n.startsWith('dolce-lms-u-'))
    let attempts = 0
    for (const name of names) {
      const db: IDBDatabase = await new Promise((res, rej) => { const r = indexedDB.open(name); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error) })
      const all: { type: string }[] = await new Promise((res) => { const r = db.transaction('outbox').objectStore('outbox').getAll(); r.onsuccess = () => res(r.result) })
      attempts += all.filter((e) => e.type === 'attempt.submit').length
      db.close()
    }
    return attempts
  })
  expect(left).toBe(0)
  expect(workerRequests).toBeGreaterThan(0)
  measure('service-worker-send', { delivered: true, trigger: row!.sync_trigger, app_state: row!.sync_app_state,
    page_requests_blocked: pageAttempts, worker_requests: workerRequests, replies: replies.slice(-2) })
  await api.dispose()
  await ctx.close()
})

test('закрытое приложение: ответы уходят фоновой задачей Service Worker', async ({ browser, baseURL }) => {
  test.setTimeout(12 * 60_000)
  const ctx = await phone(browser)
  const p = await ctx.newPage()
  await login(p, ACCOUNTS.ivan)
  await waitForShell(p)
  const support = await p.evaluate(async () => {
    const reg = await navigator.serviceWorker.ready
    if (!('sync' in reg)) return 'нет API'
    try {
      await (reg as ServiceWorkerRegistration & { sync: { register(tag: string): Promise<void> } }).sync.register('lms-probe')
      return 'ok'
    } catch (e) {
      return (e as Error).message
    }
  })
  if (support !== 'ok') {
    measure('background-sync-closed-app', { delivered: null, skipped: true, reason: support, browser: 'Chromium ' + browser.version() + ' (Playwright)' })
  }
  test.skip(support !== 'ok', `Background Sync в этом браузере недоступен: ${support}`)

  await submitOffline(ctx, p)
  await p.waitForTimeout(1500)
  for (const page of ctx.pages()) await page.close()                 // приложение закрыто: ни одного окна
  const { api, find } = await serverSide(baseURL)
  expect(await find('Иванова Марина Петровна')).toBeUndefined()
  const restored = Date.now()
  await ctx.setOffline(false)                                         // связь вернулась, приложение не открываем
  let row: Record<string, any> | undefined
  const deadline = Date.now() + 9 * 60_000
  while (Date.now() < deadline && !row) {
    row = await find('Иванова Марина Петровна')
    if (!row) await new Promise((r) => setTimeout(r, 5000))
  }
  measure('background-sync-closed-app', {
    delivered: !!row, seconds: row ? Math.round((Date.now() - restored) / 1000) : null, trigger: row?.sync_trigger ?? null,
    app_state: row?.sync_app_state ?? null, browser: 'Chromium ' + browser.version(), restored: 'context.setOffline(false), окна закрыты',
  })
  expect(row, 'попытка доставлена без открытия приложения').toBeTruthy()
  expect(row!.sync_trigger).toBe('background-sync')
  expect(row!.sync_app_state).toBe('closed')
  await api.dispose()
  await ctx.close()
})
