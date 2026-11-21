// Печать HTML в PDF через Chromium (без интернета): node tools/print-pdf.mjs <html> <pdf> [landscape]
// Используется для материалов защиты в docs/защита: памятка и шпаргалка.
import { chromium } from '@playwright/test'
import { pathToFileURL } from 'node:url'
import { resolve } from 'node:path'
const [src, out, orientation] = process.argv.slice(2)
const browser = await chromium.launch()
const page = await browser.newPage()
page.on('pageerror', (e) => console.error('pageerror:', e.message))
page.on('requestfailed', (r) => console.error('не загружено:', r.url()))
await page.goto(pathToFileURL(resolve(src)).href)
await page.evaluate(() => document.fonts.ready)
const overflow = await page.evaluate(() => [...document.querySelectorAll('.page')].map((p) => p.scrollHeight - p.clientHeight))
await page.pdf({ path: resolve(out), format: 'A4', landscape: orientation === 'landscape', printBackground: true, preferCSSPageSize: true })
await browser.close()
console.log('сохранено:', out, '| не поместилось по высоте, px:', overflow.join(', '))
