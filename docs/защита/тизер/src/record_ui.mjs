// Запись настоящего интерфейса для тизера: две независимые сессии браузера на отдельном контуре
// «teaser» (LMS_ENV=teaser, порты 8200/8643, база lms_teaser, своя папка данных).
// Демонстрационный контур (8000/8443) и контур автотестов (8100) не затрагиваются.
//
// Запуск:  node record_ui.mjs <рабочая папка>
// Результат: <рабочая папка>/rec/pc/*.jpg, rec/phone/*.jpg — снимки экрана в разрешении устройства
//            (page.screenshot в цикле: CDP screencast и captureScreenshot отдают кадры в CSS-пикселях),
//            rec/log.json — время каждого кадра, движения мыши, касания и отметки шагов.
import { createRequire } from 'node:module'
import { execFileSync, spawn } from 'node:child_process'
import { mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = join(HERE, '..', '..', '..', '..')
const require = createRequire(join(ROOT, 'e2e', 'package.json'))
const { chromium } = require('@playwright/test')

const OUT = join(process.argv[2] ?? '.', 'rec')
const BASE = 'http://localhost:8200'
const PY = join(ROOT, '.venv', 'Scripts', 'python.exe')
const ENV = { ...process.env, LMS_ENV: 'teaser', LMS_HTTP_PORT: '8200', LMS_HTTPS_PORT: '8643', PYTHONIOENCODING: 'utf-8' }
const COURSE = 'Как проверить актуальность СОП перед началом смены'

const log = { pc: { dpr: 4 / 3, frames: [] }, phone: { dpr: 2.625, frames: [] }, events: [] }
const mark = (who, kind, data = {}) => log.events.push({ t: Date.now() / 1000, who, kind, ...data })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function healthy() {
  try { return (await fetch(BASE + '/api/health')).ok } catch { return false }
}

async function screencast(page, who) {
  const dir = join(OUT, who)
  mkdirSync(dir, { recursive: true })
  let n = 0
  let running = true
  const loop = (async () => {
    while (running) {
      const t1 = Date.now()
      const buf = await page.screenshot({ type: 'jpeg', quality: 90, animations: 'allow', caret: 'initial' }).catch(() => null)
      const t2 = Date.now()
      if (!buf) { await sleep(20); continue }
      const name = String(++n).padStart(5, '0') + '.jpg'
      writeFileSync(join(dir, name), buf)
      log[who].frames.push({ f: name, t: (t1 + t2) / 2000 })
    }
  })()
  return async () => { running = false; await loop }
}

// ---------- мышь методиста: плавные движения, каждое положение записывается ----------
let mouse = { x: 1150, y: 260 }
async function moveTo(page, x, y, ms = 650) {
  const steps = Math.max(10, Math.round(ms / 16))
  const x0 = mouse.x, y0 = mouse.y
  for (let i = 1; i <= steps; i++) {
    const k = i / steps
    const e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2
    const x1 = x0 + (x - x0) * e, y1 = y0 + (y - y0) * e
    await page.mouse.move(x1, y1)
    mark('pc', 'mouse', { x: x1, y: y1 })
    await sleep(ms / steps)
  }
  mouse = { x, y }
}
async function click(page, locator, ms = 650) {
  await locator.scrollIntoViewIfNeeded()
  await sleep(150)
  const b = await locator.boundingBox()
  await moveTo(page, b.x + b.width / 2, b.y + b.height / 2, ms)
  mark('pc', 'click', { x: mouse.x, y: mouse.y })
  await page.mouse.down(); await sleep(80); await page.mouse.up()
}
async function typeSlow(page, text, delay = 38) {
  await page.keyboard.type(text, { delay })
}
async function tap(page, locator) {
  await locator.scrollIntoViewIfNeeded()
  await sleep(200)
  const b = await locator.boundingBox()
  mark('phone', 'tap', { x: b.x + b.width / 2, y: b.y + b.height / 2 })
  await locator.tap()
}
async function login(page, user, password) {
  await page.goto(BASE + '/')
  await page.getByLabel('Логин').fill(user)
  await page.getByLabel('Пароль').fill(password)
  await page.getByRole('button', { name: 'Войти' }).click()
  await page.getByRole('button', { name: 'Войти' }).waitFor({ state: 'detached' })
}

async function main() {
  rmSync(OUT, { recursive: true, force: true })
  mkdirSync(OUT, { recursive: true })

  // чистый контур «teaser»: те же демоданные, что получает каждый прогон автотестов
  execFileSync(PY, [join(ROOT, 'run.py'), 'reset-demo'], { env: ENV, stdio: 'inherit' })
  let server = null
  if (!(await healthy())) {
    server = spawn(PY, [join(ROOT, 'run.py')], { env: ENV, stdio: 'ignore' })
    for (let i = 0; i < 180 && !(await healthy()); i++) await sleep(1000)
    if (!(await healthy())) throw new Error('тестовый сервер не поднялся')
  }

  const browser = await chromium.launch()
  try {
    // ---------- ноутбук методиста ----------
    const pc = await browser.newContext({ viewport: { width: 1440, height: 810 }, deviceScaleFactor: 4 / 3, locale: 'ru-RU', timezoneId: 'Asia/Almaty' })
    const m = await pc.newPage()
    await login(m, 'metodist', 'metod2026')
    await m.getByRole('heading', { level: 1, name: 'Обучение смены' }).waitFor()
    await sleep(800)
    const stopPc = await screencast(m, 'pc')
    await m.mouse.move(mouse.x, mouse.y)
    mark('pc', 'overview')
    await sleep(2200)

    await click(m, m.getByRole('link', { name: 'Курсы и назначения' }))
    await m.getByRole('link', { name: 'Редактировать' }).first().waitFor()
    await sleep(700)
    await click(m, m.getByRole('link', { name: 'Редактировать' }).first())
    await m.getByRole('heading', { level: 1, name: COURSE }).waitFor()
    mark('pc', 'editor')
    await sleep(1200)
    await click(m, m.getByRole('button', { name: 'Открытый ответ' }))
    const q = m.getByLabel('Текст вопроса').last()
    await q.waitFor()
    await sleep(500)
    mark('pc', 'typing')
    await typeSlow(m, 'Редакция на рабочем месте не совпала с реестром. Что вы сделаете?')
    await sleep(300)
    await click(m, m.getByLabel('Критерии проверки (видит только методист)').last(), 500)
    await typeSlow(m, '2 балла — не начинает работу и сообщает мастеру смены.', 30)
    await m.locator('.block-card').last().getByText('сохранено').waitFor()
    mark('pc', 'saved')
    await sleep(1000)
    await click(m, m.getByRole('button', { name: 'Опубликовать' }))
    const dialog = m.getByRole('dialog')
    await dialog.getByText(/будет заменена/).waitFor()
    mark('pc', 'publish-dialog')
    await sleep(1500)
    await click(m, dialog.getByRole('button', { name: 'Опубликовать' }), 500)
    const assign = m.getByRole('dialog', { name: 'Назначить курс' })
    await assign.waitFor()
    mark('pc', 'assign-dialog')
    await sleep(900)
    await click(m, assign.getByLabel('Поиск сотрудника'), 500)
    await typeSlow(m, 'Ибраев', 70)
    await sleep(500)
    await click(m, assign.getByText('Ибраев Тимур Асхатович'), 500)
    await sleep(500)
    await click(m, assign.getByRole('button', { name: /Назначить \(1\)/ }), 600)
    await m.getByRole('dialog', { name: 'Курс назначен' }).waitFor()
    mark('pc', 'assigned')
    await sleep(1500)
    await click(m, m.getByRole('button', { name: 'Готово' }), 450)
    await sleep(400)
    await click(m, m.getByRole('link', { name: 'Обучение смены' }), 700)
    await m.getByRole('heading', { level: 1, name: 'Обучение смены' }).waitFor()
    mark('pc', 'overview2')
    await sleep(2500)
    await stopPc()

    // ---------- телефон оператора ----------
    const mobile = await browser.newContext({
      viewport: { width: 412, height: 915 }, screen: { width: 412, height: 915 }, deviceScaleFactor: 2.625, isMobile: true, hasTouch: true,
      userAgent: 'Mozilla/5.0 (Linux; Android 15; 2412DPC0AG) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Mobile Safari/537.36',
      locale: 'ru-RU', timezoneId: 'Asia/Almaty',
    })
    const p = await mobile.newPage()
    await login(p, 'operator', 'smena2026')
    await p.getByText('Ближайшее задание').waitFor()
    await p.waitForFunction(async () => {
      const reg = await navigator.serviceWorker.getRegistration()
      return !!reg?.active && !!(await caches.match('/index.html', { ignoreSearch: true }))
    }, undefined, { timeout: 60_000 })
    await sleep(800)
    const stopPhone = await screencast(p, 'phone')
    mark('phone', 'home')
    await sleep(2200)
    // по локальной сети пакет скачивается меньше чем за секунду; для наглядности сеть ограничена ~1 МБ/с
    const net = await mobile.newCDPSession(p)
    await net.send('Network.emulateNetworkConditions', { offline: false, latency: 20, downloadThroughput: 1_000_000, uploadThroughput: 500_000 })
    await tap(p, p.getByRole('button', { name: 'Скачать для работы без сети' }))
    mark('phone', 'download')
    await p.getByText('Готово без сети').first().waitFor({ timeout: 90_000 })
    mark('phone', 'ready')
    await net.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 })
    await sleep(2200)
    // состав курса: восемь материалов, каждый сохранён на телефоне
    await tap(p, p.locator('section.hero').getByRole('link', { name: 'Состав курса' }))
    await p.getByRole('heading', { level: 1, name: COURSE }).waitFor()
    mark('phone', 'materials')
    await sleep(1500)
    for (let i = 0; i < 40; i++) { await p.mouse.wheel(0, 22); await sleep(45) }
    mark('phone', 'materials-scrolled')
    await sleep(1800)
    await p.goBack()
    await p.getByText('Ближайшее задание').waitFor()
    mark('phone', 'home-again')
    await sleep(1200)
    await mobile.setOffline(true)
    mark('phone', 'offline')
    await p.getByRole('button', { name: /Состояние связи: Нет связи/ }).waitFor({ timeout: 20_000 })
    mark('phone', 'offline-shown')
    await sleep(2500)
    await stopPhone()
    await sleep(300)
  } finally {
    await browser.close()
    writeFileSync(join(OUT, 'log.json'), JSON.stringify(log))
    if (server) server.kill()
  }
  console.log('pc frames', log.pc.frames.length, 'phone frames', log.phone.frames.length)
}

main().catch((e) => { console.error(e); process.exit(1) })
