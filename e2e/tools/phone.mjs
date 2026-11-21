// Диагностика приложения на подключённом по USB телефоне Android (Chrome, отладка по USB включена).
//
// Перед запуском:  adb forward tcp:9222 localabstract:chrome_devtools_remote
// Запуск из папки e2e:
//   node tools/phone.mjs diag                 — Service Worker, кэш оболочки, установка, хранилище
//   node tools/phone.mjs text                 — текст, который сейчас на экране приложения
//   node tools/phone.mjs local                — что лежит в локальной базе: пакеты, очередь, попытки
//   node tools/phone.mjs blocks               — материалы скачанных курсов
//   node tools/phone.mjs nav /a/<id>          — переход внутри приложения без перезагрузки
//   node tools/phone.mjs reload               — перезагрузить приложение (получить новую сборку)
//
// Скрипт подключается напрямую к странице платформы (адрес из LMS_ORIGIN) по протоколу отладки Chrome.
// Остальные вкладки браузера он не открывает и не читает.
const ORIGIN = process.env.LMS_ORIGIN ?? 'https://192.168.137.1:8443'
const cmd = process.argv[2] ?? 'diag'

const targets = await (await fetch('http://localhost:9222/json')).json()
const pages = targets.filter((t) => t.type === 'page' && t.url.startsWith(ORIGIN))
if (!pages.length) {
  console.log(JSON.stringify({ error: `страница ${ORIGIN} на телефоне не открыта` }))
  process.exit(2)
}
const target = pages[0]          // Chrome отдаёт вкладки от последней активной

