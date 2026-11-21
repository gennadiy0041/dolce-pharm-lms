// Кабинет методиста через интерфейс: новый курс с нуля, импорт сотрудников из CSV, оценка 360° с пересчётом.
import { expect, request, test } from '@playwright/test'
import { join } from 'node:path'
import { ACCOUNTS, HERE, desktop, login, phone, shot } from './helpers'

const ROOT = join(HERE, '..', '..')
const MATERIALS = join(ROOT, 'backend', 'demo', 'materials')

test('новый курс без SCORM: создать, наполнить, опубликовать, назначить — курс виден сотруднику', async ({ browser }) => {
  const pc = await desktop(browser)
  const m = await pc.newPage()
  await login(m, ACCOUNTS.metodist)
  await m.goto('/m/courses')
  await m.getByRole('button', { name: 'Новый курс' }).click()
  await m.getByLabel('Название').fill('Как заполнять журнал приёма-передачи смены')
  await m.getByLabel('Код СОП').fill('СОП-ПР-014')
  await m.getByRole('button', { name: 'Создать черновик' }).click()
  await expect(m.getByRole('heading', { level: 1, name: 'Как заполнять журнал приёма-передачи смены' })).toBeVisible()
  await expect(m.getByText('Нужно исправить')).toBeVisible()                    // пустой курс публиковать нельзя

  // текст
  await m.getByRole('button', { name: 'Текст', exact: true }).click()
  await m.getByLabel('Текст', { exact: true }).fill('# Зачем нужен журнал\n\nЗапись делается сразу, разборчиво и без исправлений.\n\n- дата и смена\n- кто сдал и кто принял')
  // Word: исходник + читаемая копия
  const [chooser] = await Promise.all([m.waitForEvent('filechooser'), m.getByRole('button', { name: 'Word', exact: true }).click()])
  await chooser.setFiles(join(MATERIALS, 'zhurnal-smeny.docx'))
  await expect(m.getByText('Копия для чтения без сети готова')).toBeVisible({ timeout: 60_000 })
  // файл неподходящего формата отклоняется с понятной причиной
  const [bad] = await Promise.all([m.waitForEvent('filechooser'), m.getByRole('button', { name: 'PDF', exact: true }).click()])
  await bad.setFiles({ name: 'не-pdf.pdf', mimeType: 'application/pdf', buffer: Buffer.from('это не PDF') })
  await expect(m.getByText(/Файл не похож на PDF/)).toBeVisible()

  // вопрос с одним вариантом
  await m.getByRole('button', { name: 'Один вариант' }).click()
  await m.getByLabel('Текст вопроса').last().fill('Когда делается запись в журнале?')
  const q = m.locator('.block-card', { hasText: 'Вопрос 1' })
  await q.getByLabel('Текст варианта 1').fill('Сразу при передаче смены')
  await q.getByLabel('Текст варианта 2').fill('В конце недели')
  await q.getByLabel('Верный вариант 1').check()
  await expect(q.getByText('сохранено')).toBeVisible()
  await m.getByRole('button', { name: 'Проверить ещё раз' }).click()
  await expect(m.getByText('Можно публиковать')).toBeVisible()
  await shot(m, '29-editor-new-course')

  // глазами сотрудника — до публикации
  await m.getByRole('button', { name: 'Глазами сотрудника' }).click()
  await expect(m.getByRole('heading', { level: 2, name: 'Зачем нужен журнал' })).toBeVisible()
  await m.getByRole('button', { name: 'Дальше' }).click()
  await expect(m.locator('.doc canvas').first()).toBeVisible({ timeout: 30_000 })
  await shot(m, '30-preview-as-employee')
  await m.getByRole('link', { name: '← К редактору' }).click()

  await m.getByRole('button', { name: 'Опубликовать' }).click()
  await m.getByRole('dialog').getByRole('button', { name: 'Опубликовать' }).click()
  const assign = m.getByRole('dialog', { name: 'Назначить курс' })
  await assign.getByRole('checkbox', { name: 'Участок фасовки и упаковки', exact: true }).check()
  await assign.getByRole('button', { name: /^Назначить \(4\)/ }).click()
  await expect(m.getByRole('dialog', { name: 'Курс назначен' })).toBeVisible()
  await m.getByRole('button', { name: 'Готово' }).click()
  // контроль назначений
  await m.locator('section', { hasText: 'Как заполнять журнал' }).getByRole('button', { name: 'Кому назначено' }).click()
  await expect(m.getByRole('dialog').locator('tbody tr')).toHaveCount(4)
  await expect(m.getByRole('dialog').getByText('Не начато').first()).toBeVisible()
  await pc.close()

  // сотрудник подразделения видит новый курс
  const ctx = await phone(browser)
  const p = await ctx.newPage()
  await login(p, ACCOUNTS.kas)
  await expect(p.getByText('Как заполнять журнал приёма-передачи смены').first()).toBeVisible()
  await ctx.close()
})

