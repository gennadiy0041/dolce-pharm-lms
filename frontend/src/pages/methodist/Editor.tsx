// Редактор курса: сведения, блоки урока, тест, проверка готовности и публикация.
// Изменения сохраняются автоматически; перед публикацией все несохранённые правки дописываются.
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  ArrowDown, ArrowUp, Check, CircleAlert, CircleCheck, Eye, FileText, Film, GitBranch, Link2, MessagesSquare, Plus, Presentation,
  Send, Sheet, Trash2, TriangleAlert, Type, Upload,
} from 'lucide-react'
import { api } from '../../api/client'
import { BLOCK_LABEL } from '../../components/Viewers'
import { QUESTION_LABEL } from '../../components/QuestionView'
import { formatBytes } from '../../local/download'
import type { Scenario } from '../../types'
import { Field, Loading, Modal, Notice, Pill, Spinner, errorText, plural, todayIso, toast } from '../../ui/kit'
import { VERSION_STATUS } from './Courses'

interface FileInfo {
  id: string; name: string; size: number; ext: string; pages: number | null; conversion_status: string
  conversion_error: string | null; conversion_warnings: string[]; converter: string | null
}
interface BlockRow {
  id: string; type: string; title: string; required: boolean; position: number
  data: { text?: string; url?: string; mandatory?: boolean; note?: string; alt_file_id?: string | null; file_id?: string | null; scenario?: Scenario }
  file?: FileInfo | null; view_file?: FileInfo | null
}
interface QuestionRow {
  id: string; type: 'single' | 'multiple' | 'order' | 'open'; text: string; options: { id: string; text: string }[]
  correct: string | string[] | null; points: number; criteria: string; position: number
}
interface VersionFull {
  id: string; course_id: string; revision: number; status: 'draft' | 'published' | 'superseded'; title: string; sop_code: string
  description: string; change_note: string; pass_percent: number; max_attempts: number; gate_test: boolean
  blocks: BlockRow[]; questions: QuestionRow[]; versions: { id: string; revision: number; status: string }[]
}
interface CheckResult {
  errors: string[]; warnings: string[]
  package: { total_size: number; files: number; blocks: number; questions: number }
  previous: { revision: number; passed: number; not_passed: number } | null
}

// ---------- автосохранение ----------
const savers = new Set<() => Promise<boolean>>()
/** Дописывает все несохранённые правки. false — хотя бы одно сохранение не удалось: на сервере остались прежние данные. */
async function flushAll(): Promise<boolean> {
  const results = await Promise.all([...savers].map((fn) => fn()))
  return results.every(Boolean)
}

type SaveState = 'idle' | 'dirty' | 'saving' | 'saved' | 'error'

function useAutosave<T>(value: T, save: (v: T) => Promise<void>, enabled = true): SaveState {
  const [state, setState] = useState<SaveState>('idle')
  const latest = useRef(value)
  const saved = useRef(JSON.stringify(value))
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const saveRef = useRef(save)
  saveRef.current = save
  const enabledRef = useRef(enabled)
  enabledRef.current = enabled
  const wasEnabled = useRef(false)
  const key = JSON.stringify(value)

  // Сохранения идут строго по очереди. Иначе «дописать всё» во время уже летящего запроса сравнило бы
  // данные с прежним снимком и ответило «сохранено», не дождавшись этого запроса.
  const chain = useRef<Promise<boolean>>(Promise.resolve(true))
  const run = useRef((): Promise<boolean> => {
    const next = chain.current.then(runOnce.current, runOnce.current)
    chain.current = next
    return next
  })
  const runOnce = useRef(async (): Promise<boolean> => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
    if (!enabledRef.current) return true      // опубликованная редакция или данные ещё грузятся — сохранять нечего
    const snapshot = JSON.stringify(latest.current)
    if (snapshot === saved.current) return true
    setState('saving')
    try {
      await saveRef.current(latest.current)
      saved.current = snapshot
      setState(JSON.stringify(latest.current) === snapshot ? 'saved' : 'dirty')
      return true
    } catch (e) {
      setState('error')
      toast(`Не сохранено: ${errorText(e)}`, 'error')
      return false
    }
  })

  useEffect(() => {
    latest.current = value
    // Пока сохранение выключено, текущее значение и есть исходное. В момент включения (данные
    // загрузились) оно тоже принимается за исходное — лишнего запроса с теми же значениями не будет.
    if (!enabled || !wasEnabled.current) {
      wasEnabled.current = enabled
      saved.current = key
      return
    }
    if (key === saved.current) {
      // правку вернули назад до отправки — сохранять нечего, индикатор не должен «висеть»
      if (timer.current) clearTimeout(timer.current)
      timer.current = null
      setState((s) => (s === 'dirty' ? 'saved' : s))
      return
    }
    setState('dirty')
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => void run.current(), 600)
  }, [key, enabled]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const fn = run.current
    savers.add(fn)
    return () => {
      savers.delete(fn)
      void fn()
    }
  }, [])
  return state
}

function SaveMark({ state }: { state: SaveState }) {
  if (state === 'saving' || state === 'dirty') return <span className="muted small row-tight"><span className="spinner" style={{ width: '0.9rem', height: '0.9rem', borderWidth: 2 }} /> сохраняем</span>
  if (state === 'saved') return <span className="small row-tight" style={{ color: 'var(--ok)' }}><Check aria-hidden width={16} /> сохранено</span>
  if (state === 'error') return <span className="small error-text">не сохранено</span>
  return null
}

