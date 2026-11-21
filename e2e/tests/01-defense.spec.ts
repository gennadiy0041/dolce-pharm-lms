// Сквозной сценарий защиты на двух независимых сессиях браузера:
// методист на ноутбуке ↔ оператор на телефоне (эмуляция), с отключением сети и повторным открытием приложения.
import { expect, test, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { ACCOUNTS, apiGet, apiLogin, desktop, expectNoHorizontalScroll, login, measure, phone, shot, waitForShell } from './helpers'

const COURSE = 'Как проверить актуальность СОП перед началом смены'
const WORD = 'лосось'

async function orderTexts(page: Page): Promise<string[]> {
  return (await page.locator('.order-item .grow').allTextContents()).map((t) => t.trim())
}

async function arrangeOrder(page: Page, target: string[]): Promise<void> {
  const items = page.locator('.order-item')
  for (let i = 0; i < target.length; i++) {
    for (let guard = 0; guard < 20; guard++) {
      const texts = await orderTexts(page)
      const at = texts.indexOf(target[i])
      expect(at, `вариант «${target[i]}» найден`).toBeGreaterThanOrEqual(0)
      if (at === i) break
      await items.nth(at).getByRole('button', { name: /^Выше/ }).click()
      // ответ сначала записывается на устройство, затем экран перерисовывается
      await expect.poll(() => orderTexts(page)).not.toEqual(texts)
    }
  }
  expect(await orderTexts(page)).toEqual(target)
}

test('защита: назначение с ПК → скачивание → тест без сети → автодоставка → проверка → сертификат и протокол', async ({ browser }) => {
  const pc = await desktop(browser)
  const mobile = await phone(browser)
  const m = await pc.newPage()
  let p = await mobile.newPage()

  // ---------- 0:00 методист добавляет вопрос, публикует и назначает ----------
  await login(m, ACCOUNTS.metodist)
  await m.goto('/m/courses')
  await m.getByRole('link', { name: 'Редактировать' }).first().click()
  await expect(m.getByRole('heading', { level: 1, name: COURSE })).toBeVisible()
  await m.getByRole('button', { name: 'Открытый ответ' }).click()
  const qText = m.getByLabel('Текст вопроса').last()
  await expect(qText).toBeFocused()
  await qText.fill('Напишите контрольное слово, которое назвал проверяющий.')
  await m.getByLabel('Критерии проверки (видит только методист)').last().fill(`2 балла — слово «${WORD}».`)
  await expect(m.locator('.block-card').last().getByText('сохранено')).toBeVisible()
  await shot(m, '01-editor-open-question')
  await m.getByRole('button', { name: 'Опубликовать' }).click()
  const dialog = m.getByRole('dialog')
  await expect(dialog.getByText('Редакция 1 будет заменена')).toBeVisible()
  await shot(m, '02-publish-dialog')
  await dialog.getByRole('button', { name: 'Опубликовать' }).click()
  // после публикации сразу открывается назначение
  const assign = m.getByRole('dialog', { name: 'Назначить курс' })
  await expect(assign).toBeVisible()
  await assign.getByLabel('Поиск сотрудника').fill('Ибраев')
  await assign.getByText('Ибраев Тимур Асхатович').click()
  await shot(m, '03-assign-dialog')
  await assign.getByRole('button', { name: /Назначить \(1\)/ }).click()
  await expect(m.getByRole('dialog', { name: 'Курс назначен' })).toBeVisible()
  await m.getByRole('button', { name: 'Готово' }).click()
  await m.goto('/m/reports')
  await expect(m.getByText('Попыток пока нет')).toBeVisible()

  // ---------- 0:40 оператор скачивает пакет ----------
  await login(p, ACCOUNTS.operator)
  await expect(p.getByRole('heading', { level: 1, name: COURSE })).toBeVisible()
  await expect(p.getByText('Ближайшее задание')).toBeVisible()
  await expect(p.getByText(/Не скачано · \d/)).toBeVisible()
  await expectNoHorizontalScroll(p, 'главный экран, 390 px')
  await shot(p, '04-phone-home-before-download')
  await waitForShell(p)
  const tDownload = Date.now()
  await p.getByRole('button', { name: 'Скачать для работы без сети' }).click()
  await expect(p.getByText('Готово без сети').first()).toBeVisible({ timeout: 90_000 })
  measure('download', { seconds: (Date.now() - tDownload) / 1000, note: 'пакет редакции 2, локальная сеть, эмуляция телефона в Chromium' })
  await shot(p, '05-phone-ready-offline')

  // ---------- 0:55 авиарежим и повторное открытие приложения ----------
  await mobile.setOffline(true)
  await p.close()
  p = await mobile.newPage()
  await p.goto('/')                                  // сети нет: оболочку отдаёт Service Worker
  await expect(p.getByRole('heading', { level: 1, name: COURSE })).toBeVisible()
  await expect(p.getByRole('button', { name: /Состояние связи: Нет связи/ })).toBeVisible({ timeout: 15_000 })
  await shot(p, '06-phone-offline-reopened')

  // ---------- 1:15 материалы без сети: каждый вид блока ----------
  await p.getByRole('button', { name: 'Начать изучение' }).click()
  // текст: название шага — единственный заголовок с этим текстом (такой же заголовок в начале текста не повторяется)
  await expect(p.getByRole('heading', { name: 'Зачем проверять редакцию' })).toHaveCount(1)
  await expect(p.getByText('СОП меняются: уточняют параметры')).toBeVisible()
  await expect(p.getByRole('heading', { level: 3, name: 'Пять шагов' })).toBeVisible()
  await expectNoHorizontalScroll(p, 'урок, текст')
  await p.waitForTimeout(3000)                                                                             // читаем три секунды
  await p.getByRole('button', { name: 'Дальше' }).click()
  await expect(p.getByText(/страниц: 3/)).toBeVisible()                                                    // PDF
  await expect(p.locator('.doc canvas').first()).toBeVisible()
  await shot(p, '07-phone-pdf-offline')
  await p.getByRole('button', { name: 'Дальше' }).click()
  const video = p.locator('video')                                                                         // видео
  await expect(video).toBeVisible()
  await p.waitForFunction(() => (document.querySelector('video')?.duration ?? 0) > 30)
  await video.evaluate(async (v: HTMLVideoElement) => { v.muted = true; await v.play() })
  await p.waitForFunction(() => (document.querySelector('video')?.currentTime ?? 0) > 2)
  await p.getByRole('button', { name: /Вперёд на 10 с/ }).click()
  await p.getByRole('button', { name: /Вперёд на 10 с/ }).click()
  const afterJump = await video.evaluate((v: HTMLVideoElement) => v.currentTime)
  expect(afterJump).toBeGreaterThan(20)
  const sought = await video.evaluate((v: HTMLVideoElement) => new Promise<number>((resolve) => {
    v.addEventListener('seeked', () => resolve(v.currentTime), { once: true })
    v.currentTime = 36
  }))
  expect(Math.abs(sought - 36)).toBeLessThan(1)                                                            // перемотка работает без сети
  await shot(p, '08-phone-video-offline')
  await video.evaluate((v: HTMLVideoElement) => v.pause())
  await p.getByRole('button', { name: 'Дальше' }).click()
  await expect(p.getByText(/Исходный файл Word/)).toBeVisible()                                            // DOCX
  await expect(p.locator('.doc canvas').first()).toBeVisible()
  await p.getByRole('button', { name: 'Дальше' }).click()
  await expect(p.getByText(/Исходный файл Excel/)).toBeVisible()                                           // XLSX
  await expect(p.locator('.doc canvas').first()).toBeVisible()
  await shot(p, '09-phone-xlsx-offline')
  await p.getByRole('button', { name: 'Дальше' }).click()
  await expect(p.getByText(/страниц: 6/)).toBeVisible()                                                    // PPTX: все 6 слайдов
  await expect(p.locator('.doc canvas').first()).toBeVisible()
  await shot(p, '10-phone-pptx-offline')
  await p.getByRole('button', { name: 'Дальше' }).click()
  await expect(p.getByText('Нужен интернет')).toBeVisible()                                                // справочная ссылка
  await p.getByRole('button', { name: 'Дальше' }).click()
  await p.getByRole('button', { name: 'Начать разговор' }).click()                                         // тренажёр
  await p.getByRole('button', { name: /Не запускаю пресс и иду к мастеру смены/ }).click()
  await expect(p.getByText(/встревожен/)).toBeVisible()
  await p.getByRole('button', { name: /Лежит на месте/ }).click()
  await expect(p.getByText(/доволен/)).toBeVisible()
  await p.getByRole('button', { name: /читаю третий раздел до запуска/ }).click()
  await expect(p.getByRole('heading', { name: 'Смена началась по действующей редакции' })).toBeVisible()
  await shot(p, '11-phone-trainer-good-ending')

  // ---------- 1:40 тест без сети; черновик переживает закрытие ----------
  await p.getByRole('button', { name: 'К тесту' }).click()
  await p.getByRole('button', { name: 'Начать тест' }).click()
  await p.getByRole('radio', { name: 'В реестре действующих редакций на посту мастера смены' }).click()
  await p.getByRole('button', { name: 'Дальше' }).click()
  for (const name of ['Код документа', 'Номер редакции', 'Дату введения в действие']) {
    await p.getByRole('checkbox', { name }).click()
  }
  await shot(p, '12-phone-test-multiple')
  await p.getByRole('button', { name: 'Дальше' }).click()
  await expect(p.getByText('Вопрос 3 из 4')).toBeVisible()
  await p.waitForTimeout(400)
  await p.close()                                     // приложение закрыто посреди теста
  p = await mobile.newPage()
  await p.goto('/')
  await p.getByRole('button', { name: 'Продолжить тест' }).click()
  await expect(p.getByText('Вопрос 3 из 4')).toBeVisible()                                                 // вернулись на тот же вопрос
  await p.getByRole('button', { name: 'Назад' }).click()
  await expect(p.getByRole('checkbox', { name: 'Номер редакции' })).toHaveAttribute('aria-checked', 'true')
  await expect(p.getByRole('checkbox', { name: 'Цвет папки' })).toHaveAttribute('aria-checked', 'false')
  await p.getByRole('button', { name: 'Дальше' }).click()
  // последовательность: сначала клавиатурой, затем кнопками
  await expect(p.getByText('Вопрос 3 из 4')).toBeVisible()
  await expect(p.locator('.order-item')).toHaveCount(5)
  const firstItem = p.locator('.order-item').first()
  const before = await orderTexts(p)
  await firstItem.focus()
  await p.keyboard.press('ArrowDown')
  await expect.poll(async () => (await orderTexts(p))[1]).toBe(before[0])
  await expect(p.locator('.order-item').nth(1)).toBeFocused()                                              // фокус остаётся на перемещённой строке
  await arrangeOrder(p, [
    'Найти на титульном листе код и номер редакции', 'Открыть реестр действующих редакций',
    'Сверить код, редакцию и дату, проверить штамп «Контролируемая копия»',
    'Отсканировать QR-код и сверить с электронным реестром', 'Расписаться в журнале начала смены и приступить к работе',
  ])
  await expectNoHorizontalScroll(p, 'тест, последовательность')
  await shot(p, '13-phone-test-order')
  await p.getByRole('button', { name: 'Дальше' }).click()
  await p.getByLabel('Ваш ответ').fill(WORD)
  await p.getByRole('button', { name: 'Проверить и сдать' }).click()
  await expect(p.getByText('Вы ответили на все вопросы')).toBeVisible()
  await p.getByRole('button', { name: 'Сдать тест' }).click()
  await p.getByRole('button', { name: 'Да, сдать' }).click()
  await expect(p.getByRole('heading', { name: 'Ответы сохранены на телефоне' })).toBeVisible()
  await shot(p, '14-phone-answers-saved-offline')

  // на ПК сдачи ещё нет
  await m.waitForTimeout(7000)
  await expect(m.getByText('Попыток пока нет')).toBeVisible()
  await expect(m.locator('tbody tr', { hasText: 'Ибраев Тимур Асхатович' })).toHaveCount(0)
  await shot(m, '15-pc-no-attempt-yet')

  // ---------- 2:10 связь возвращается: методист ничего не нажимает ----------
  const t0 = Date.now()
  await mobile.setOffline(false)
  const row = m.locator('tbody tr', { hasText: 'Ибраев Тимур Асхатович' })
  await expect(row).toBeVisible({ timeout: 60_000 })
  const delay = (Date.now() - t0) / 1000
  measure('autodelivery-open-app', {
    seconds: Number(delay.toFixed(1)), state: 'приложение открыто и активно',
    device: 'эмуляция телефона, Chromium ' + browser.version(), restored: 'context.setOffline(false)',
  })
  expect(delay).toBeLessThan(60)                                                                           // внутренняя цель — до 60 секунд
  await expect(row).toContainText(`«${WORD}»`)
  await expect(row).toContainText('редакция 2')
  await expect(row).toContainText('На проверке')
  await expect(row).toContainText('Телефон')
  await expect(row).toContainText('после обнаружения связи')
  await shot(m, '16-pc-attempt-arrived')
  await expect(p.getByRole('heading', { name: 'Результат получен отделом обучения' })).toBeVisible({ timeout: 30_000 })
  await shot(p, '17-phone-result-received')

  // ---------- 2:40 методист оценивает открытый ответ ----------
  await row.getByRole('link', { name: 'Проверить' }).click()
  await expect(m.getByText(WORD, { exact: true })).toBeVisible()
  await m.locator('section', { hasText: 'Напишите контрольное слово' }).getByRole('button', { name: '2', exact: true }).click()
  await m.getByRole('button', { name: 'Выставить итог' }).click()
  await expect(m.getByText(/Сертификат № DP-\d{4}-\d{5} сформирован/)).toBeVisible()
  await expect(m.getByText('Сдал').first()).toBeVisible()
  await shot(m, '18-pc-reviewed-passed')
  const [certTab] = await Promise.all([m.waitForEvent('popup'), m.getByRole('button', { name: 'Открыть сертификат' }).click()])
  await certTab.close()

  // данные отчёта, сертификат и время изучения — через API
  const token = await apiLogin(m, ACCOUNTS.metodist)
  const report = await apiGet<{ rows: Record<string, any>[] }>(m, token, '/api/reports/attempts')
  const r = report.rows.find((x) => x.full_name === 'Ибраев Тимур Асхатович')!
  expect(r.status).toBe('passed')
  expect(r.revision).toBe(2)
  expect(r.percent).toBe(100)
  expect(r.material_seconds).toBeGreaterThanOrEqual(5)
  expect(r.platform_seconds).toBeGreaterThanOrEqual(r.material_seconds)
  expect(r.source).toBe('phone')
  expect(r.sync_trigger).toBe('online')
  measure('attempt-row', { material_seconds: r.material_seconds, platform_seconds: r.platform_seconds, delay_seconds: r.delay_seconds,
    trigger: r.sync_trigger, percent: r.percent, revision: r.revision })
  const cert = await m.request.get(`/api/files/${r.certificate_file_id}/content`, { headers: { Authorization: `Bearer ${token}` } })
  expect((await cert.body()).subarray(0, 4).toString()).toBe('%PDF')

  // ---------- 3:00 сводный протокол ----------
  await m.goto('/m/protocol')
  await m.getByLabel('Программа').selectOption({ label: COURSE })
  const prow = m.locator('tbody tr', { hasText: 'Ибраев Тимур Асхатович' })
  await expect(prow).toContainText('Аттестован')
  await expect(prow).toContainText('7 из 7')
  await shot(m, '19-pc-protocol')
  const [pdf] = await Promise.all([m.waitForEvent('download'), m.getByRole('button', { name: 'Скачать PDF' }).click()])
  expect(readFileSync((await pdf.path())!).subarray(0, 4).toString()).toBe('%PDF')
  const [csv] = await Promise.all([m.waitForEvent('download'), m.getByRole('button', { name: 'Таблица (CSV)' }).click()])
  const csvText = readFileSync((await csv.path())!)
  expect(csvText.subarray(0, 3)).toEqual(Buffer.from([0xef, 0xbb, 0xbf]))                                  // кириллица в Excel
  expect(csvText.toString('utf8')).toContain('Ибраев Тимур Асхатович')

  // сотрудник видит итог на телефоне: экран теста сам сменился на «Сдал», никуда переходить не нужно
  await expect(p.getByRole('heading', { name: 'Сдал', exact: true })).toBeVisible({ timeout: 40_000 })
  await expect(p.getByRole('button', { name: 'Открыть сертификат' })).toBeVisible()
  await shot(p, '19a-phone-passed-on-test-screen')
  await p.goto('/')
  await expect(p.getByText('Сдал').first()).toBeVisible({ timeout: 40_000 })
  await p.getByRole('link', { name: 'Результаты' }).click()
  await expect(p.getByText(/Аттестован/).first()).toBeVisible()
  await shot(p, '20-phone-passed-certificate')

  await pc.close()
  await mobile.close()
})
