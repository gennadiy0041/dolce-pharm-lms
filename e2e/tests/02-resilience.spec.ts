// Сбои связи: прерванная загрузка, потерянное подтверждение, работа тренажёра и библиотеки без сети,
// раздельные данные разных сотрудников на одном устройстве.
import { expect, test } from '@playwright/test'
import { ACCOUNTS, apiGet, apiLogin, deviceToken, login, measure, openCourse, phone, shot, waitForShell } from './helpers'

const MAIN = 'Как проверить актуальность СОП перед началом смены'
const HYGIENE = 'Гигиена и переодевание при входе в чистую зону'

test('прерванная загрузка не получает статус готовности и продолжается с того же места', async ({ browser }) => {
  const ctx = await phone(browser)
  const p = await ctx.newPage()
  await login(p, ACCOUNTS.kim)
  await waitForShell(p)
  // открываем страницу курса редакции 2 (у Ким назначение переведено на новую редакцию)
  await openCourse(p, MAIN)

  // связь обрывается после второго куска файла
  let fileRequests = 0
  await ctx.route('**/api/files/*/content', async (route) => {
    fileRequests += 1
    if (fileRequests > 2) await route.abort('internetdisconnected')
    else await route.continue()
  })
  await p.getByRole('button', { name: 'Скачать для работы без сети' }).click()
  await expect(p.getByText('Загрузка не завершена')).toBeVisible({ timeout: 30_000 })
  await expect(p.getByText(/Осталось скачать/)).toBeVisible()
  await expect(p.getByText('Готово без сети')).toHaveCount(0)                    // готовность не выставлена
  await expect(p.getByText('Скачано не полностью')).toBeVisible()
  await shot(p, '21-download-interrupted')
  const firstPass = fileRequests

  // связь вернулась: докачиваем только недостающее
  await ctx.unroute('**/api/files/*/content')
  let resumed = 0
  await ctx.route('**/api/files/*/content', async (route) => {
    resumed += 1
    await route.continue()
  })
  await p.getByRole('button', { name: 'Продолжить загрузку' }).click()
  await expect(p.getByText('Готово без сети').first()).toBeVisible({ timeout: 60_000 })
  const total = await p.evaluate(async () => {
    const dbs = await indexedDB.databases()
    return dbs.map((d) => d.name).filter((n) => n?.startsWith('dolce-lms-u-')).length
  })
  expect(total).toBe(1)
  // уже скачанные куски повторно не запрашивались: всего кусков в пакете больше, чем запросов при докачке
  measure('download-resume', { firstPassRequests: firstPass, resumeRequests: resumed })
  expect(resumed).toBeGreaterThan(0)
  expect(resumed).toBeLessThan(11)
  await ctx.close()
})

test('потеря подтверждения сервера: повторная отправка даёт одну попытку', async ({ browser }) => {
  const ctx = await phone(browser)
  const p = await ctx.newPage()
  await login(p, ACCOUNTS.kim)
  await waitForShell(p)
  await p.locator('a.task', { hasText: HYGIENE }).click()
  await p.getByRole('button', { name: 'Скачать для работы без сети' }).click()
  await expect(p.getByText('Готово без сети').first()).toBeVisible({ timeout: 60_000 })
  await p.getByRole('button', { name: 'Перейти к тесту' }).click()
  await p.getByRole('button', { name: 'Начать тест' }).click()
  await p.getByRole('radio', { name: 'Не меньше 30 секунд' }).click()
  await p.getByRole('button', { name: 'Дальше' }).click()
  await p.getByRole('checkbox', { name: 'Часы' }).click()
  await p.getByRole('button', { name: 'Дальше' }).click()
  await p.getByRole('button', { name: 'Дальше' }).click()                        // последовательность — как показано
  await p.getByLabel('Ваш ответ').fill('Не войду в зону, заменю комбинезон и скажу мастеру')

  // сервер примет запрос с попыткой, но ответ до телефона не дойдёт
  let lost = 0
  await ctx.route('**/api/sync/events', async (route) => {
    const body = route.request().postData() ?? ''
    if (lost === 0 && body.includes('attempt.submit')) {
      lost += 1
      await route.fetch()                       // запись зафиксирована на сервере
      await route.abort('connectionreset')      // подтверждение потеряно
      return
    }
    await route.continue()
  })
  await p.getByRole('button', { name: 'Проверить и сдать' }).click()
  await p.getByRole('button', { name: 'Сдать тест' }).click()
  await p.getByRole('button', { name: 'Да, сдать' }).click()
  await expect(p.getByRole('heading', { name: 'Ответы сохранены на телефоне' })).toBeVisible()
  // приложение само повторяет отправку и получает подтверждение
  await expect(p.getByRole('heading', { name: 'Результат получен отделом обучения' })).toBeVisible({ timeout: 60_000 })
  expect(lost).toBe(1)

  const token = await apiLogin(p, ACCOUNTS.metodist)
  const report = await apiGet<{ rows: Record<string, any>[] }>(p, token, '/api/reports/attempts')
  const mine = report.rows.filter((r) => r.full_name === 'Ким Алина Викторовна' && r.course_title === HYGIENE)
  expect(mine).toHaveLength(1)                                                   // одна попытка, один результат
  expect(mine[0].status).toBe('review')
  expect(mine[0].attempt_no).toBe(1)
  const state = await apiGet<{ assignments: Record<string, any>[] }>(p, await apiLogin(p, ACCOUNTS.kim), '/api/me/state')
  expect(state.assignments.find((a) => a.version.title === HYGIENE)!.attempts).toHaveLength(1)
  await ctx.close()
})

