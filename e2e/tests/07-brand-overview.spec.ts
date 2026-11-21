// Сборка 2 (фирменный стиль): сводка методиста «Обучение смены», путь задания на главном экране
// оператора, вопрос на последовательность на узком экране, логотип компании.
import { expect, test } from '@playwright/test'
import { ACCOUNTS, apiGet, apiLogin, desktop, expectNoHorizontalScroll, login, measure, openCourse, phone, shot, waitForShell } from './helpers'

const COURSE = 'Как проверить актуальность СОП перед началом смены'
const HYGIENE = 'Гигиена и переодевание при входе в чистую зону'

interface Version { id: string; status: string; revision: number }
interface Course { id: string; title: string; versions: Version[] }
interface Assignment { state: string }

test('сводка методиста: ход обучения по действующей редакции, протокол в одно нажатие', async ({ browser }) => {
  const pc = await desktop(browser)
  const m = await pc.newPage()
  await login(m, ACCOUNTS.metodist)
  // после входа методист попадает на сводку, а не на таблицу попыток
  await expect(m.getByRole('heading', { level: 1, name: 'Обучение смены' })).toBeVisible()
  await expect(m).toHaveURL(/\/m\/overview$/)

  const card = m.locator('section.ov-card', { has: m.getByRole('heading', { level: 2, name: COURSE }) })
  await expect(card).toBeVisible()
  await expect(card.locator('.ov-step-head')).toHaveText([/Инструкция/, /Обучение/, /Протокол/])

  // числа на карточке совпадают с назначениями на сервере
  const token = await apiLogin(m, ACCOUNTS.metodist)
  const courses = await apiGet<Course[]>(m, token, '/api/courses')
  const course = courses.find((c) => c.versions.some((v) => v.status === 'published' && c.title === COURSE))
    ?? courses.find((c) => c.title === COURSE)!
  const published = course.versions.find((v) => v.status === 'published')!
  const assignments = await apiGet<Assignment[]>(m, token, `/api/assignments?version_id=${published.id}`)
  const passed = assignments.filter((a) => a.state === 'passed').length
  expect(assignments.length).toBeGreaterThan(0)
  await expect(card.locator('.ov-big')).toHaveText(`${passed} из ${assignments.length}`)
  await expect(card.getByText(`Редакция ${published.revision}`, { exact: true })).toBeVisible()
  const legendTotal = await card.locator('.legend b').allTextContents()
  expect(legendTotal.map(Number).reduce((a, b) => a + b, 0)).toBe(assignments.length)

  // протокол открывается прямо с карточки: тот же PDF, что на странице протокола
  const [resp] = await Promise.all([
    m.waitForResponse((r) => r.url().includes('/api/protocol.pdf')),
    card.getByRole('button', { name: 'Протокол (PDF)' }).click(),
  ])
  expect(resp.status()).toBe(200)
  expect(resp.headers()['content-type']).toContain('application/pdf')
  expect((await resp.body()).subarray(0, 5).toString()).toBe('%PDF-')

  // список назначений и назначение доступны с карточки
  await card.getByRole('button', { name: 'Кому назначено' }).click()
  const list = m.getByRole('dialog', { name: /Назначения · редакция/ })
  await expect(list.locator('tbody tr')).toHaveCount(assignments.length)
  await list.getByRole('button', { name: 'Закрыть' }).click()
  await card.getByRole('button', { name: 'Назначить' }).click()
  await expect(m.getByRole('dialog', { name: 'Назначить курс' })).toBeVisible()
  await m.getByRole('dialog', { name: 'Назначить курс' }).getByRole('button', { name: 'Отмена' }).click()

  // лента результатов: попытки предыдущих сценариев, у телефона указан способ доставки
  await expect(m.getByRole('heading', { level: 2, name: 'Последние результаты' })).toBeVisible()
  const report = await apiGet<{ rows: unknown[] }>(m, token, '/api/reports/attempts')
  if (report.rows.length) await expect(m.locator('table.report tbody tr')).toHaveCount(Math.min(6, report.rows.length))
  else await expect(m.getByText('Результатов пока нет')).toBeVisible()
  await expectNoHorizontalScroll(m, 'сводка методиста, 1366 px')
  // логотип компании загружен с адреса платформы
  const logo = m.locator('.side .brand img')
  expect(await logo.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true)
  await shot(m, '36-overview')
  measure('overview', { course: COURSE, revision: published.revision, assigned: assignments.length, passed })

  // меню ведёт на сводку и обратно в отчёт
  await m.getByRole('link', { name: 'Отчёт по обучению' }).click()
  await expect(m.getByRole('heading', { level: 1, name: 'Отчёт по обучению' })).toBeVisible()
  await m.getByRole('link', { name: 'Обучение смены' }).click()
  await expect(m.getByRole('heading', { level: 1, name: 'Обучение смены' })).toBeVisible()
  await pc.close()
})

