// Роли в интерфейсе, экран 360 px, увеличенный текст, размеры зон нажатия, управление с клавиатуры.
import { expect, test, type Page } from '@playwright/test'
import { ACCOUNTS, desktop, expectNoHorizontalScroll, login, measure, phone, shot, waitForShell } from './helpers'

const MAIN = 'Как проверить актуальность СОП перед началом смены'
const HYGIENE = 'Гигиена и переодевание при входе в чистую зону'

async function smallTargets(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const bad: string[] = []
    const nodes = document.querySelectorAll<HTMLElement>('main button, main a.btn, main a.task, main a.step, .tabbar a, .topbar button, .modal button')
    nodes.forEach((el) => {
      const r = el.getBoundingClientRect()
      if (r.width === 0 || r.height === 0) return
      if (r.height < 47.5 || r.width < 47.5) bad.push(`${(el.textContent ?? '').trim().slice(0, 30)} ${Math.round(r.width)}×${Math.round(r.height)}`)
    })
    return bad
  })
}

test('руководитель видит только своё подразделение, сотрудник — только себя', async ({ browser }) => {
  const ctx = await desktop(browser)
  const p = await ctx.newPage()
  await login(p, ACCOUNTS.master)
  await p.getByRole('link', { name: 'Участок' }).click()
  await expect(p.getByRole('heading', { name: 'Обучение моего подразделения' })).toBeVisible()
  const rows = p.locator('table.report tbody tr')
  await expect(rows.first()).toBeVisible()
  const depts = await rows.locator('td:first-child .cell-sub').allTextContents()
  expect(depts.length).toBeGreaterThan(0)
  expect(depts.every((d) => d.includes('Участок таблетирования'))).toBe(true)
  await shot(p, '25-manager-team-report')
  // кабинет методиста руководителю недоступен
  await p.goto('/m/users')
  await expect(p).toHaveURL(/\/$/)
  for (const path of ['/api/users', '/api/courses', '/api/assessments', '/api/settings', '/api/system/info']) {
    const token = await p.evaluate(async () => {
      const req = indexedDB.open('dolce-lms-device')
      const db: IDBDatabase = await new Promise((res, rej) => { req.onsuccess = () => res(req.result); req.onerror = () => rej(req.error) })
      const all: { token: string }[] = await new Promise((res) => { const r = db.transaction('accounts').objectStore('accounts').getAll(); r.onsuccess = () => res(r.result) })
      return all[0].token
    })
    const res = await p.request.get(path, { headers: { Authorization: `Bearer ${token}` } })
    expect(res.status(), path).toBe(403)
  }
  await ctx.close()

  // руководитель другого участка не видит чужих сотрудников
  const ctx2 = await desktop(browser)
  const q = await ctx2.newPage()
  await login(q, ACCOUNTS.nurl)
  await q.getByRole('link', { name: 'Участок' }).click()
  await expect(q.getByText('Попыток пока нет')).toBeVisible()
  await expect(q.locator('tbody tr', { hasText: 'Ибраев' })).toHaveCount(0)
  await ctx2.close()

  // сотрудник: нет раздела «Участок», в отчёты не попасть
  const ctx3 = await phone(browser)
  const e = await ctx3.newPage()
  await login(e, ACCOUNTS.operator)
  await expect(e.getByRole('link', { name: 'Участок' })).toHaveCount(0)
  await e.goto('/m/reports')
  await expect(e).toHaveURL(/\/$/)
  await e.getByRole('link', { name: 'Результаты' }).click()
  await expect(e.getByText('История аттестаций')).toBeVisible()
  await expect(e.getByText('Ким Алина')).toHaveCount(0)
  await ctx3.close()
})