test('без сети: два выбора в тренажёре ведут к разным финалам, библиотека ищет и открывает сохранённое', async ({ browser }) => {
  const ctx = await phone(browser)
  let p = await ctx.newPage()
  await login(p, ACCOUNTS.kim)
  await waitForShell(p)
  // пока есть связь: скачиваем курс и сохраняем справочник
  await expect(p.getByRole('heading', { level: 1, name: MAIN })).toBeVisible()
  await p.getByRole('button', { name: 'Скачать для работы без сети' }).click()
  await expect(p.getByText('Готово без сети').first()).toBeVisible({ timeout: 60_000 })
  await p.getByRole('link', { name: 'Библиотека' }).click()
  await p.getByLabel('Поиск по названию, коду СОП, категории и тексту документа').fill('памятка')
  const memo = p.locator('article', { hasText: 'Памятка: в смене оказалась инструкция другой редакции' })
  await memo.getByRole('button', { name: 'Сохранить' }).click()
  await expect(memo.getByText('На устройстве')).toBeVisible({ timeout: 30_000 })

  await ctx.setOffline(true)
  await p.close()
  p = await ctx.newPage()
  await p.goto('/library')
  await expect(p.getByText('Поиск идёт по сохранённому списку')).toBeVisible({ timeout: 15_000 })
  await p.getByLabel('Поиск по названию, коду СОП, категории и тексту документа').fill('соп-ок-015')   // по коду СОП, без сети
  // первыми — два материала с этим кодом; ниже может быть реестр, где код упомянут в тексте
  await expect(p.locator('article').nth(0)).toContainText(/Памятка|Пятиминутка/)
  await expect(p.locator('article').nth(1)).toContainText(/Памятка|Пятиминутка/)
  await expect(p.locator('article', { hasText: 'Пятиминутка' }).getByText('Нужна связь')).toBeVisible()
  await p.getByLabel('Категория').selectOption('Памятки')
  await expect(p.locator('article')).toHaveCount(1)
  await p.getByRole('link', { name: 'Открыть' }).click()
  await expect(p.locator('.doc canvas').first()).toBeVisible()                             // открывается из локального хранилища
  await shot(p, '22-library-offline')

  // тренажёр без сети: плохая развязка, затем хорошая
  await p.goto('/')
  await openCourse(p, MAIN)
  await p.getByRole('link', { name: /Тренажёр/ }).click()
  await p.getByRole('button', { name: 'Начать разговор' }).click()
  await p.getByRole('button', { name: /Начинаю работу: отличия наверняка небольшие/ }).click()
  await expect(p.getByText(/рассержен/)).toBeVisible()
  await p.getByRole('button', { name: /Всё как всегда/ }).click()
  await expect(p.getByRole('heading', { name: 'Серия забракована, начато расследование' })).toBeVisible()
  await expect(p.getByText('Плохой исход')).toBeVisible()
  await shot(p, '23-trainer-bad-ending-offline')
  await p.getByRole('button', { name: /Пройти ещё раз/ }).click()
  await p.getByRole('button', { name: /Не запускаю пресс и иду к мастеру смены/ }).click()
  await p.getByRole('button', { name: /Лежит на месте/ }).click()
  await p.getByRole('button', { name: /читаю третий раздел до запуска/ }).click()
  await expect(p.getByRole('heading', { name: 'Смена началась по действующей редакции' })).toBeVisible()
  await expect(p.getByText('Хороший исход')).toBeVisible()

  // связь вернулась — оба исхода сохранены на сервере
  await ctx.setOffline(false)
  const token = await apiLogin(p, ACCOUNTS.metodist)
  await expect.poll(async () => {
    const rows = await apiGet<Record<string, any>[]>(p, token, '/api/reports/trainer')
    return rows.filter((r) => r.full_name === 'Ким Алина Викторовна').map((r) => r.outcome).sort().join(',')
  }, { timeout: 60_000 }).toBe('bad,good')
  await ctx.close()
})