test('телефон 360 px: путь задания, логотип в шапке, вопрос на последовательность без разрыва слов', async ({ browser }) => {
  const ctx = await phone(browser, 360)
  const p = await ctx.newPage()
  await login(p, ACCOUNTS.nurl)
  await expect(p.getByText('Ближайшее задание')).toBeVisible()
  await waitForShell(p)

  // путь задания: четыре шага, текущий — «Скачать»
  const steps = p.locator('ol.journey li')
  await expect(steps).toHaveCount(4)
  await expect(p.locator('ol.journey li[aria-current="step"]')).toHaveText(/Скачать/)
  const logo = p.locator('.topbar .brand img')
  await expect(logo).toBeVisible()
  expect(await logo.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true)
  // логотип и статус отправки помещаются в шапке целиком
  const chip = p.locator('.topbar .syncchip')
  const [logoBox, chipBox] = [await logo.boundingBox(), await chip.boundingBox()]
  expect(logoBox!.x).toBeGreaterThanOrEqual(0)
  expect(logoBox!.x + logoBox!.width).toBeLessThanOrEqual(chipBox!.x)
  expect(chipBox!.x + chipBox!.width).toBeLessThanOrEqual(360)
  await expectNoHorizontalScroll(p, 'главный экран с путём задания, 360 px')
  await shot(p, '37-phone-360-journey-download')

  // после скачивания текущим становится «Изучить», «Скачать» отмечен выполненным
  await p.getByRole('button', { name: 'Скачать для работы без сети' }).click()
  await expect(p.getByText('Готово без сети').first()).toBeVisible({ timeout: 60_000 })
  await expect(p.locator('ol.journey li[aria-current="step"]')).toHaveText(/Изучить/)
  await expect(p.locator('ol.journey li.done')).toHaveCount(1)
  await shot(p, '38-phone-360-journey-learn')

  // вопрос на последовательность (курс по гигиене): текст шага во всю ширину карточки, кнопки — под ним в ряд
  await openCourse(p, HYGIENE)
  const download = p.getByRole('button', { name: 'Скачать для работы без сети' })
  if (await download.count()) await download.click()          // если ближайшим был другой курс, этот ещё не скачан
  await expect(p.getByText('Готово без сети').first()).toBeVisible({ timeout: 60_000 })
  await p.getByRole('button', { name: 'Перейти к тесту' }).click()
  await p.getByRole('button', { name: 'Начать тест' }).click()
  // вопрос на последовательность — третий; каждый переход ждёт появления следующего вопроса
  await expect(p.getByText('Вопрос 1 из 4')).toBeVisible()
  await p.getByRole('button', { name: 'Дальше' }).click()
  await expect(p.getByText('Вопрос 2 из 4')).toBeVisible()
  await p.getByRole('button', { name: 'Дальше' }).click()
  await expect(p.getByText('Вопрос 3 из 4')).toBeVisible()
  const item = p.locator('.order-item').first()
  await expect(item).toBeVisible()
  const geometry = await p.locator('.order-item').evaluateAll((items) => items.map((el) => {
    const text = el.querySelector('.grow')!.getBoundingClientRect()
    const btns = el.querySelector('.order-btns')!.getBoundingClientRect()
    const box = el.getBoundingClientRect()
    return { textShare: text.width / box.width, below: btns.top >= text.bottom - 1, btnHeights: [...el.querySelectorAll('.order-btns button')].map((b) => b.getBoundingClientRect().height) }
  }))
  for (const g of geometry) {
    expect(g.below, 'кнопки «Выше/Ниже» стоят под текстом шага').toBe(true)
    expect(g.textShare, 'текст шага занимает почти всю ширину карточки').toBeGreaterThan(0.7)
    g.btnHeights.forEach((h) => expect(h).toBeGreaterThanOrEqual(48))
  }
  // перестановка по-прежнему работает кнопкой
  const before = (await p.locator('.order-item .grow').allTextContents()).map((t) => t.trim())
  await p.locator('.order-item').nth(1).getByRole('button', { name: /^Выше/ }).click()
  const after = (await p.locator('.order-item .grow').allTextContents()).map((t) => t.trim())
  expect(after[0]).toBe(before[1])
  expect(after[1]).toBe(before[0])
  await expectNoHorizontalScroll(p, 'вопрос на последовательность, 360 px')
  await shot(p, '39-phone-360-order')
  measure('order-360', { text_share: geometry.map((g) => Math.round(g.textShare * 100) / 100) })
  await ctx.close()
})
