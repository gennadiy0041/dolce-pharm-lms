// Настоящие файлы Word, Excel и PowerPoint с таблицами и диаграммами: методист добавляет их в библиотеку,
// сотрудник находит по тексту документа, сохраняет на телефон и читает без сети.
// Файлы — e2e/fixtures/office-real (книгу Excel сделал сам Excel, документ и презентацию — python-docx/pptx
// с диаграммами Office). PDF-копию на ноутбуке с Microsoft Office делает сам Office.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test } from '@playwright/test'
import { ACCOUNTS, HERE, apiLogin, login, measure, phone, shot, waitForShell } from './helpers'

const FIXTURES = join(HERE, '..', 'fixtures', 'office-real')
const FILES = [
  { name: 'СОП-ОК-021 Контроль отклонений.docx', title: 'СОП-ОК-021: контроль отклонений (Word)', code: 'СОП-ОК-021', shot: '50-office-docx-offline' },
  { name: 'Журнал температур и отклонений.xlsx', title: 'Журнал температур и отклонений (Excel)', code: '', shot: '51-office-xlsx-offline' },
  { name: 'Гигиена рук — обучение.pptx', title: 'Гигиена рук — обучение (PowerPoint)', code: 'СОП-ГП-004', shot: '52-office-pptx-offline' },
]
const MIME: Record<string, string> = {
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
}

test('Word, Excel и PowerPoint с таблицами и диаграммами: поиск по тексту и чтение без сети', async ({ browser }) => {
  test.setTimeout(300_000)
  const ctx = await phone(browser)
  let p = await ctx.newPage()
  const token = await apiLogin(p, ACCOUNTS.metodist)
  const added: string[] = []
  try {
    for (const f of FILES) {
      const t0 = Date.now()
      const res = await p.request.post('/api/library', {
        headers: { Authorization: `Bearer ${token}` }, timeout: 240_000,
        multipart: {
          title: f.title, sop_code: f.code, category: 'Проверка Office', description: '',
          file: { name: f.name, mimeType: MIME[f.name.split('.').pop()!], buffer: readFileSync(join(FIXTURES, f.name)) },
        },
      })
      expect(res.ok(), await res.text()).toBeTruthy()
      const item = await res.json()
      added.push(item.id)
      expect(item.view?.ext, `${f.name}: PDF-копия для чтения`).toBe('pdf')
      expect(item.text, `${f.name}: текст документа для поиска`).toBeTruthy()
      measure('office-upload-convert', { file: f.name, seconds: (Date.now() - t0) / 1000, pages: item.view.pages })
    }

    await login(p, ACCOUNTS.kim)
    await waitForShell(p)
    await p.goto('/library')
    const search = p.getByLabel('Поиск по названию, коду СОП, категории и тексту документа')
    // слово есть только внутри таблицы Excel — материал находится, и показан отрывок текста
    await search.fill('кондиционера')
    const journal = p.locator('article', { hasText: 'Журнал температур и отклонений' })
    await expect(journal).toBeVisible()
    await expect(journal.locator('.lib-snippet mark')).toBeVisible()
    await shot(p, '49-library-search-in-text')
    await search.fill('')
    await p.getByLabel('Категория').selectOption('Проверка Office')
    for (const f of FILES) {
      const card = p.locator('article', { hasText: f.title })
      await card.getByRole('button', { name: 'Сохранить' }).click()
      await expect(card.getByText('На устройстве')).toBeVisible({ timeout: 60_000 })
    }

    await ctx.setOffline(true)
    await p.close()
    p = await ctx.newPage()
    await p.goto('/library')
    await expect(p.getByText('Поиск идёт по сохранённому списку')).toBeVisible({ timeout: 15_000 })
    // поиск по тексту документа работает и без сети
    await p.getByLabel('Поиск по названию, коду СОП, категории и тексту документа').fill('перерыва перчаток')
    await expect(p.locator('article', { hasText: 'Гигиена рук — обучение' })).toBeVisible()
    for (const f of FILES) {
      await p.goto('/library')
      await p.getByLabel('Категория').selectOption('Проверка Office')
      await p.locator('article', { hasText: f.title }).getByRole('link', { name: 'Открыть' }).click()
      await expect(p.locator('.doc canvas').first()).toBeVisible({ timeout: 30_000 })
      await p.waitForTimeout(800)
      await shot(p, f.shot)
      // страницы рисуются по мере прокрутки: листаем до конца — там диаграмма документа и презентации
      await p.locator('.doc').evaluate((el) => { el.scrollIntoView(); el.scrollTop = el.scrollHeight })
      const last = p.locator('.doc-page').last().locator('canvas')
      await expect(last).toBeVisible({ timeout: 15_000 })
      await p.waitForTimeout(1200)
      await shot(p, f.shot + '-last')
      const painted = await last.evaluate((c: HTMLCanvasElement) => {
        const g = c.getContext('2d')!
        const d = g.getImageData(0, 0, c.width, c.height).data
        let ink = 0
        for (let i = 0; i < d.length; i += 16) if (d[i] < 200 || d[i + 1] < 200 || d[i + 2] < 200) ink++
        return ink
      })
      expect(painted, `${f.name}: страница нарисована`).toBeGreaterThan(500)
    }
  } finally {
    await ctx.setOffline(false)
    for (const id of added) await p.request.delete(`/api/library/${id}`, { headers: { Authorization: `Bearer ${token}` } })
    await ctx.close()
  }
})
