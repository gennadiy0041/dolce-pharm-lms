// Копирует в public/ локальные ресурсы просмотрщика PDF (стандартные шрифты и декодеры),
// чтобы документы открывались без интернета. Запускается перед сборкой и dev-сервером.
import { cpSync, existsSync, mkdirSync, readdirSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const src = join(root, 'node_modules', 'pdfjs-dist')
const dst = join(root, 'public', 'pdfjs')

// Файл, который уже лежит на месте и совпадает по размеру, не перезаписывается: в папке под OneDrive
// перезапись существующего файла иногда завершается ошибкой, и сборка останавливалась на ровном месте.
function copy(from, to) {
  if (existsSync(to) && statSync(to).size === statSync(from).size) return
  cpSync(from, to)
}

mkdirSync(join(dst, 'standard_fonts'), { recursive: true })
mkdirSync(join(dst, 'wasm'), { recursive: true })
for (const f of readdirSync(join(src, 'standard_fonts'))) {
  if (/\.(pfb|ttf)$/.test(f)) copy(join(src, 'standard_fonts', f), join(dst, 'standard_fonts', f))
}
for (const f of ['jbig2.wasm', 'openjpeg.wasm', 'qcms_bg.wasm']) {
  if (existsSync(join(src, 'wasm', f))) copy(join(src, 'wasm', f), join(dst, 'wasm', f))
}
console.log('pdf.js: шрифты и декодеры скопированы в public/pdfjs')
