// Показ блоков урока: текст, видео, документы, ссылка. Источник файлов передаётся снаружи:
// у сотрудника это локальная база (работает без сети), в предпросмотре методиста — сервер.
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Download, ExternalLink, FastForward, Rewind, WifiOff } from 'lucide-react'
import { presentBlob } from '../api/client'
import { formatBytes } from '../local/download'
import { setVideoPlaying } from '../local/tracker'
import type { FileRef, ManifestBlock } from '../types'
import { Notice, Pill, Spinner } from '../ui/kit'
import { PdfView } from './PdfView'
import { TrainerView, type TrainerOutcome } from './TrainerView'

export type FileSource = (ref: FileRef) => Promise<Blob | undefined>

export const BLOCK_LABEL: Record<string, string> = {
  text: 'Текст', link: 'Ссылка', pdf: 'PDF', video: 'Видео', docx: 'Word', xlsx: 'Excel', pptx: 'PowerPoint', trainer: 'Тренажёр',
}

// ---------- текст с простой разметкой ----------
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') ? <strong key={i}>{part.slice(2, -2)}</strong> : part)
}

const sameTitle = (a: string, b: string) => a.replace(/[*#\s]+/g, ' ').trim().toLowerCase() === b.replace(/\s+/g, ' ').trim().toLowerCase()

/** `title` — название шага над текстом. Если текст начинается с такого же заголовка, второй раз он не выводится. */
export function TextView({ text, title }: { text: string; title?: string }) {
  const nodes = useMemo(() => {
    const out: ReactNode[] = []
    const lines = text.replace(/\r/g, '').split('\n')
    const first = lines.findIndex((l) => l.trim() !== '')
    if (title && first >= 0 && /^#{1,2}\s/.test(lines[first].trim()) && sameTitle(lines[first], title)) lines.splice(0, first + 1)
    let list: { ordered: boolean; items: string[] } | null = null
    let para: string[] = []
    const flushPara = () => {
      if (para.length) out.push(<p key={out.length}>{inline(para.join(' '))}</p>)
      para = []
    }
    const flushList = () => {
      if (!list) return
      const items = list.items.map((t, i) => <li key={i}>{inline(t)}</li>)
      out.push(list.ordered ? <ol key={out.length}>{items}</ol> : <ul key={out.length}>{items}</ul>)
      list = null
    }
    for (const raw of lines) {
      const line = raw.trim()
      const ordered = /^\d+[.)]\s+/.test(line)
      const bullet = /^[-•]\s+/.test(line)
      if (!line) {
        flushPara()
        flushList()
      } else if (line.startsWith('## ')) {
        flushPara(); flushList()
        out.push(<h3 key={out.length}>{inline(line.slice(3))}</h3>)
      } else if (line.startsWith('# ')) {
        flushPara(); flushList()
        out.push(<h2 key={out.length}>{inline(line.slice(2))}</h2>)
      } else if (line.startsWith('> ')) {
        flushPara(); flushList()
        out.push(<blockquote key={out.length}>{inline(line.slice(2))}</blockquote>)
      } else if (ordered || bullet) {
        flushPara()
        if (!list || list.ordered !== ordered) {
          flushList()
          list = { ordered, items: [] }
        }
        list.items.push(line.replace(/^(\d+[.)]|[-•])\s+/, ''))
      } else {
        flushList()
        para.push(line)
      }
    }
    flushPara()
    flushList()
    return out
  }, [text, title])
  return <div className="prose">{nodes}</div>
}

// ---------- файл из источника ----------
function useBlob(ref: FileRef | undefined, source: FileSource): { blob?: Blob; state: 'loading' | 'ready' | 'missing' } {
  const [blob, setBlob] = useState<Blob | undefined>()
  const [state, setState] = useState<'loading' | 'ready' | 'missing'>('loading')
  useEffect(() => {
    let alive = true
    setState('loading')
    setBlob(undefined)
    if (!ref) {
      setState('missing')
      return
    }
    source(ref)
      .then((b) => {
        if (!alive) return
        setBlob(b)
        setState(b ? 'ready' : 'missing')
      })
      .catch(() => alive && setState('missing'))
    return () => {
      alive = false
    }
  }, [ref?.sha256, source]) // eslint-disable-line react-hooks/exhaustive-deps
  return { blob, state }
}

function Missing() {
  return <Notice tone="warn" title="Файл не найден на устройстве">Скачайте курс заново, когда появится связь.</Notice>
}

// ---------- видео ----------
export function VideoView({ fileRef, source }: { fileRef?: FileRef; source: FileSource }) {
  const { blob, state } = useBlob(fileRef, source)
  const videoRef = useRef<HTMLVideoElement>(null)
  const url = useMemo(() => (blob ? URL.createObjectURL(blob) : undefined), [blob])
  useEffect(() => () => {
    if (url) URL.revokeObjectURL(url)
    setVideoPlaying(false)
  }, [url])
  if (state === 'loading') return <Spinner label="Открываем видео…" />
  if (state === 'missing' || !url) return <Missing />
  const jump = (delta: number) => {
    const v = videoRef.current
    if (v) v.currentTime = Math.max(0, Math.min(v.duration || 0, v.currentTime + delta))
  }
  return (
    <div className="stack-sm">
      <video
        ref={videoRef} className="video" src={url} controls playsInline preload="metadata"
        onPlaying={() => setVideoPlaying(true)} onPause={() => setVideoPlaying(false)}
        onEnded={() => setVideoPlaying(false)} onWaiting={() => setVideoPlaying(false)}
      />
      <div className="pair">
        <button className="btn btn-secondary" onClick={() => jump(-10)}><Rewind aria-hidden /> Назад на 10 с</button>
        <button className="btn btn-secondary" onClick={() => jump(10)}>Вперёд на 10 с <FastForward aria-hidden /></button>
      </div>
      <p className="muted small">Видео сохранено на устройстве{fileRef ? ` · ${formatBytes(fileRef.size)}` : ''}. Время засчитывается, пока идёт воспроизведение.</p>
    </div>
  )
}

// ---------- документ (PDF или PDF-копия Office) ----------
export function DocumentView({ block, source }: { block: ManifestBlock; source: FileSource }) {
  const { blob, state } = useBlob(block.view, source)
  const office = block.file && block.view && block.file.id !== block.view.id
  const download = async () => {
    if (!block.file) return
    const original = await source(block.file)
    if (original) presentBlob(original, block.file.name, true)
  }
  return (
    <div className="stack-sm">
      {office && (
        <div className="spread">
          <span className="muted small">Показана копия для чтения. Исходный файл {BLOCK_LABEL[block.type]} тоже сохранён.</span>
          <button className="btn btn-ghost btn-sm" onClick={download}><Download aria-hidden /> Исходный файл ({formatBytes(block.file!.size)})</button>
        </div>
      )}
      {state === 'loading' && <Spinner label="Открываем документ…" />}
      {state === 'missing' && <Missing />}
      {state === 'ready' && blob && <PdfView blob={blob} label={block.view?.name} />}
    </div>
  )
}

// ---------- ссылка ----------
export function LinkView({ block, source }: { block: ManifestBlock; source: FileSource }) {
  return (
    <div className="stack">
      <div className="card card-flat card-tint stack-sm">
        <div className="row">
          {block.needs_internet
            ? <Pill tone="warn" icon={<WifiOff aria-hidden />}>Нужен интернет</Pill>
            : <Pill tone="ok">Копия доступна без сети</Pill>}
          {!block.mandatory && <Pill>Справочно</Pill>}
        </div>
        {block.note && <p>{block.note}</p>}
        <a className="btn btn-secondary" href={block.url} target="_blank" rel="noopener noreferrer">
          <ExternalLink aria-hidden /> Открыть ссылку
        </a>
        <p className="muted small" style={{ wordBreak: 'break-all' }}>{block.url}</p>
      </div>
      {/* локальная копия ссылки может быть и видео — тогда показываем проигрыватель, а не документ */}
      {block.view && (block.view.mime.startsWith('video/')
        ? <VideoView fileRef={block.view} source={source} />
        : <DocumentView block={block} source={source} />)}
    </div>
  )
}

// ---------- блок целиком ----------
export function BlockView({ block, source, onTrainerFinish }: { block: ManifestBlock; source: FileSource; onTrainerFinish?: (r: TrainerOutcome) => void }) {
  switch (block.type) {
    case 'text':
      return <TextView text={block.text ?? ''} title={block.title} />
    case 'video':
      return <VideoView fileRef={block.view ?? block.file} source={source} />
    case 'pdf':
    case 'docx':
    case 'xlsx':
    case 'pptx':
      return <DocumentView block={block} source={source} />
    case 'link':
      return <LinkView block={block} source={source} />
    case 'trainer':
      return block.scenario ? <TrainerView scenario={block.scenario} onFinish={onTrainerFinish} /> : <Missing />
    default:
      return null
  }
}
