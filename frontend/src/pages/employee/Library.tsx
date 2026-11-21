// Библиотека: поиск по словам в названии, коде СОП, категории, описании и тексте документа.
// Список вместе с текстами хранится на устройстве: поиск работает и без сети, открываются сохранённые материалы.
import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useLiveQuery } from '../../local/live'
import { CircleCheck, Download, Search, Trash2, WifiOff } from 'lucide-react'
import { api, presentBlob, query } from '../../api/client'
import { DocumentView, VideoView, type FileSource } from '../../components/Viewers'
import { collectGarbage, downloadLibraryFiles, fileBlob, formatBytes } from '../../local/download'
import { trackedDownload } from '../../local/downloads'
import { isActive, useActive } from '../../local/lifecycle'
import { searchItems } from '../../local/search'
import { useNet } from '../../local/net'
import { currentDb } from '../../local/session'
import { beginMaterial, endMaterial } from '../../local/tracker'
import type { BlockType, FileRef, LibraryItem, ManifestBlock } from '../../types'
import { Empty, Loading, Notice, Pill, Spinner, errorText, toast } from '../../ui/kit'

interface LibraryData { items: LibraryItem[]; categories: string[] }

function useLibrary(): { data?: LibraryData; pins: Map<string, LibraryItem>; offline: boolean } {
  const net = useNet()
  const cached = useLiveQuery(() => currentDb().state.get('library'), [], undefined)
  const pinRows = useLiveQuery(() => currentDb().libraryPins.toArray(), [], [])
  const active = useActive()
  useEffect(() => {
    // в фоне база закрыта — список обновится, когда приложение вернётся на экран
    if (net.reachable !== true || !active) return
    api.get<LibraryData>('/api/library')
      .then((value) => (isActive() ? currentDb().state.put({ key: 'library', value, savedAt: new Date().toISOString() }) : undefined))
      .catch(() => undefined)
  }, [net.reachable, active])
  const pins = useMemo(() => new Map(pinRows.map((p) => [p.itemId, p.item])), [pinRows])
  let data = cached?.value as LibraryData | undefined
  if (!data && pinRows.length) data = { items: pinRows.map((p) => p.item), categories: [...new Set(pinRows.map((p) => p.item.category).filter(Boolean))] }
  return { data, pins, offline: net.reachable === false }
}

function refsOf(item: LibraryItem): FileRef[] {
  return item.view && item.view.id !== item.file.id ? [item.file, item.view] : [item.file]
}

async function saveItem(item: LibraryItem, onProgress: (pct: number) => void): Promise<void> {
  const db = currentDb()
  // загрузка зарегистрирована в общем списке: выход и «Очистить данные» останавливают её и ждут завершения
  await trackedDownload((signal) => downloadLibraryFiles(db, refsOf(item), signal,
    (p) => onProgress(p.totalBytes ? p.doneBytes / p.totalBytes : 0),
    () => db.libraryPins.put({ itemId: item.id, item, savedAt: new Date().toISOString() })))
}