test('заменённая редакция: пересдача старой не предлагается, действующая — в заданиях', async ({ browser, baseURL }) => {
  // состояние готовится через API: курс редакции 7, неудачная попытка сотрудника, затем выходит редакция 8
  const api = await request.newContext({ baseURL })
  const auth = async (who: { login: string; password: string }) => {
    const r = await api.post('/api/auth/login', { data: { login: who.login, password: who.password, device_id: 'e2e-rev', device_kind: 'pc' } })
    const body = await r.json()
    return { headers: { Authorization: `Bearer ${body.token}` }, userId: body.user.id as string }
  }
  const m = await auth(ACCOUNTS.metodist)
  const e = await auth(ACCOUNTS.smag)
  const title = 'Порядок передачи смены'
  const created = await (await api.post('/api/courses', { headers: m.headers, data: { title, sop_code: 'СОП-ПР-020', revision: 7 } })).json()
  const v1 = created.version_id as string
  await api.post(`/api/versions/${v1}/blocks`, { headers: m.headers, data: { type: 'text', title: 'Текст', data: { text: 'Смена передаётся под запись.' } } })
  await api.post(`/api/versions/${v1}/questions`, { headers: m.headers, data: {
    type: 'single', text: 'Как передаётся смена?', options: [{ id: 'a', text: 'Под запись в журнале' }, { id: 'b', text: 'Устно' }], correct: 'a', points: 1 } })
  expect((await api.post(`/api/versions/${v1}/publish`, { headers: m.headers, data: {} })).ok()).toBeTruthy()
  await api.post('/api/assignments', { headers: m.headers, data: { version_id: v1, user_ids: [e.userId] } })
  const state = await (await api.get('/api/me/state', { headers: e.headers })).json()
  const assignment = state.assignments.find((a: { version_id: string }) => a.version_id === v1)
  const now = new Date().toISOString()
  const sent = await (await api.post('/api/sync/events', { headers: e.headers, data: { device_id: 'e2e-rev', sent_at: now, events: [{
    id: crypto.randomUUID(), type: 'attempt.submit', device_time: now,
    payload: { attempt_id: crypto.randomUUID(), assignment_id: assignment.id, version_id: v1, submitted_at: now, answers: { [assignment.version.id]: 'b' } } }] } })).json()
  expect(sent.results[0].status).toBe('accepted')                     // ответа на вопрос нет → «Не сдал», осталось 2 попытки
  const v2 = (await (await api.post(`/api/courses/${created.course_id}/new-version`, { headers: m.headers })).json()).version_id as string
  expect((await api.post(`/api/versions/${v2}/publish`, { headers: m.headers, data: {} })).ok()).toBeTruthy()
  await api.dispose()

  const ctx = await phone(browser)
  const p = await ctx.newPage()
  await login(p, ACCOUNTS.smag)
  // действующая редакция 8 — среди активных заданий; редакция 7 — в завершённых, без предложения пересдать
  await expect(p.getByText('СОП-ПР-020 · редакция 8').first()).toBeVisible()
  const old = p.locator('a.task', { hasText: 'редакция 7' })
  await expect(old.getByText('Заменено новой редакцией')).toBeVisible()
  await old.click()
  await expect(p.getByRole('heading', { name: 'Редакция заменена новой' })).toBeVisible()
  await expect(p.getByRole('button', { name: /Пройти тест/ })).toHaveCount(0)
  await expect(p.getByRole('button', { name: /Скачать/ })).toHaveCount(0)
  await shot(p, '35-replaced-revision')
  await ctx.close()
})

