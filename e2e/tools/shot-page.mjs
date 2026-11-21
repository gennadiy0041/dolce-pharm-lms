// Снимок страницы в Chromium: node tools/shot-page.mjs <адрес или путь к html> <png> [ширина] [высота]
import { chromium } from '@playwright/test'
import { pathToFileURL } from 'node:url'
const [target, out, w = '1920', h = '1080'] = process.argv.slice(2)
const url = /^(https?|file):/.test(target) ? target : pathToFileURL(target.split('?')[0]).href + (target.includes('?') ? '?' + target.split('?')[1] : '')
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: Number(w), height: Number(h) } })
page.on('pageerror', (e) => console.error('pageerror:', e.message))
page.on('requestfailed', (r) => console.error('failed:', r.url(), r.failure()?.errorText))
await page.goto(url)
await page.evaluate(() => document.fonts.ready)
await page.waitForTimeout(300)
await page.screenshot({ path: out })
console.log('fonts:', await page.evaluate(() => [...document.fonts].filter((f) => f.status === 'loaded').length), 'saved', out)
await browser.close()