test('экран 360 px, увеличенный текст и зоны нажатия: без горизонтальной прокрутки', async ({ browser }) => {
  const ctx = await phone(browser, 360)
  const p = await ctx.newPage()
  await login(p, ACCOUNTS.abdr)
  await waitForShell(p)
  const screens: [string, () => Promise<void>][] = [
    ['главный экран', async () => { await p.goto('/'); await expect(p.getByText('Ближайшее задание')).toBeVisible() }],
    ['страница курса', async () => { await p.getByRole('link', { name: 'Состав курса' }).click(); await expect(p.getByRole('heading', { name: 'Материалы' })).toBeVisible() }],
    ['библиотека', async () => { await p.getByRole('link', { name: 'Библиотека' }).click(); await expect(p.locator('article').first()).toBeVisible() }],
    ['результаты', async () => { await p.getByRole('link', { name: 'Результаты' }).click(); await expect(p.getByText('История аттестаций')).toBeVisible() }],
    ['опросы', async () => { await p.getByRole('link', { name: 'Опросы' }).click(); await expect(p.getByRole('heading', { level: 1, name: 'Опросы' })).toBeVisible() }],
    ['профиль', async () => { await p.getByRole('link', { name: 'Профиль' }).click(); await expect(p.getByText('Проверка автономной работы')).toBeVisible() }],
  ]
  const problems: string[] = []
  for (const [name, open] of screens) {
    await open()
    await expectNoHorizontalScroll(p, `${name}, 360 px`)
    problems.push(...(await smallTargets(p)).map((t) => `${name}: ${t}`))
  }
  // основной текст — не меньше 18 px
  const fontPx = await p.evaluate(() => parseFloat(getComputedStyle(document.body).fontSize))
  expect(fontPx).toBeGreaterThanOrEqual(18)
  measure('ui-360', { body_font_px: fontPx, small_targets: problems })
  expect(problems, 'зоны нажатия меньше 48 px').toEqual([])
  await shot(p, '26-phone-360-profile')

  // курс и тест на 360 px
  await p.goto('/')
  const hero = p.getByRole('heading', { level: 1 })
  await expect(hero).toBeVisible()
  await p.getByRole('button', { name: 'Скачать для работы без сети' }).click()
  await expect(p.getByText('Готово без сети').first()).toBeVisible({ timeout: 60_000 })
  await p.getByRole('link', { name: 'Состав курса' }).click()
  await p.getByRole('button', { name: 'Перейти к тесту' }).click()
  await p.getByRole('button', { name: 'Начать тест' }).click()
  for (let i = 0; i < 3; i++) {
    await expectNoHorizontalScroll(p, `тест, вопрос ${i + 1}, 360 px`)
    expect(await smallTargets(p), `тест, вопрос ${i + 1}`).toEqual([])
    await p.getByRole('button', { name: 'Дальше' }).click()
  }
  await shot(p, '27-phone-360-test')

  // увеличенный системный шрифт: всё масштабируется, прокрутки вбок нет
  await p.addStyleTag({ content: 'html { font-size: 160% !important; }' })
  for (const path of ['/', '/library', '/profile']) {
    await p.goto(path)
    await p.addStyleTag({ content: 'html { font-size: 160% !important; }' })
    await p.waitForTimeout(400)
    await expectNoHorizontalScroll(p, `${path}, 360 px, шрифт 160 %`)
  }
  await p.goto('/')
  await p.addStyleTag({ content: 'html { font-size: 160% !important; }' })
  await shot(p, '28-phone-360-large-text')
  await ctx.close()
})

test('управление с клавиатуры: вход, главное действие, вопрос с выбором', async ({ browser }) => {
  const ctx = await desktop(browser)
  const p = await ctx.newPage()
  await p.goto('/')
  await p.getByLabel('Логин').focus()
  await p.keyboard.type(ACCOUNTS.kas.login)
  await p.keyboard.press('Tab')
  await p.keyboard.type(ACCOUNTS.kas.password)
  await p.keyboard.press('Enter')
  await expect(p.getByText('Ближайшее задание')).toBeVisible()
  await waitForShell(p)
  // до главной кнопки можно дойти клавишей Tab, фокус виден
  let reached = false
  for (let i = 0; i < 12 && !reached; i++) {
    await p.keyboard.press('Tab')
    reached = await p.evaluate(() => (document.activeElement?.textContent ?? '').includes('Скачать'))
  }
  expect(reached).toBe(true)
  const outline = await p.evaluate(() => getComputedStyle(document.activeElement!).outlineStyle)
  expect(outline).not.toBe('none')
  await p.keyboard.press('Enter')
  await expect(p.getByText('Готово без сети').first()).toBeVisible({ timeout: 60_000 })
  await p.goto('/')
  await p.getByRole('link', { name: 'Состав курса' }).click()
  await p.getByRole('button', { name: 'Перейти к тесту' }).focus()
  await p.keyboard.press('Enter')
  await p.getByRole('button', { name: 'Начать тест' }).focus()
  await p.keyboard.press('Enter')
  const option = p.getByRole('radio', { name: 'Не меньше 30 секунд' })
  await option.focus()
  await p.keyboard.press('Space')
  await expect(option).toHaveAttribute('aria-checked', 'true')
  await ctx.close()
})
