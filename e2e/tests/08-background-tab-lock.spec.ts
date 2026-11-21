// Вкладка браузера в фоне не должна блокировать установленное приложение.
//
// На телефоне Android 21.11.2026 приложение зависло на «Загружаем…», пока в Chrome оставалась вкладка
// с той же платформой. Механизм: каждое изменение локальной базы рассылается всем копиям платформы
// (Dexie, BroadcastChannel), и фоновая вкладка в ответ перечитывает базу. Android замораживает её
// процесс посреди такого чтения, и незавершённая транзакция держит хранилище — запись в приложении
// встаёт в очередь навсегда.
//
// Здесь заморозка воспроизводится точно: на скрытой вкладке ставится точка останова на событие
// «success» запроса IndexedDB. Если вкладка обратится к базе, её выполнение остановится посреди
// транзакции — ровно как при заморозке процесса.
import { expect, test, type CDPSession } from '@playwright/test'
import { ACCOUNTS, apiGet, apiLogin, login, measure, phone, setHidden, shot, waitForShell } from './helpers'

test('вкладка в фоне не трогает базу: приложение скачивает курс, пока вкладка «заморожена»', async ({ browser }) => {
  test.setTimeout(180_000)
  const ctx = await phone(browser)
  const tab = await ctx.newPage()              // вкладка Chrome, через которую ставили приложение
  await login(tab, ACCOUNTS.tul)
  await waitForShell(tab)
  await expect(tab.getByText(/Здравствуйте/)).toBeVisible()

  const app = await ctx.newPage()              // установленное приложение
  await app.goto('/')
  await expect(app.getByText(/Здравствуйте/)).toBeVisible()
  const download = app.getByRole('button', { name: /Скачать для работы без сети/ }).first()
  await expect(download).toBeVisible()

  // вкладка уходит в фон
  await setHidden(tab, true)
  await tab.waitForTimeout(1500)

  // «заморозка»: любое обращение вкладки к IndexedDB останавливает её посреди транзакции
  const cdp: CDPSession = await ctx.newCDPSession(tab)
  let paused = 0
  cdp.on('Debugger.paused', () => { paused += 1 })
  await cdp.send('Debugger.enable')
  await cdp.send('DOMDebugger.setEventListenerBreakpoint', { eventName: 'success' })

  const started = Date.now()
  await download.click()
  let ready = true
  try {
    await expect(app.getByText('Готово без сети').first()).toBeVisible({ timeout: 45_000 })
  } catch {
    ready = false
  }
  const seconds = Number(((Date.now() - started) / 1000).toFixed(1))
  await shot(app, '40-app-download-while-tab-frozen')
  measure('background-tab-lock', { tab_idb_pauses: paused, app_download_ready: ready, seconds })

  // снимаем «заморозку», вкладка возвращается на экран и видит свежие данные
  await cdp.send('DOMDebugger.removeEventListenerBreakpoint', { eventName: 'success' }).catch(() => undefined)
  await cdp.send('Debugger.resume').catch(() => undefined)
  await cdp.send('Debugger.disable').catch(() => undefined)

  expect(paused, 'скрытая вкладка обращалась к локальной базе').toBe(0)
  expect(ready, 'приложение не смогло скачать курс, пока вкладка была «заморожена»').toBe(true)

  await setHidden(tab, false)
  await expect(tab.getByText('Готово без сети').first()).toBeVisible({ timeout: 20_000 })
  await ctx.close()
})

test('приложение свернули посреди загрузки: в фоне база не трогается, при возврате загрузка продолжается сама', async ({ browser }) => {
  test.setTimeout(180_000)
  // отдельный сотрудник, которому курс назначается здесь же: результат не зависит от других сценариев
  const who = { login: 'li.s', password: 'demo2026' }
  const ctx = await phone(browser)
  // Счётчик транзакций IndexedDB, начатых страницей, пока она скрыта
  await ctx.addInitScript(() => {
    const w = window as unknown as { __idbHidden: number }
    w.__idbHidden = 0
    const original = IDBDatabase.prototype.transaction
    IDBDatabase.prototype.transaction = function (this: IDBDatabase, ...args: Parameters<IDBDatabase['transaction']>) {
      if (document.visibilityState === 'hidden') w.__idbHidden += 1
      return original.apply(this, args)
    } as IDBDatabase['transaction']
  })
  const app = await ctx.newPage()
  const token = await apiLogin(app, ACCOUNTS.metodist)
  const courses = await apiGet<{ title: string; versions: { id: string; status: string }[] }[]>(app, token, '/api/courses')
  const hygiene = courses.find((c) => c.title.startsWith('Гигиена'))!
  const version = hygiene.versions.find((v) => v.status === 'published')!
  const users = await apiGet<{ id: string; login: string }[]>(app, token, '/api/users')
  const li = users.find((u) => u.login === who.login)!
  const assigned = await app.request.post('/api/assignments', {
    headers: { Authorization: `Bearer ${token}` }, data: { version_id: version.id, user_ids: [li.id] },
  })
  expect(assigned.ok()).toBeTruthy()

  await login(app, who)
  await waitForShell(app)
  const download = app.getByRole('button', { name: /Скачать для работы без сети/ }).first()
  await expect(download).toBeVisible()

  // файл курса отдаётся медленно — успеваем «свернуть» приложение посреди загрузки
  await ctx.route('**/api/files/*/content', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 3000))
    await route.continue().catch(() => undefined)
  })
  await download.click()
  await expect(app.getByText('Скачивается').first()).toBeVisible()
  await app.waitForTimeout(800)
  await setHidden(app, true)
  await expect.poll(() => app.evaluate(() => (window as unknown as { __lmsLifecycle: { stats: { suspended: number } } }).__lmsLifecycle.stats.suspended))
    .toBeGreaterThan(0)
  const before = await app.evaluate(() => (window as unknown as { __idbHidden: number }).__idbHidden)
  await app.waitForTimeout(5000)                // в это время ответ сервера приходит, но страница в фоне
  const after = await app.evaluate(() => (window as unknown as { __idbHidden: number }).__idbHidden)
  expect(after - before, 'страница в фоне обращалась к локальной базе после ухода в фон').toBe(0)

  // возврат на экран: загрузка продолжается сама, без нажатий
  await ctx.unroute('**/api/files/*/content')
  const resumed = Date.now()
  await setHidden(app, false)
  await expect(app.getByText('Готово без сети').first()).toBeVisible({ timeout: 30_000 })
  measure('download-paused-in-background', {
    idb_transactions_while_hidden: after - before, resumed_to_ready_seconds: Number(((Date.now() - resumed) / 1000).toFixed(1)),
  })
  await shot(app, '41-download-resumed-after-background')
  await ctx.close()
})