export function LibraryPage() {
  const { data, pins, offline } = useLibrary()
  const [q, setQ] = useState('')
  const [category, setCategory] = useState('')
  const [onlySaved, setOnlySaved] = useState(false)
  const [busy, setBusy] = useState<Record<string, number>>({})

  const items = useMemo(() => {
    // сохранённая копия материала знает свой текст, даже если в общем списке его ещё нет
    const pool = (data?.items ?? []).filter((i) => (!category || i.category === category) && (!onlySaved || pins.has(i.id)))
      .map((i) => (i.text === undefined && pins.get(i.id)?.text ? { ...i, text: pins.get(i.id)!.text } : i))
    return searchItems(pool, q)
  }, [data, q, category, onlySaved, pins])

  if (!data) return offline ? <Empty title="Библиотека недоступна без сети">Сохраните нужные материалы на устройство, пока есть связь.</Empty> : <Loading />

  const save = async (item: LibraryItem) => {
    setBusy((b) => ({ ...b, [item.id]: 0 }))
    try {
      await saveItem(item, (pct) => setBusy((b) => ({ ...b, [item.id]: pct })))
      toast('Материал сохранён на устройство', 'ok')
    } catch (e) {
      toast(errorText(e), 'error')
    } finally {
      setBusy((b) => {
        const next = { ...b }
        delete next[item.id]
        return next
      })
    }
  }
  const remove = async (item: LibraryItem) => {
    const db = currentDb()
    await db.libraryPins.delete(item.id)
    await collectGarbage(db)
    toast('Удалено с устройства')
  }

  return (
    <div className="stack">
      <h1>Библиотека</h1>
      {offline && <Notice tone="warn" title="Нет связи">Поиск идёт по сохранённому списку. Открываются материалы с отметкой «На устройстве».</Notice>}
      <div className="card stack-sm">
        <div className="field">
          <label htmlFor="lib-q">Поиск по названию, коду СОП, категории и тексту документа</label>
          <div className="row-tight">
            <Search aria-hidden className="muted" />
            <input id="lib-q" className="input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Например: СОП-ОК-015 или гигиена рук" />
          </div>
        </div>
        <div className="row">
          <div className="field grow">
            <label htmlFor="lib-cat">Категория</label>
            <select id="lib-cat" className="select" value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">Все категории</option>
              {data.categories.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <label className="check"><input type="checkbox" checked={onlySaved} onChange={(e) => setOnlySaved(e.target.checked)} /> Только на устройстве</label>
        </div>
      </div>
      <p className="muted small" aria-live="polite">Найдено: {items.length}</p>
      {items.length === 0 && <Empty title="Ничего не найдено">Измените запрос или категорию.</Empty>}
      {items.map(({ item, snippet }) => {
        const saved = pins.has(item.id)
        const progress = busy[item.id]
        const canOpen = saved || !offline
        return (
          <article key={item.id} className="card stack-sm">
            <div className="row">
              {item.category && <Pill tone="brand">{item.category}</Pill>}
              {item.sop_code && <Pill>{item.sop_code}</Pill>}
              {saved
                ? <Pill tone="ok" icon={<CircleCheck aria-hidden />}>На устройстве</Pill>
                : offline && <Pill tone="warn" icon={<WifiOff aria-hidden />}>Нужна связь</Pill>}
            </div>
            <h3>{item.title}</h3>
            {item.description && <p className="muted">{item.description}</p>}
            {snippet && (
              <p className="small lib-snippet"><span className="muted">В тексте документа: </span>
                {snippet.before}<mark>{snippet.match}</mark>{snippet.after}</p>
            )}
            <p className="muted small">{item.file.ext.toUpperCase()} · {formatBytes(item.size)}</p>
            <div className="row">
              {canOpen
                ? <Link className="btn btn-primary grow" to={`/library/${item.id}`}>Открыть</Link>
                : <button className="btn btn-primary grow" disabled>Открыть</button>}
              {saved ? (
                <button className="btn btn-ghost" onClick={() => void remove(item)}><Trash2 aria-hidden /> Удалить</button>
              ) : progress !== undefined ? (
                <span className="btn btn-secondary" aria-live="polite"><Spinner /> {Math.round(progress * 100)} %</span>
              ) : (
                <button className="btn btn-secondary" onClick={() => void save(item)} disabled={offline}><Download aria-hidden /> Сохранить</button>
              )}
            </div>
          </article>
        )
      })}
    </div>
  )
}

export function LibraryItemPage() {
  const { itemId } = useParams()
  const { data, pins, offline } = useLibrary()
  const item = pins.get(itemId ?? '') ?? data?.items.find((i) => i.id === itemId)
  const saved = pins.has(itemId ?? '')

  useEffect(() => {
    if (!item) return
    beginMaterial({ libraryItemId: item.id, mode: item.file.mime.startsWith('video/') ? 'video' : 'reading' })
    return () => endMaterial()
  }, [item?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const source: FileSource = useMemo(() => async (ref: FileRef) => {
    const local = await fileBlob(currentDb(), ref.sha256)
    if (local) return local
    return api.blob(`/api/files/${ref.id}/content`)
  }, [])

  if (!item) return data || offline ? <Empty title="Материал не найден" action={<Link className="btn btn-primary" to="/library">В библиотеку</Link>} /> : <Loading />
  // тип — по исходному файлу: подпись «Исходный файл Word/Excel/PowerPoint тоже сохранён» должна быть верной
  const ext = item.file.ext as BlockType
  const type: BlockType = ext === 'docx' || ext === 'xlsx' || ext === 'pptx' ? ext : 'pdf'
  const block: ManifestBlock = { id: item.id, type, title: item.title, required: false, position: 0, file: item.file, view: item.view ?? undefined }
  const isVideo = item.file.mime.startsWith('video/')

  return (
    <div className="stack">
      <div>
        <Link to="/library" className="btn btn-ghost btn-sm" style={{ marginLeft: '-0.6rem' }}>← Библиотека</Link>
        <h1 style={{ marginTop: '0.3rem' }}>{item.title}</h1>
        <div className="row" style={{ marginTop: '0.5rem' }}>
          {item.category && <Pill tone="brand">{item.category}</Pill>}
          {item.sop_code && <Pill>{item.sop_code}</Pill>}
          {saved && <Pill tone="ok" icon={<CircleCheck aria-hidden />}>На устройстве</Pill>}
        </div>
        {item.description && <p className="muted" style={{ marginTop: '0.5rem' }}>{item.description}</p>}
      </div>
      {!saved && offline
        ? <Notice tone="warn" title="Нужна связь">Материал не сохранён на устройстве.</Notice>
        : isVideo ? <VideoView fileRef={item.file} source={source} />
          : item.view ? <DocumentView block={block} source={source} />
            : <Notice tone="warn" title="Нет копии для чтения">{item.conversion_error ?? 'Откройте исходный файл.'}</Notice>}
      <button className="btn btn-ghost" onClick={async () => {
        try {
          const blob = await source(item.file)
          if (blob) presentBlob(blob, item.file.name, true)
        } catch (e) {
          toast(errorText(e), 'error')
        }
      }}><Download aria-hidden /> Скачать исходный файл ({formatBytes(item.file.size)})</button>
    </div>
  )
}

export { query }
