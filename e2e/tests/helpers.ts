import { expect, type Browser, type BrowserContext, type Page } from '@playwright/test'
import { appendFileSync, mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

export const HERE = dirname(fileURLToPath(import.meta.url))
export const SHOTS = join(HERE, '..', 'results', 'shots')
export const MEASURES = join(HERE, '..', 'results', 'measurements.jsonl')
mkdirSync(SHOTS, { recursive: true })

export const ACCOUNTS = {
  metodist: { login: 'metodist', password: 'metod2026' },
  operator: { login: 'operator', password: 'smena2026' },
  master: { login: 'master', password: 'master2026' },
  kim: { login: 'kim.a', password: 'demo2026' },
  abdr: { login: 'abdrakhmanov.r', password: 'demo2026' },
  smag: { login: 'smagulova.zh', password: 'demo2026' },
  petr: { login: 'petrenko.o', password: 'demo2026' },
  nurl: { login: 'nurlanova.a', password: 'demo2026' },
  kas: { login: 'kasymov.d', password: 'demo2026' },
  ivan: { login: 'ivanova.m', password: 'demo2026' },
  tul: { login: 'tulegenov.a', password: 'demo2026' },
}

/** Записывает фактически измеренное значение прогона — из этого файла собирается матрица приёмки. */
export function measure(name: string, data: Record<string, unknown>): void {
  appendFileSync(MEASURES, JSON.stringify({ name, at: new Date().toISOString(), ...data }) + '\n')
}

export async function shot(page: Page, name: string): Promise<void> {
  await page.screenshot({ path: join(SHOTS, `${name}.png`), fullPage: false })
}

/** Ноутбук методиста. */
export async function desktop(browser: Browser): Promise<BrowserContext> {
  return browser.newContext({ viewport: { width: 1366, height: 820 }, locale: 'ru-RU', timezoneId: 'Asia/Almaty', acceptDownloads: true })
}

/** Телефон оператора: узкий экран, касания (эмуляция в Chromium). */
export async function phone(browser: Browser, width = 390): Promise<BrowserContext> {
  return browser.newContext({
    viewport: { width, height: 800 }, screen: { width, height: 800 }, deviceScaleFactor: 2.5, isMobile: true, hasTouch: true,
    userAgent: 'Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36',
    locale: 'ru-RU', timezoneId: 'Asia/Almaty',
  })
}

export async function login(page: Page, who: { login: string; password: string }): Promise<void> {
  await page.goto('/')
  await page.getByLabel('Логин').fill(who.login)
  await page.getByLabel('Пароль').fill(who.password)
  await page.getByRole('button', { name: 'Войти' }).click()
  await expect(page.getByRole('button', { name: 'Войти' })).toHaveCount(0)
}

/** Ждёт, пока оболочка приложения сохранится в кэше Service Worker. */
export async function waitForShell(page: Page): Promise<void> {
  await page.waitForFunction(async () => {
    if (!('serviceWorker' in navigator)) return false
    const reg = await navigator.serviceWorker.getRegistration()
    if (!reg?.active) return false
    return !!(await caches.match('/index.html', { ignoreSearch: true }))
  }, undefined, { timeout: 60_000 })
}

/** Открывает страницу курса с главного экрана — где бы он ни был: в «Ближайшем задании» или в списке ниже. */
export async function openCourse(page: Page, title: string): Promise<void> {
  await expect(page.getByText(/Здравствуйте/)).toBeVisible()
  await expect(page.getByText(title).first()).toBeVisible()
  if (await page.locator('section.hero').getByRole('heading', { level: 1, name: title }).count()) {
    await page.locator('section.hero').getByRole('link', { name: 'Состав курса' }).click()
  } else {
    await page.locator('a.task', { hasText: title }).first().click()
  }
  await expect(page.getByRole('heading', { level: 1, name: title })).toBeVisible()
}

/** Токен текущего пользователя из базы устройства (для проверок через API от его имени). */
export async function deviceToken(page: Page): Promise<string> {
  return page.evaluate(async () => {
    const req = indexedDB.open('dolce-lms-device')
    const db: IDBDatabase = await new Promise((res, rej) => { req.onsuccess = () => res(req.result); req.onerror = () => rej(req.error) })
    const all: { token: string | null }[] = await new Promise((res) => { const r = db.transaction('accounts').objectStore('accounts').getAll(); r.onsuccess = () => res(r.result) })
    db.close()
    return all.find((a) => a.token)?.token ?? ''
  })
}

export async function apiLogin(page: Page, who: { login: string; password: string }): Promise<string> {
  const res = await page.request.post('/api/auth/login', { data: { login: who.login, password: who.password, device_id: 'e2e-api', device_kind: 'pc' } })
  expect(res.ok()).toBeTruthy()
  return (await res.json()).token as string
}

export async function apiGet<T>(page: Page, token: string, path: string): Promise<T> {
  const res = await page.request.get(path, { headers: { Authorization: `Bearer ${token}` } })
  expect(res.ok(), `${path} → ${res.status()}`).toBeTruthy()
  return (await res.json()) as T
}

/** На экране нет горизонтальной прокрутки. */
export async function expectNoHorizontalScroll(page: Page, label: string): Promise<void> {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow, `горизонтальная прокрутка: ${label}`).toBeLessThanOrEqual(1)
}

/** Эмуляция сворачивания приложения: страница получает visibilityState=hidden и событие visibilitychange. */
export async function setHidden(page: Page, hidden: boolean): Promise<void> {
  await page.evaluate((h) => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => (h ? 'hidden' : 'visible') })
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => h })
    document.dispatchEvent(new Event('visibilitychange'))
  }, hidden)
}