// ---------- загрузка файла ----------
const ACCEPT: Record<string, string> = {
  pdf: '.pdf,application/pdf', video: '.mp4,.m4v,.webm,video/mp4,video/webm', docx: '.docx', xlsx: '.xlsx', pptx: '.pptx',
  any: '.pdf,.mp4,.m4v,.webm,.docx,.xlsx,.pptx',
}

export async function uploadFile(file: File): Promise<FileInfo & { view_file?: FileInfo }> {
  const form = new FormData()
  form.append('file', file)
  return api.upload('/api/files', form)
}

// Загрузки файлов, которые ещё идут (вместе с привязкой файла к блоку). Пока счётчик не ноль,
// публиковать и открывать предпросмотр нельзя: файл ещё не стал частью курса. Счётчик свой у каждого
// открытого редактора — загрузка в другом курсе эту редакцию не блокирует.
type UploadCount = { current: number }
const UploadCounter = createContext<UploadCount>({ current: 0 })
async function whileUploading<T>(counter: UploadCount, work: () => Promise<T>): Promise<T> {
  counter.current += 1
  try {
    return await work()
  } finally {
    counter.current -= 1
  }
}

export function FileStatus({ file, view }: { file?: FileInfo | null; view?: FileInfo | null }) {
  if (!file) return <Notice tone="warn" title="Файл не загружен">Без файла курс нельзя опубликовать.</Notice>
  const office = ['docx', 'xlsx', 'pptx'].includes(file.ext)
  return (
    <div className="stack-sm">
      <p><b>{file.name}</b> <span className="muted small">· {formatBytes(file.size)}</span></p>
      {office && file.conversion_status === 'ok' && (
        <Notice tone="ok" title="Копия для чтения без сети готова">
          PDF, страниц: {view?.pages ?? file.pages ?? '—'}{view ? ` · ${formatBytes(view.size)}` : ''}. Конвертер: {file.converter === 'libreoffice' ? 'LibreOffice' : 'встроенный'}.
          Исходный файл сохранён и скачивается вместе с курсом.
          {file.conversion_warnings.length > 0 && <ul style={{ margin: '0.3rem 0 0', paddingLeft: '1.2rem' }}>{file.conversion_warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
        </Notice>
      )}
      {office && file.conversion_status === 'error' && (
        <Notice tone="danger" title="Не удалось построить копию для чтения">
          {file.conversion_error} Исправьте файл и загрузите снова — иначе курс не опубликуется.
        </Notice>
      )}
      {!office && file.pages ? <p className="muted small">Страниц: {file.pages}</p> : null}
    </div>
  )
}

function UploadButton({ kind, label, onDone, primary }: { kind: string; label: string; onDone: (f: FileInfo & { view_file?: FileInfo }) => Promise<void> | void; primary?: boolean }) {
  const input = useRef<HTMLInputElement>(null)
  const uploads = useContext(UploadCounter)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return (
    <div className="stack-sm">
      <input ref={input} type="file" className="sr-only" accept={ACCEPT[kind] ?? ACCEPT.any} tabIndex={-1} aria-hidden
        onChange={async (e) => {
          const file = e.target.files?.[0]
          e.target.value = ''
          if (!file) return
          setBusy(true)
          setError(null)
          try {
            await whileUploading(uploads, async () => onDone(await uploadFile(file)))
          } catch (err) {
            setError(errorText(err))
          } finally {
            setBusy(false)
          }
        }} />
      <button type="button" className={`btn ${primary ? 'btn-primary' : 'btn-secondary'} btn-sm`} disabled={busy} onClick={() => input.current?.click()}>
        {busy ? <Spinner /> : <Upload aria-hidden />} {busy ? 'Загружаем и проверяем…' : label}
      </button>
      {error && <p className="error-text" role="alert">{error}</p>}
    </div>
  )
}

// ---------- блок ----------
function BlockCard({ block, index, count, readOnly, onChanged, onMove, onDelete }: {
  block: BlockRow; index: number; count: number; readOnly: boolean
  onChanged: (b: BlockRow) => void; onMove: (delta: number) => void; onDelete: () => void
}) {
  const [title, setTitle] = useState(block.title)
  const [required, setRequired] = useState(block.required)
  const [data, setData] = useState(block.data)
  const state = useAutosave({ title, required, data }, async (v) => {
    const next = await api.patch<BlockRow>(`/api/blocks/${block.id}`, v)
    onChanged(next)
  }, !readOnly)
  const fileKind = ['pdf', 'video', 'docx', 'xlsx', 'pptx'].includes(block.type)

  return (
    <div className="block-card">
      <div className="block-head">
        <span className="block-kind">{index + 1}. {BLOCK_LABEL[block.type]}</span>
        <input className="input grow" style={{ minHeight: '2.5rem', minWidth: '12rem' }} value={title} disabled={readOnly}
          onChange={(e) => setTitle(e.target.value)} placeholder="Название шага" aria-label={`Название блока ${index + 1}`} />
        <SaveMark state={state} />
        {!readOnly && (
          <span className="row-tight">
            <button className="btn btn-ghost btn-sm icon-btn" disabled={index === 0} onClick={() => onMove(-1)} aria-label="Поднять блок выше"><ArrowUp aria-hidden /></button>
            <button className="btn btn-ghost btn-sm icon-btn" disabled={index === count - 1} onClick={() => onMove(1)} aria-label="Опустить блок ниже"><ArrowDown aria-hidden /></button>
            <button className="btn btn-ghost btn-sm icon-btn" onClick={onDelete} aria-label="Удалить блок"><Trash2 aria-hidden /></button>
          </span>
        )}
      </div>
      <div className="block-body stack-sm">
        {block.type === 'text' && (
          <Field label="Текст" hint="Абзацы разделяйте пустой строкой. «# Заголовок», «## Подзаголовок», «- пункт», «1. шаг», **выделение**.">
            <textarea className="textarea" rows={7} value={data.text ?? ''} disabled={readOnly} onChange={(e) => setData({ ...data, text: e.target.value })} />
          </Field>
        )}
        {fileKind && (
          <>
            <FileStatus file={block.file} view={block.view_file} />
            {!readOnly && <UploadButton kind={block.type} label={block.file ? 'Заменить файл' : 'Загрузить файл'} primary={!block.file}
              onDone={(f) => setData({ file_id: f.id })} />}
          </>
        )}
        {block.type === 'link' && (
          <>
            <Field label="Адрес ссылки">
              <input className="input" type="url" value={data.url ?? ''} disabled={readOnly} placeholder="https://…" onChange={(e) => setData({ ...data, url: e.target.value })} />
            </Field>
            <Field label="Пояснение для сотрудника">
              <input className="input" value={data.note ?? ''} disabled={readOnly} onChange={(e) => setData({ ...data, note: e.target.value })} />
            </Field>
            <label className="check">
              <input type="checkbox" checked={!!data.mandatory} disabled={readOnly} onChange={(e) => setData({ ...data, mandatory: e.target.checked })} />
              <span>Обязательная ссылка <span className="muted small" style={{ display: 'block' }}>Для офлайн-курса обязательной ссылке нужна локальная копия материала.</span></span>
            </label>
            {block.file
              ? <FileStatus file={block.file} view={block.view_file} />
              : <Notice tone={data.mandatory ? 'danger' : 'warn'} title={data.mandatory ? 'Нужна локальная копия' : 'Без локальной копии'}>
                  {data.mandatory ? 'Прикрепите файл с содержимым ссылки, иначе курс не опубликуется.' : 'У сотрудника ссылка будет помечена «Нужен интернет».'}
                </Notice>}
            {!readOnly && (
              <div className="row">
                {/* загрузка идёт несколько секунд: берём актуальные данные блока, а не те, что были при выборе файла */}
                <UploadButton kind="any" label={block.file ? 'Заменить локальную копию' : 'Прикрепить локальную копию'} onDone={(f) => setData((d) => ({ ...d, alt_file_id: f.id }))} />
                {block.file && <button className="btn btn-ghost btn-sm" onClick={() => setData({ ...data, alt_file_id: null })}>Убрать копию</button>}
              </div>
            )}
          </>
        )}
        {block.type === 'trainer' && data.scenario && (
          <Notice tone="info" title={data.scenario.title}>
            {data.scenario.summary} Шагов диалога: {Object.values(data.scenario.nodes).filter((n) => !n.ending).length},
            развязок: {Object.values(data.scenario.nodes).filter((n) => n.ending).length}. Сценарий входит в скачиваемый пакет и работает без сети.
          </Notice>
        )}
        {/* у ссылки обязательность задаётся одним флажком «Обязательная ссылка» выше */}
        {block.type !== 'link' && (
          <label className="check" style={{ minHeight: '2.4rem' }}>
            <input type="checkbox" checked={required} disabled={readOnly} onChange={(e) => setRequired(e.target.checked)} /> Обязательный материал
          </label>
        )}
      </div>
    </div>
  )
}

// ---------- вопрос ----------
function newOptionId(): string {
  return Math.random().toString(36).slice(2, 10)
}

function QuestionCard({ q, index, count, readOnly, onMove, onDelete, onSaved, autoFocus }: {
  q: QuestionRow; index: number; count: number; readOnly: boolean; onMove: (delta: number) => void; onDelete: () => void
  onSaved: () => void; autoFocus?: boolean
}) {
  const [text, setText] = useState(q.text)
  const [options, setOptions] = useState(q.options)
  const [correct, setCorrect] = useState<string | string[] | null>(q.correct)
  const [points, setPoints] = useState(q.points)
  const [pointsText, setPointsText] = useState(String(q.points))
  const [criteria, setCriteria] = useState(q.criteria)
  const state = useAutosave({ type: q.type, text, options, correct, points, criteria }, async (v) => {
    await api.patch(`/api/questions/${q.id}`, v)
    onSaved()                                // панель «Готовность к публикации» должна видеть свежие вопросы
  }, !readOnly)

  const setOption = (id: string, value: string) => setOptions((o) => o.map((x) => (x.id === id ? { ...x, text: value } : x)))
  const moveOption = (i: number, delta: number) => setOptions((o) => {
    const next = [...o]
    const j = i + delta
    if (j < 0 || j >= next.length) return o
    ;[next[i], next[j]] = [next[j], next[i]]
    return next
  })
  const removeOption = (id: string) => {
    setOptions((o) => o.filter((x) => x.id !== id))
    setCorrect((c) => (Array.isArray(c) ? c.filter((x) => x !== id) : c === id ? null : c))
  }
  const multi = Array.isArray(correct) ? correct : []

  return (
    <div className="block-card">
      <div className="block-head">
        <span className="block-kind">Вопрос {index + 1} · {QUESTION_LABEL[q.type]}</span>
        <span className="grow" />
        <SaveMark state={state} />
        {!readOnly && (
          <span className="row-tight">
            <button className="btn btn-ghost btn-sm icon-btn" disabled={index === 0} onClick={() => onMove(-1)} aria-label="Поднять вопрос выше"><ArrowUp aria-hidden /></button>
            <button className="btn btn-ghost btn-sm icon-btn" disabled={index === count - 1} onClick={() => onMove(1)} aria-label="Опустить вопрос ниже"><ArrowDown aria-hidden /></button>
            <button className="btn btn-ghost btn-sm icon-btn" onClick={onDelete} aria-label="Удалить вопрос"><Trash2 aria-hidden /></button>
          </span>
        )}
      </div>
      <div className="block-body stack-sm">
        <Field label="Текст вопроса">
          <textarea className="textarea" style={{ minHeight: '3.6rem' }} rows={2} value={text} disabled={readOnly} autoFocus={autoFocus} onChange={(e) => setText(e.target.value)} />
        </Field>
        {q.type !== 'open' && (
          <div>
            <p className="label">{q.type === 'order' ? 'Варианты в правильном порядке' : q.type === 'single' ? 'Варианты (отметьте один верный)' : 'Варианты (отметьте все верные)'}</p>
            {q.type === 'order' && <p className="hint">Сотруднику варианты покажутся вперемешку. Засчитывается только полное совпадение порядка.</p>}
            {q.type === 'multiple' && <p className="hint">Засчитывается только точное совпадение набора.</p>}
            <div style={{ marginTop: '0.4rem' }}>
              {options.map((o, i) => (
                <div className="opt-row" key={o.id}>
                  {q.type === 'single' && <input type="radio" name={`c-${q.id}`} checked={correct === o.id} disabled={readOnly} onChange={() => setCorrect(o.id)} aria-label={`Верный вариант ${i + 1}`} style={{ width: '1.3rem', height: '1.3rem', accentColor: 'var(--brand)' }} />}
                  {q.type === 'multiple' && <input type="checkbox" checked={multi.includes(o.id)} disabled={readOnly} aria-label={`Верный вариант ${i + 1}`}
                    onChange={(e) => setCorrect(e.target.checked ? [...multi, o.id] : multi.filter((x) => x !== o.id))} style={{ width: '1.3rem', height: '1.3rem', accentColor: 'var(--brand)' }} />}
                  {q.type === 'order' && <span className="order-num" style={{ width: '1.7rem', height: '1.7rem', fontSize: '0.85rem' }}>{i + 1}</span>}
                  <input className="input" value={o.text} disabled={readOnly} onChange={(e) => setOption(o.id, e.target.value)} placeholder={`Вариант ${i + 1}`} aria-label={`Текст варианта ${i + 1}`} />
                  {!readOnly && q.type === 'order' && (
                    <>
                      <button className="btn btn-ghost btn-sm icon-btn" disabled={i === 0} onClick={() => moveOption(i, -1)} aria-label="Выше"><ArrowUp aria-hidden /></button>
                      <button className="btn btn-ghost btn-sm icon-btn" disabled={i === options.length - 1} onClick={() => moveOption(i, 1)} aria-label="Ниже"><ArrowDown aria-hidden /></button>
                    </>
                  )}
                  {!readOnly && <button className="btn btn-ghost btn-sm icon-btn" disabled={options.length <= 2} onClick={() => removeOption(o.id)} aria-label="Удалить вариант"><Trash2 aria-hidden /></button>}
                </div>
              ))}
            </div>
            {!readOnly && <button className="btn btn-ghost btn-sm" style={{ marginTop: '0.4rem' }} onClick={() => setOptions((o) => [...o, { id: newOptionId(), text: '' }])}><Plus aria-hidden /> Добавить вариант</button>}
          </div>
        )}
        {q.type === 'open' && (
          <Field label="Критерии проверки (видит только методист)" hint="Например: «2 балла — назван мастер смены и остановка работы; 1 балл — только одно из двух».">
            <textarea className="textarea" style={{ minHeight: '3.6rem' }} rows={2} value={criteria} disabled={readOnly} onChange={(e) => setCriteria(e.target.value)} />
          </Field>
        )}
        <Field label="Баллы за вопрос">
          {/* поле хранит набранный текст отдельно: его можно стереть и ввести заново, не получая «13» вместо «3» */}
          <input className="input" type="number" min={1} max={100} style={{ maxWidth: '8rem' }} value={pointsText} disabled={readOnly}
            onChange={(e) => {
              setPointsText(e.target.value)
              const n = Number.parseInt(e.target.value, 10)
              if (n >= 1 && n <= 100) setPoints(n)
            }}
            onBlur={() => setPointsText(String(points))} />
        </Field>
      </div>
    </div>
  )
}

const ADD_BLOCKS: { type: string; label: string; icon: typeof FileText }[] = [
  { type: 'text', label: 'Текст', icon: Type }, { type: 'pdf', label: 'PDF', icon: FileText }, { type: 'video', label: 'Видео', icon: Film },
  { type: 'docx', label: 'Word', icon: FileText }, { type: 'xlsx', label: 'Excel', icon: Sheet }, { type: 'pptx', label: 'PowerPoint', icon: Presentation },
  { type: 'link', label: 'Ссылка', icon: Link2 }, { type: 'trainer', label: 'Тренажёр', icon: MessagesSquare },
]

export function EditorPage() {
  const { versionId } = useParams()
  // На каждую редакцию — свой экземпляр редактора. При переходе к другой редакции (например, после
  // «Новая редакция») прежний снимается с экрана и дописывает свои несохранённые правки.
  return <Editor key={versionId} versionId={versionId ?? ''} />
}

function Editor({ versionId }: { versionId: string }) {
  const navigate = useNavigate()
  const uploads = useRef(0)
  const [v, setV] = useState<VersionFull | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [check, setCheck] = useState<CheckResult | null>(null)
  const [publishing, setPublishing] = useState(false)
  const [focusQuestion, setFocusQuestion] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const pendingType = useRef<string>('pdf')
  const [uploading, setUploading] = useState<string | null>(null)
  const [scenarios, setScenarios] = useState<Scenario[] | null>(null)

  const everLoaded = useRef(false)
  const load = useCallback(async (): Promise<boolean> => {
    try {
      setV(await api.get<VersionFull>(`/api/versions/${versionId}`))
      setError(null)
      everLoaded.current = true
      return true
    } catch (e) {
      // курс уже открыт — сбой обновления не должен заменять редактор сообщением об ошибке
      if (everLoaded.current) toast(`Не удалось обновить данные курса: ${errorText(e)}`, 'error')
      else setError(errorText(e))
      return false
    }
  }, [versionId])
  const recheck = useCallback(async () => {
    try {
      setCheck(await api.get<CheckResult>(`/api/versions/${versionId}/check`))
    } catch {
      /* проверка повторится после следующего сохранения */
    }
  }, [versionId])

  useEffect(() => {
    setV(null)
    everLoaded.current = false
    void load().then(recheck)
  }, [load, recheck])

  // сведения о курсе
  const [meta, setMeta] = useState({ title: '', sop_code: '', revision: 1, description: '', change_note: '', pass_percent: 80, max_attempts: 3, gate_test: false })
  const metaLoaded = useRef<string | null>(null)
  // Номер редакции набирается как текст: поле можно очистить и ввести заново. В данные курса он
  // попадает, только когда допустим, — неверный номер не блокирует сохранение остальных полей.
  const [revText, setRevText] = useState('1')
  useEffect(() => {
    if (v && metaLoaded.current !== v.id) {
      metaLoaded.current = v.id
      setMeta({ title: v.title, sop_code: v.sop_code, revision: v.revision, description: v.description, change_note: v.change_note,
        pass_percent: v.pass_percent, max_attempts: v.max_attempts, gate_test: v.gate_test })
      setRevText(String(v.revision))
    }
  }, [v])
  const readOnly = v?.status !== 'draft'
  const metaState = useAutosave(meta, async (m) => {
    await api.patch(`/api/versions/${versionId}`, m)
    void recheck()
  }, !!v && !readOnly && metaLoaded.current === v.id)

  if (error) {
    return (
      <Notice tone="danger" title="Курс не открылся"
        action={<button className="btn btn-secondary" onClick={() => void load().then(recheck)}>Повторить</button>}>{error}</Notice>
    )
  }
  if (!v) return <Loading />

  const addBlock = async (type: string) => {
    if (['pdf', 'video', 'docx', 'xlsx', 'pptx'].includes(type)) {
      pendingType.current = type
      if (fileInput.current) {
        fileInput.current.accept = ACCEPT[type]
        fileInput.current.click()
      }
      return
    }
    try {
      if (type === 'trainer') {
        // сценариев несколько — методист выбирает подходящий к теме курса
        const list = await api.get<Scenario[]>('/api/trainer/scenarios')
        if (!list.length) throw new Error('Нет готовых сценариев')
        if (list.length === 1) await addTrainer(list[0])
        else setScenarios(list)
        return
      }
      const title = type === 'text' ? 'Текст' : type === 'link' ? 'Полезная ссылка' : ''
      await api.post(`/api/versions/${v.id}/blocks`, { type, title, required: type === 'text', data: {} })
      await load()
      void recheck()
    } catch (e) {
      toast(errorText(e), 'error')
    }
  }
  const addTrainer = async (scenario: Scenario) => {
    setScenarios(null)
    try {
      await api.post(`/api/versions/${v.id}/blocks`, { type: 'trainer', title: `Тренажёр: ${scenario.title}`, required: false, data: { scenario } })
      await load()
      void recheck()
    } catch (e) {
      toast(errorText(e), 'error')
    }
  }
  const onFilePicked = async (file: File) => {
    const type = pendingType.current
    setUploading(type)
    try {
      const info = await whileUploading(uploads, async () => {
        const uploaded = await uploadFile(file)
        await api.post(`/api/versions/${v.id}/blocks`, { type, title: file.name.replace(/\.[^.]+$/, ''), required: type === 'pdf', data: { file_id: uploaded.id } })
        return uploaded
      })
      if (info.conversion_status === 'error') toast('Файл загружен, но копию для чтения построить не удалось — см. блок.', 'error', 9000)
      await load()
      void recheck()
    } catch (e) {
      toast(errorText(e), 'error', 9000)
    } finally {
      setUploading(null)
    }
  }
  const moveBlock = async (index: number, delta: number) => {
    await flushAll()
    const ids = v.blocks.map((b) => b.id)
    const j = index + delta
    ;[ids[index], ids[j]] = [ids[j], ids[index]]
    setV(await api.post<VersionFull>(`/api/versions/${v.id}/blocks/order`, { ids }))
  }
  const deleteBlock = async (id: string) => {
    if (!window.confirm('Удалить блок из курса?')) return
    await api.del(`/api/blocks/${id}`)
    await load()
    void recheck()
  }
  const addQuestion = async (type: QuestionRow['type']) => {
    const options = type === 'open' ? [] : [{ id: newOptionId(), text: '' }, { id: newOptionId(), text: '' }]
    try {
      const q = await api.post<QuestionRow>(`/api/versions/${v.id}/questions`, { type, text: '', options, correct: type === 'multiple' ? [] : null, points: type === 'single' ? 1 : 2, criteria: '' })
      setFocusQuestion(q.id)
      await load()
      void recheck()
    } catch (e) {
      toast(errorText(e), 'error')
    }
  }
  const moveQuestion = async (index: number, delta: number) => {
    await flushAll()
    const ids = v.questions.map((q) => q.id)
    const j = index + delta
    ;[ids[index], ids[j]] = [ids[j], ids[index]]
    setV(await api.post<VersionFull>(`/api/versions/${v.id}/questions/order`, { ids }))
  }
  const deleteQuestion = async (id: string) => {
    if (!window.confirm('Удалить вопрос?')) return
    await api.del(`/api/questions/${id}`)
    await load()
    void recheck()
  }
  // номер редакции должен быть больше номеров остальных редакций этого курса — то же правило проверяет сервер
  const otherVersions = v.versions.filter((x) => x.id !== v.id)
  const minRevision = otherVersions.reduce((m, x) => Math.max(m, x.revision + 1), 1)
  // Поля «О курсе» заполняются из загруженной редакции эффектом; до этого в состоянии лежат значения
  // по умолчанию (или прошлой редакции), и проверять по ним номер нельзя — вышла бы ложная ошибка.
  const metaReady = metaLoaded.current === v.id
  const shownRev = metaReady ? revText : String(v.revision)
  const parseRev = (text: string) => (/^\d{1,3}$/.test(text.trim()) ? Number.parseInt(text.trim(), 10) : NaN)
  // уже сохранённый номер допустим всегда — иначе черновик со старым номером нельзя было бы ни сохранить, ни исправить
  const revAllowed = (n: number) => n === v.revision || (n >= minRevision && n <= 999)
  const revError = readOnly || !metaReady || revAllowed(parseRev(revText))
    ? null
    : minRevision > 1
      ? `Введите число от ${minRevision} до 999: редакция ${minRevision - 1} у этого курса уже есть.`
      : 'Введите число от 1 до 999.'
  const onRevision = (text: string) => {
    setRevText(text)
    const n = parseRev(text)
    if (revAllowed(n)) setMeta({ ...meta, revision: n })
  }
  /** Дописывает правки; если что-то не сохранилось, объясняет это и возвращает false. */
  const saveAll = async (): Promise<boolean> => {
    if (uploads.current > 0 || uploading) {
      toast('Файл ещё загружается и проверяется. Дождитесь окончания и повторите.', 'error', 8000)
      return false
    }
    if (revError) {
      toast(`Номер редакции не сохранён. ${revError}`, 'error', 8000)
      document.getElementById('m-rev')?.focus()
      return false
    }
    if (await flushAll()) return true
    toast('Не все изменения сохранены — на сервере остались прежние данные. Исправьте ошибку и повторите.', 'error', 8000)
    return false
  }
  const openPublish = async () => {
    if (!(await saveAll())) return
    // номер редакции и название в окне публикации — уже сохранённые; без свежих данных окно не открываем
    if (!(await load())) return
    await recheck()
    setPublishing(true)
  }
  const newVersion = async () => {
    try {
      const res = await api.post<{ version_id: string }>(`/api/courses/${v.course_id}/new-version`)
      navigate(`/m/courses/${res.version_id}`)
    } catch (e) {
      toast(errorText(e), 'error')
    }
  }
  const hasDraft = v.versions.some((x) => x.status === 'draft')

  return (
    <UploadCounter.Provider value={uploads}>
    <div className="stack">
      <input ref={fileInput} type="file" className="sr-only" tabIndex={-1} aria-hidden onChange={(e) => {
        const file = e.target.files?.[0]
        e.target.value = ''
        if (file) void onFilePicked(file)
      }} />
      <div>
        <Link to="/m/courses" className="btn btn-ghost btn-sm" style={{ marginLeft: '-0.6rem' }}>← Все курсы</Link>
        <div className="page-head" style={{ marginTop: '0.3rem' }}>
          <div>
            <h1>{meta.title || v.title || 'Новый курс'}</h1>
            <p className="row"><Pill tone={VERSION_STATUS[v.status].tone}>{VERSION_STATUS[v.status].label}</Pill> Редакция {readOnly ? v.revision : meta.revision}{meta.sop_code ? ` · ${meta.sop_code}` : ''}</p>
          </div>
          <div className="row">
            {/* сначала дописываем несохранённые правки, потом открываем предпросмотр — иначе он покажет прежний текст */}
            <button className="btn btn-secondary" onClick={async () => { if (readOnly || (await saveAll())) navigate(`/m/preview/${v.id}`) }}><Eye aria-hidden /> Глазами сотрудника</button>
            {v.status === 'draft' && <button className="btn btn-primary" onClick={() => void openPublish()}><Send aria-hidden /> Опубликовать</button>}
            {v.status === 'published' && <Link className="btn btn-primary" to={`/m/courses?assign=${v.id}`}><Send aria-hidden /> Назначить</Link>}
            {v.status === 'published' && !hasDraft && <button className="btn btn-secondary" onClick={() => void newVersion()}><GitBranch aria-hidden /> Новая редакция</button>}
          </div>
        </div>
      </div>
      {readOnly && (
        <Notice tone="info" title="Опубликованная редакция не изменяется">
          Попытки и выданные документы ссылаются именно на неё. Чтобы внести правки, создайте новую редакцию.
        </Notice>
      )}

      <div className="editor">
        <div className="stack">
          <section className="card stack-sm" aria-labelledby="ed-meta">
            <div className="spread"><h2 id="ed-meta">О курсе</h2><SaveMark state={metaState} /></div>
            <div className="form-grid">
              <Field label="Название" htmlFor="m-title"><input id="m-title" className="input" value={meta.title} disabled={readOnly} onChange={(e) => setMeta({ ...meta, title: e.target.value })} /></Field>
              <Field label="Код СОП" htmlFor="m-code"><input id="m-code" className="input" value={meta.sop_code} disabled={readOnly} onChange={(e) => setMeta({ ...meta, sop_code: e.target.value })} /></Field>
              <Field label="Редакция" htmlFor="m-rev" hint="Номер редакции документа. Должен быть больше уже опубликованных.">
                <input id="m-rev" className="input" inputMode="numeric" maxLength={3} style={{ maxWidth: '8rem' }} disabled={readOnly}
                  value={readOnly ? String(v.revision) : shownRev} aria-invalid={revError ? true : undefined}
                  aria-describedby={revError ? 'm-rev-error' : undefined} onChange={(e) => onRevision(e.target.value)} />
                {revError && <p id="m-rev-error" className="error-text" role="alert">{revError}</p>}
              </Field>
            </div>
            <Field label="Описание" htmlFor="m-desc"><textarea id="m-desc" className="textarea" style={{ minHeight: '3.6rem' }} rows={2} value={meta.description} disabled={readOnly} onChange={(e) => setMeta({ ...meta, description: e.target.value })} /></Field>
            {/* поле нужно, когда у курса уже есть выпущенная редакция, — а не когда номер больше единицы */}
            {otherVersions.some((x) => x.status !== 'draft') && (
              <Field label={`Что изменилось в редакции ${readOnly ? v.revision : meta.revision}`} htmlFor="m-note" hint="Этот текст увидят сотрудники, которым назначено повторное ознакомление.">
                <textarea id="m-note" className="textarea" style={{ minHeight: '3.6rem' }} rows={2} value={meta.change_note} disabled={readOnly} onChange={(e) => setMeta({ ...meta, change_note: e.target.value })} />
              </Field>
            )}
          </section>

          <section className="card stack" aria-labelledby="ed-blocks">
            <h2 id="ed-blocks">Материалы урока</h2>
            {v.blocks.length === 0 && <p className="muted">Добавьте первый материал: текст, файл или видео.</p>}
            {v.blocks.map((b, i) => (
              <BlockCard key={b.id} block={b} index={i} count={v.blocks.length} readOnly={readOnly}
                onChanged={(nb) => { setV((cur) => (cur ? { ...cur, blocks: cur.blocks.map((x) => (x.id === nb.id ? { ...nb, position: x.position } : x)) } : cur)); void recheck() }}
                onMove={(d) => void moveBlock(i, d)} onDelete={() => void deleteBlock(b.id)} />
            ))}
            {!readOnly && (
              <div>
                <p className="label" style={{ marginBottom: '0.5rem' }}>Добавить блок</p>
                <div className="add-grid">
                  {ADD_BLOCKS.map((a) => (
                    <button key={a.type} className="btn btn-secondary" disabled={!!uploading} onClick={() => void addBlock(a.type)}>
                      {uploading === a.type ? <Spinner /> : <a.icon aria-hidden />}{uploading === a.type ? 'Загрузка…' : a.label}
                    </button>
                  ))}
                </div>
                <p className="hint" style={{ marginTop: '0.5rem' }}>Word, Excel и PowerPoint: исходник сохраняется, для чтения без сети автоматически строится PDF-копия.</p>
              </div>
            )}
          </section>

          <section className="card stack" aria-labelledby="ed-test">
            <h2 id="ed-test">Тест</h2>
            <div className="form-grid">
              <Field label="Проходной процент" htmlFor="m-pass">
                <input id="m-pass" className="input" type="number" min={1} max={100} value={meta.pass_percent} disabled={readOnly} onChange={(e) => setMeta({ ...meta, pass_percent: Number(e.target.value) || 0 })} />
              </Field>
              <Field label="Число попыток" htmlFor="m-att">
                <input id="m-att" className="input" type="number" min={1} max={20} value={meta.max_attempts} disabled={readOnly} onChange={(e) => setMeta({ ...meta, max_attempts: Number(e.target.value) || 0 })} />
              </Field>
            </div>
            <label className="check">
              <input type="checkbox" checked={meta.gate_test} disabled={readOnly} onChange={(e) => setMeta({ ...meta, gate_test: e.target.checked })} />
              Открывать тест только после изучения обязательных материалов
            </label>
            {v.questions.length === 0 && <p className="muted">Вопросов пока нет. Без теста аттестация и сертификат не формируются.</p>}
            {v.questions.map((q, i) => (
              <QuestionCard key={q.id} q={q} index={i} count={v.questions.length} readOnly={readOnly} autoFocus={focusQuestion === q.id}
                onMove={(d) => void moveQuestion(i, d)} onDelete={() => void deleteQuestion(q.id)} onSaved={() => void recheck()} />
            ))}
            {!readOnly && (
              <div>
                <p className="label" style={{ marginBottom: '0.5rem' }}>Добавить вопрос</p>
                <div className="add-grid">
                  {(['single', 'multiple', 'order', 'open'] as const).map((t) => (
                    <button key={t} className="btn btn-secondary" onClick={() => void addQuestion(t)}><Plus aria-hidden />{QUESTION_LABEL[t]}</button>
                  ))}
                </div>
              </div>
            )}
          </section>
        </div>

        <aside className="editor-side stack">
          <Readiness check={check} readOnly={readOnly} onRefresh={async () => { await flushAll(); await recheck() }} />
        </aside>
      </div>

      {scenarios && (
        <Modal title="Выберите сценарий тренажёра" size="wide" onClose={() => setScenarios(null)}>
          <div className="stack-sm">
            {scenarios.map((s) => {
              const nodes = Object.values(s.nodes)
              return (
                <button key={s.id} className="card stack-sm scenario-pick" style={{ textAlign: 'left', cursor: 'pointer' }} onClick={() => void addTrainer(s)}>
                  <b>{s.title}</b>
                  <span className="muted small">{s.summary}</span>
                  <span className="small">Шагов диалога: {nodes.filter((n) => !n.ending).length}, развязок: {nodes.filter((n) => n.ending).length} · собеседник: {s.character.name}, {s.character.role}</span>
                </button>
              )
            })}
          </div>
        </Modal>
      )}
      {publishing && check && (
        <PublishDialog version={v} check={check} onClose={() => setPublishing(false)}
          onPublished={() => navigate(`/m/courses?assign=${v.id}`)} />
      )}
    </div>
    </UploadCounter.Provider>
  )
}

function Readiness({ check, readOnly, onRefresh }: { check: CheckResult | null; readOnly: boolean; onRefresh: () => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  if (!check) return <div className="card"><Spinner label="Проверяем курс…" /></div>
  const ok = check.errors.length === 0
  return (
    <div className="card stack-sm" aria-live="polite">
      <h2>{readOnly ? 'Пакет для телефона' : 'Готовность к публикации'}</h2>
      {!readOnly && (ok
        ? <Pill tone="ok" icon={<CircleCheck aria-hidden />}>Можно публиковать</Pill>
        : <Pill tone="danger" icon={<CircleAlert aria-hidden />}>Нужно исправить: {check.errors.length}</Pill>)}
      {check.errors.map((e, i) => <ListLine key={`e${i}`} tone="danger">{e}</ListLine>)}
      {check.warnings.map((w, i) => <ListLine key={`w${i}`} tone="warn">{w}</ListLine>)}
      <hr className="divider" />
      <p className="small">
        Объём загрузки на телефон: <b>{formatBytes(check.package.total_size)}</b><br />
        {check.package.blocks} {plural(check.package.blocks, 'материал', 'материала', 'материалов')}, {check.package.questions} {plural(check.package.questions, 'вопрос', 'вопроса', 'вопросов')}, {check.package.files} {plural(check.package.files, 'файл', 'файла', 'файлов')}
      </p>
      {!readOnly && <button className="btn btn-ghost btn-sm" disabled={busy} onClick={async () => { setBusy(true); await onRefresh(); setBusy(false) }}>{busy ? <Spinner /> : null} Проверить ещё раз</button>}
    </div>
  )
}

function ListLine({ tone, children }: { tone: 'danger' | 'warn'; children: ReactNode }) {
  return (
    <p className="row-tight small" style={{ alignItems: 'flex-start', color: tone === 'danger' ? 'var(--danger)' : 'var(--amber-ink)' }}>
      {tone === 'danger' ? <CircleAlert aria-hidden width={18} style={{ flex: 'none' }} /> : <TriangleAlert aria-hidden width={18} style={{ flex: 'none' }} />}
      <span>{children}</span>
    </p>
  )
}

function PublishDialog({ version, check, onClose, onPublished }: { version: VersionFull; check: CheckResult; onClose: () => void; onPublished: () => void }) {
  const [reacquaint, setReacquaint] = useState(true)
  const [due, setDue] = useState(todayIso(7))
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<string[]>(check.errors)
  const prev = check.previous
  const publish = async () => {
    setBusy(true)
    try {
      const res = await api.post<{ moved: number; reacquainted: number }>(`/api/versions/${version.id}/publish`, {
        reacquaint, due_at: due ? new Date(`${due}T23:59:00`).toISOString() : null,
      })
      toast(`Редакция ${version.revision} опубликована.${res.reacquainted ? ` Повторное ознакомление: ${res.reacquainted}.` : ''}${res.moved ? ` Переведено на новую редакцию: ${res.moved}.` : ''}`, 'ok', 8000)
      onPublished()
    } catch (e) {
      const err = e as { errors?: string[] }
      setErrors(err.errors ?? [errorText(e)])
      setBusy(false)
    }
  }
  return (
    <Modal title={`Опубликовать редакцию ${version.revision}`} onClose={onClose} actions={<>
      <button className="btn btn-secondary" onClick={onClose} disabled={busy}>Отмена</button>
      <button className="btn btn-primary" onClick={() => void publish()} disabled={busy || errors.length > 0}>{busy ? <Spinner /> : <Send aria-hidden />} Опубликовать</button>
    </>}>
      <div className="stack">
        {errors.length > 0 && <Notice tone="danger" title="Курс не готов к публикации"><ul style={{ margin: '0.3rem 0 0', paddingLeft: '1.2rem' }}>{errors.map((e, i) => <li key={i}>{e}</li>)}</ul></Notice>}
        {errors.length === 0 && (
          <p>После публикации редакцию нельзя изменить. Объём загрузки на телефон — <b>{formatBytes(check.package.total_size)}</b>.</p>
        )}
        {check.warnings.length > 0 && <Notice tone="warn" title="Обратите внимание"><ul style={{ margin: '0.3rem 0 0', paddingLeft: '1.2rem' }}>{check.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></Notice>}
        {prev && (
          <div className="card card-flat card-tint stack-sm">
            <h3>Редакция {prev.revision} будет заменена</h3>
            <p className="small">Попытки и сертификаты по редакции {prev.revision} сохранятся и останутся привязаны к ней.</p>
            <p className="small">Ещё не сдали редакцию {prev.revision}: <b>{prev.not_passed}</b> — они получат редакцию {version.revision} автоматически.</p>
            <label className="check">
              <input type="checkbox" checked={reacquaint} onChange={(e) => setReacquaint(e.target.checked)} />
              <span>Назначить повторное ознакомление тем, кто уже сдал редакцию {prev.revision} (<b>{prev.passed}</b>)</span>
            </label>
            <Field label="Срок повторного ознакомления" htmlFor="pb-due">
              <input id="pb-due" type="date" className="input" style={{ maxWidth: '14rem' }} value={due} min={todayIso()} onChange={(e) => setDue(e.target.value)} />
            </Field>
          </div>
        )}
        <p className="muted small">Сразу после публикации откроется назначение курса сотрудникам.</p>
      </div>
    </Modal>
  )
}