const ws = new WebSocket(target.webSocketDebuggerUrl)
await new Promise((resolve, reject) => {
  ws.addEventListener('open', resolve, { once: true })
  ws.addEventListener('error', () => reject(new Error('не удалось подключиться к странице на телефоне')), { once: true })
})
let seq = 0
const waiting = new Map()
ws.addEventListener('message', (e) => {
  const msg = JSON.parse(e.data)
  const w = msg.id && waiting.get(msg.id)
  if (!w) return
  waiting.delete(msg.id)
  if (msg.error) w.reject(new Error(msg.error.message))
  else w.resolve(msg.result)
})
function send(method, params = {}, timeoutMs = 20000) {
  const id = ++seq
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { waiting.delete(id); reject(new Error(`${method}: нет ответа за ${timeoutMs / 1000} с`)) }, timeoutMs)
    waiting.set(id, { resolve: (v) => { clearTimeout(timer); resolve(v) }, reject: (e) => { clearTimeout(timer); reject(e) } })
    ws.send(JSON.stringify({ id, method, params }))
  })
}
/** Выполняет функцию на странице и возвращает её результат. */
async function run(fn, ...args) {
  const expression = `(${fn.toString()})(...${JSON.stringify(args)})`
  const res = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
  if (res.exceptionDetails) throw new Error(res.exceptionDetails.exception?.description ?? res.exceptionDetails.text)
  return res.result.value
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const out = (v) => console.log(JSON.stringify(v, null, 1))

try {
  if (cmd === 'reload') {
    await send('Page.reload', { ignoreCache: false })
    await sleep(3500)
    out({ url: await run(() => location.href), build: await run(() => [...document.querySelectorAll('script[src]')].map((s) => s.getAttribute('src')).filter((s) => s.includes('assets/'))[0]) })
  } else if (cmd === 'nav') {
    await run((path) => {
      history.pushState({}, '', path)
      dispatchEvent(new PopStateEvent('popstate'))
    }, process.argv[3] ?? '/')
    await sleep(1500)
    out({ url: await run(() => location.href) })
  } else if (cmd === 'text') {
    out({ url: await run(() => location.href), text: (await run(() => document.body.innerText)).replace(/\n{2,}/g, '\n').slice(0, 4000) })
  } else if (cmd === 'blocks') {
    out(await run(async () => {
      const names = (await indexedDB.databases()).map((d) => d.name).filter((n) => n && n.startsWith('dolce-lms-u-'))
      const result = []
      for (const name of names) {
        const db = await new Promise((res, rej) => { const r = indexedDB.open(name); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error) })
        const get = (store) => new Promise((res) => { const r = db.transaction(store).objectStore(store).getAll(); r.onsuccess = () => res(r.result); r.onerror = () => res([]) })
        const [packages, state] = await Promise.all([get('packages'), get('state')])
        const server = state.find((s) => s.key === 'server')?.value
        for (const p of packages) {
          const a = server?.assignments?.find((x) => x.version_id === p.versionId)
          result.push({ assignment: a?.id, title: p.manifest?.title, blocks: (p.manifest?.blocks ?? []).map((b) => ({ id: b.id, type: b.type, title: b.title })) })
        }
        db.close()
      }
      return result
    }))
  } else if (cmd === 'local') {
    out(await run(async () => {
      const names = (await indexedDB.databases()).map((d) => d.name).filter((n) => n && n.startsWith('dolce-lms-u-'))
      const result = []
      for (const name of names) {
        const db = await new Promise((res, rej) => { const r = indexedDB.open(name); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error) })
        const all = (store) => new Promise((res) => {
          if (!db.objectStoreNames.contains(store)) return res([])
          const r = db.transaction(store).objectStore(store).getAll()
          r.onsuccess = () => res(r.result)
          r.onerror = () => res([])
        })
        const [packages, outbox, attempts, files, rejected] = await Promise.all([all('packages'), all('outbox'), all('attempts'), all('files'), all('rejected')])
        result.push({
          db: name,
          packages: packages.map((p) => ({ title: p.manifest?.title ?? p.pending?.title, revision: p.manifest?.revision ?? p.pending?.revision, status: p.status, doneBytes: p.doneBytes, totalBytes: p.totalBytes, verifiedAt: p.verifiedAt, error: p.error })),
          files: files.length,
          fileBytes: files.reduce((s, f) => s + (f.size ?? 0), 0),
          outbox: outbox.map((e) => ({ type: e.type, tries: e.tries, lastError: e.lastError })),
          attempts: attempts.map((a) => ({ status: a.status, answered: Object.keys(a.answers ?? {}).length, submittedAt: a.submittedAt })),
          rejected: rejected.length,
        })
        db.close()
      }
      return result
    }))
  } else {
    const info = await run(async () => {
      const reg = await navigator.serviceWorker?.getRegistration()
      const cacheNames = await caches.keys()
      let shellEntries = 0
      for (const n of cacheNames) shellEntries += (await (await caches.open(n)).keys()).length
      const est = await navigator.storage?.estimate?.()
      return {
        url: location.href,
        title: document.title,
        standalone: matchMedia('(display-mode: standalone)').matches,
        secure: isSecureContext,
        online: navigator.onLine,
        sw: reg?.active?.state ?? null,
        swControlsPage: !!navigator.serviceWorker?.controller,
        swWaiting: !!reg?.waiting,
        cacheNames,
        shellEntries,
        indexHtmlCached: !!(await caches.match('/index.html', { ignoreSearch: true })),
        backgroundSyncApi: 'SyncManager' in window,
        storagePersisted: await navigator.storage?.persisted?.(),
        usageMb: est ? Math.round((est.usage ?? 0) / 1048576 * 10) / 10 : null,
        quotaMb: est ? Math.round((est.quota ?? 0) / 1048576) : null,
        viewport: { w: innerWidth, h: innerHeight, dpr: devicePixelRatio },
        userAgent: navigator.userAgent,
        build: [...document.querySelectorAll('script[src]')].map((s) => s.getAttribute('src')).filter((s) => s.includes('assets/')).slice(0, 2),
      }
    })
    let installability
    try {
      installability = (await send('Page.getInstallabilityErrors')).installabilityErrors
    } catch (e) {
      installability = 'нет данных: ' + e.message
    }
    out({ pagesOpen: pages.length, ...info, installabilityErrors: installability })
  }
} finally {
  ws.close()
}