test('импорт сотрудников из CSV: предпросмотр, создание, повторный импорт без дублей, экспорт', async ({ browser }) => {
  const pc = await desktop(browser)
  const m = await pc.newPage()
  await login(m, ACCOUNTS.metodist)
  await m.goto('/m/users')
  await m.getByRole('button', { name: 'Импорт из CSV' }).click()
  const dialog = m.getByRole('dialog')
  const [chooser] = await Promise.all([m.waitForEvent('filechooser'), dialog.getByRole('button', { name: 'Выбрать файл' }).click()])
  await chooser.setFiles(join(ROOT, 'samples', 'employees_import.csv'))
  await expect(dialog.getByText('Новых: 4')).toBeVisible()
  await expect(dialog.getByText('Уже есть: 1')).toBeVisible()
  await expect(dialog.getByText('С ошибками: 1')).toBeVisible()
  await expect(dialog.getByText('нет табельного номера')).toBeVisible()
  await shot(m, '31-import-preview')
  await dialog.getByRole('button', { name: 'Создать 4' }).click()
  await expect(m.getByText('Создано сотрудников: 4')).toBeVisible()
  await m.getByRole('button', { name: 'Готово' }).click()
  await m.getByLabel('Поиск по ФИО, табельному номеру, логину').fill('жумабаев')
  await expect(m.locator('tbody tr')).toHaveCount(1)

  // тот же файл ещё раз: создавать некого
  await m.getByRole('button', { name: 'Импорт из CSV' }).click()
  const [again] = await Promise.all([m.waitForEvent('filechooser'), m.getByRole('dialog').getByRole('button', { name: 'Выбрать файл' }).click()])
  await again.setFiles(join(ROOT, 'samples', 'employees_import.csv'))
  await expect(m.getByRole('dialog').getByText('Новых: 0')).toBeVisible()
  await expect(m.getByRole('dialog').getByText('Уже есть: 5')).toBeVisible()
  await expect(m.getByRole('dialog').getByRole('button', { name: /^Создать/ })).toBeDisabled()
  await m.getByRole('dialog').getByRole('button', { name: 'Отмена' }).click()

  const [file] = await Promise.all([m.waitForEvent('download'), m.getByRole('button', { name: 'Экспорт в Excel (CSV)' }).click()])
  expect(file.suggestedFilename()).toBe('Сотрудники.csv')
  await pc.close()
})

test('оценка 360°: ответы участников пересчитывают сводку на открытом экране методиста', async ({ browser }) => {
  const pc = await desktop(browser)
  const m = await pc.newPage()
  await login(m, ACCOUNTS.metodist)
  await m.goto('/m/assessments')
  const card = m.locator('section', { hasText: 'Ибраев Тимур Асхатович' }).first()
  await expect(card.getByText('Ответов: 0 из 4')).toBeVisible()
  const firstRow = card.locator('tbody tr').first()

  async function answer(who: { login: string; password: string }, scores: (number | null)[]): Promise<void> {
    const ctx = await phone(browser)
    const p = await ctx.newPage()
    await login(p, who)
    await p.getByRole('link', { name: 'Опросы' }).click()
    const task = p.locator('article', { hasText: /Ибраев Тимур Асхатович|Оцените себя/ }).first()
    await task.getByRole('button', { name: /Заполнить|Изменить оценки/ }).click()
    const groups = p.getByRole('dialog').locator('fieldset')
    for (let i = 0; i < scores.length; i++) {
      const s = scores[i]
      await groups.nth(i).getByRole('button', { name: s === null ? 'Не могу оценить' : String(s), exact: true }).click()
    }
    await p.getByRole('dialog').getByRole('button', { name: 'Отправить' }).click()
    await expect(p.getByText('Отправлено').first()).toBeVisible()
    await ctx.close()
  }

  await answer(ACCOUNTS.operator, [4, 5, 3])                    // самооценка
  await expect(card.getByText('Ответов: 1 из 4')).toBeVisible({ timeout: 15_000 })   // экран методиста обновился сам
  await answer(ACCOUNTS.master, [3, null, 4])                   // руководитель
  await answer(ACCOUNTS.kim, [5, 4, null])                      // коллега 1
  await answer(ACCOUNTS.abdr, [3, 2, null])                     // коллега 2
  await expect(card.getByText('Ответов: 4 из 4')).toBeVisible({ timeout: 15_000 })
  // первая компетенция: самооценка 4, руководитель 3, коллеги (5+3)/2 = 4, окружение (3+5+3)/3 = 3,67, ответов 4
  await expect(firstRow.locator('td').nth(1)).toHaveText('4')
  await expect(firstRow.locator('td').nth(2)).toHaveText('3')
  await expect(firstRow.locator('td').nth(3)).toContainText('4')
  await expect(firstRow.locator('td').nth(4)).toHaveText('3,7')
  await expect(firstRow.locator('td').nth(6)).toHaveText('4')
  // вторая: руководитель пропустил — прочерк, а не ноль; ответов 3
  const second = card.locator('tbody tr').nth(1)
  await expect(second.locator('td').nth(2)).toHaveText('—')
  await expect(second.locator('td').nth(6)).toHaveText('3')
  await shot(m, '32-assessment-360-summary')

  // одна изменённая оценка меняет сводку
  await answer(ACCOUNTS.kim, [1, 4, null])
  await expect(firstRow.locator('td').nth(3)).toContainText('2', { timeout: 15_000 })        // (1+3)/2
  await expect(firstRow.locator('td').nth(4)).toHaveText('2,3')                                // (3+1+3)/3
  await shot(m, '33-assessment-360-recalculated')
  await pc.close()
})