test('другой сотрудник на том же телефоне не видит чужих локальных данных; вход без сети по сохранённому паролю', async ({ browser }) => {
  const ctx = await phone(browser)
  const p = await ctx.newPage()
  await login(p, ACCOUNTS.kim)
  await waitForShell(p)
  await expect(p.getByRole('heading', { level: 1, name: MAIN })).toBeVisible()
  await p.getByRole('button', { name: 'Скачать для работы без сети' }).click()
  await expect(p.getByText('Готово без сети').first()).toBeVisible({ timeout: 60_000 })
  // выход Ким, вход другого сотрудника
  await p.getByRole('link', { name: 'Профиль' }).click()
  await p.getByRole('button', { name: 'Выйти' }).click()
  await p.getByRole('dialog').getByRole('button', { name: 'Выйти' }).click()
  await expect(p.getByRole('button', { name: 'Войти' })).toBeVisible()
  await login(p, ACCOUNTS.kas)
  await expect(p.getByText(/Здравствуйте, Данияр/)).toBeVisible()
  await expect(p.getByText('Готово без сети')).toHaveCount(0)                    // чужой скачанный курс не виден
  await expect(p.getByText(/Не скачано/).first()).toBeVisible()
  const bases = await p.evaluate(async () => (await indexedDB.databases()).map((d) => d.name).filter((n) => n?.startsWith('dolce-lms-u-')))
  expect(bases).toHaveLength(2)                                                  // у каждого своя база на устройстве
  await p.getByRole('link', { name: 'Профиль' }).click()
  await expect(p.getByText('Скачано курсов: 0')).toBeVisible()
  await p.getByRole('button', { name: 'Выйти' }).click()
  await p.getByRole('dialog').getByRole('button', { name: 'Выйти' }).click()
  await expect(p.getByRole('button', { name: 'Войти' })).toBeVisible()

  // сети нет: Ким входит по паролю, сохранённому на устройстве в виде проверочного хэша
  await ctx.setOffline(true)
  await p.goto('/')
  await p.getByLabel('Логин').fill(ACCOUNTS.kim.login)
  await p.getByLabel('Пароль').fill('неверный пароль')
  await p.getByRole('button', { name: 'Войти' }).click()
  await expect(p.getByText(/пароль не совпал/)).toBeVisible({ timeout: 20_000 })
  await p.getByLabel('Пароль').fill(ACCOUNTS.kim.password)
  await p.getByRole('button', { name: 'Войти' }).click()
  await expect(p.getByText(/Здравствуйте, Алина/)).toBeVisible({ timeout: 20_000 })
  await expect(p.getByText('Готово без сети').first()).toBeVisible()             // её курсы на месте
  await expect(p.getByText('Войдите заново, чтобы отправить результаты')).toBeVisible()
  await shot(p, '24-offline-login-needs-relogin')
  // без сети кнопка «Войти» не заводит в тупик: пароль проверен по копии на устройстве, человек возвращается к работе
  await p.getByRole('link', { name: 'Войти' }).click()
  await p.getByLabel('Пароль').fill(ACCOUNTS.kim.password)
  await p.getByRole('button', { name: 'Войти' }).click()
  await expect(p.getByText(/Здравствуйте, Алина/)).toBeVisible({ timeout: 20_000 })
  await ctx.close()
})

test('сессия завершена на сервере: приложение само просит войти, после входа работа продолжается', async ({ browser }) => {
  const ctx = await phone(browser)
  const p = await ctx.newPage()
  await login(p, ACCOUNTS.kas)
  await waitForShell(p)
  await expect(p.getByRole('button', { name: /Состояние связи: Всё отправлено/ })).toBeVisible({ timeout: 20_000 })
  // токен отзывается на сервере (так же заканчивается сессия через 30 дней или при отключении учётной записи)
  const token = await deviceToken(p)
  expect(token).not.toBe('')
  const res = await p.request.post('/api/auth/logout', { headers: { Authorization: `Bearer ${token}` } })
  expect(res.ok()).toBeTruthy()
  // приложение узнаёт об этом при очередном обращении к серверу и показывает приглашение войти
  await expect(p.getByText('Войдите заново, чтобы отправить результаты')).toBeVisible({ timeout: 40_000 })
  await expect(p.getByRole('button', { name: /Состояние связи: Нужен вход/ })).toBeVisible()
  await shot(p, '34-session-expired-banner')
  await p.getByRole('link', { name: 'Войти' }).click()
  await p.getByLabel('Пароль').fill(ACCOUNTS.kas.password)
  await p.getByRole('button', { name: 'Войти' }).click()
  await expect(p.getByText(/Здравствуйте, Данияр/)).toBeVisible()
  await expect(p.getByText('Войдите заново, чтобы отправить результаты')).toHaveCount(0)
  await expect(p.getByRole('button', { name: /Состояние связи: Всё отправлено/ })).toBeVisible({ timeout: 30_000 })
  await ctx.close()
})
