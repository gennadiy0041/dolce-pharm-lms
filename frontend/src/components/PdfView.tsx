// Просмотр PDF локальным pdf.js: страницы рисуются на canvas, внешние сервисы не нужны.
import { useEffect, useRef, useState } from 'react'
import { ZoomIn, ZoomOut } from 'lucide-react'
// сборка legacy рассчитана на браузеры постарше — важно для телефонов, которые давно не обновлялись
import * as pdfjs from 'pdfjs-dist/legacy/build/pdf.mjs'
import workerUrl from 'pdfjs-dist/legacy/build/pdf.worker.min.mjs?url'
import { Notice, Spinner } from '../ui/kit'

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

type PdfDoc = Awaited<ReturnType<typeof pdfjs.getDocument>['promise']>

const ZOOMS = [1, 1.5, 2, 3]

export function PdfView({ blob, label }: { blob: Blob; label?: string }) {
  const boxRef = useRef<HTMLDivElement>(null)
  const [doc, setDoc] = useState<PdfDoc | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [zoom, setZoom] = useState(0)
  const [width, setWidth] = useState(0)

  useEffect(() => {
    let alive = true
    let task: ReturnType<typeof pdfjs.getDocument> | null = null
    setDoc(null)
    setError(null)
    ;(async () => {
      try {
        const data = new Uint8Array(await blob.arrayBuffer())
        if (!alive) return
        task = pdfjs.getDocument({ data, standardFontDataUrl: '/pdfjs/standard_fonts/', wasmUrl: '/pdfjs/wasm/' })
        const loaded = await task.promise
        if (!alive) return
        // альбомные страницы (таблицы Excel, слайды) на узком экране сразу показываем крупнее:
        // столбцы читаются, а лист прокручивается внутри рамки
        const first = (await loaded.getPage(1)).getViewport({ scale: 1 })
        const narrow = (boxRef.current?.clientWidth ?? 9999) < 520
        setZoom(narrow && first.width > first.height * 1.2 ? 2 : 0)
        setDoc(loaded)
      } catch {
        if (alive) setError('Не удалось открыть документ. Файл повреждён или защищён паролем.')
      }
    })()
    return () => {
      alive = false
      void task?.destroy()
    }
  }, [blob])

  useEffect(() => {
    const el = boxRef.current
    if (!el) return
    const measure = () => setWidth(Math.max(240, el.clientWidth - 16))
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  if (error) return <Notice tone="danger" title="Документ не открылся">{error}</Notice>

  return (
    <div>
      <div className="doc-tools">
        <span className="muted small doc-name">{doc ? `${label ? `${label} · ` : ''}страниц: ${doc.numPages}` : 'Открываем документ…'}</span>
        <button className="btn btn-secondary btn-sm" onClick={() => setZoom((z) => Math.max(0, z - 1))} disabled={zoom === 0} aria-label="Уменьшить">
          <ZoomOut aria-hidden /> Мельче
        </button>
        <button className="btn btn-secondary btn-sm" onClick={() => setZoom((z) => Math.min(ZOOMS.length - 1, z + 1))} disabled={zoom === ZOOMS.length - 1} aria-label="Увеличить">
          <ZoomIn aria-hidden /> Крупнее
        </button>
      </div>
      <div className="doc" ref={boxRef} tabIndex={0} aria-label={label ?? 'Документ'}>
        {!doc && <div className="center" style={{ padding: '2rem', color: '#fff' }}><Spinner label="Открываем…" /></div>}
        {doc && width > 0 && Array.from({ length: doc.numPages }, (_, i) => (
          <PdfPage key={`${i}-${zoom}-${width}`} doc={doc} number={i + 1} cssWidth={Math.round(width * ZOOMS[zoom])} root={boxRef.current} />
        ))}
      </div>
    </div>
  )
}

function PdfPage({ doc, number, cssWidth, root }: { doc: PdfDoc; number: number; cssWidth: number; root: HTMLElement | null }) {
  const holder = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [ratio, setRatio] = useState(1.414)
  const [visible, setVisible] = useState(number <= 2)

  useEffect(() => {
    const el = holder.current
    if (!el || visible) return
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) setVisible(true)
    }, { root, rootMargin: '600px 0px' })
    io.observe(el)
    return () => io.disconnect()
  }, [root, visible])

  useEffect(() => {
    if (!visible) return
    let cancelled = false
    let task: { cancel(): void; promise: Promise<unknown> } | null = null
    ;(async () => {
      const page = await doc.getPage(number)
      if (cancelled) return
      const base = page.getViewport({ scale: 1 })
      setRatio(base.height / base.width)
      const dpr = Math.min(window.devicePixelRatio || 1, 2.5)
      const viewport = page.getViewport({ scale: (cssWidth / base.width) * dpr })
      const canvas = canvasRef.current
      if (!canvas) return
      canvas.width = Math.floor(viewport.width)
      canvas.height = Math.floor(viewport.height)
      canvas.style.width = `${cssWidth}px`
      canvas.style.height = `${Math.round(cssWidth * (base.height / base.width))}px`
      task = page.render({ canvas, viewport })
      try {
        await task.promise
      } catch {
        /* отрисовка отменена при смене масштаба */
      }
    })()
    return () => {
      cancelled = true
      task?.cancel()
    }
  }, [doc, number, cssWidth, visible])

  return (
    <div ref={holder} className="doc-page" style={{ width: cssWidth, minHeight: Math.round(cssWidth * ratio) }}>
      {visible && <canvas ref={canvasRef} aria-label={`Страница ${number}`} role="img" />}
    </div>
  )
}
