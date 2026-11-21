// Общие элементы интерфейса и форматирование.
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { CircleAlert, CircleCheck, Info, TriangleAlert, X } from 'lucide-react'
import { createStore, useStore } from '../local/store'

// ---------- форматирование ----------
const DATE = new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })
const DATETIME = new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
const TIME = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
const DAYMONTH = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' })

export function fmtDate(iso?: string | null): string {
  return iso ? DATE.format(new Date(iso)) : '—'
}
export function fmtDateTime(iso?: string | null): string {
  return iso ? DATETIME.format(new Date(iso)).replace(',', '') : '—'
}
export function fmtTime(iso?: string | null): string {
  return iso ? TIME.format(new Date(iso)) : '—'
}
export function fmtDayMonth(iso?: string | null): string {
  return iso ? DAYMONTH.format(new Date(iso)) : '—'
}
export function fmtNum(x: number | null | undefined): string {
  if (x === null || x === undefined) return '—'
  return (Math.round(x * 10) / 10).toString().replace('.', ',')
}
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  const s = Math.max(0, Math.round(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (h) return `${h} ч ${m} мин`
  if (m) return `${m} мин ${sec} с`
  return `${sec} с`
}
export function plural(n: number, one: string, few: string, many: string): string {
  const a = Math.abs(n) % 100
  const b = a % 10
  if (a > 10 && a < 20) return many
  if (b > 1 && b < 5) return few
  if (b === 1) return one
  return many
}
export function dueText(iso: string | null): { text: string; tone: 'ok' | 'warn' | 'danger' | 'none' } {
  if (!iso) return { text: 'Без срока', tone: 'none' }
  const due = new Date(iso)
  // считаем календарные дни, а не сутки: срок «сегодня в 23:59» — это сегодня, а не завтра
  const dayStart = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  const days = Math.round((dayStart(due) - dayStart(new Date())) / 86400000)
  if (due.getTime() < Date.now()) return { text: `Срок истёк ${fmtDayMonth(iso)}`, tone: 'danger' }
  if (days === 0) return { text: 'Срок — сегодня', tone: 'warn' }
  if (days === 1) return { text: 'Срок — завтра', tone: 'warn' }
  if (days <= 3) return { text: `Осталось ${days} ${plural(days, 'день', 'дня', 'дней')} — до ${fmtDayMonth(iso)}`, tone: 'warn' }
  return { text: `До ${fmtDayMonth(iso)}`, tone: 'ok' }
}
export function todayIso(offsetDays = 0): string {
  const d = new Date(Date.now() + offsetDays * 86400000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// ---------- элементы ----------
export type Tone = 'ok' | 'warn' | 'danger' | 'info' | 'brand' | 'none'

export function Pill({ tone = 'none', icon, children }: { tone?: Tone; icon?: ReactNode; children: ReactNode }) {
  return <span className={`pill ${tone !== 'none' ? `pill-${tone}` : ''}`}>{icon}{children}</span>
}

const NOTICE_ICON = { ok: CircleCheck, warn: TriangleAlert, danger: CircleAlert, info: Info }

export function Notice({ tone = 'info', title, children, action }: { tone?: 'ok' | 'warn' | 'danger' | 'info'; title?: string; children?: ReactNode; action?: ReactNode }) {
  const Icon = NOTICE_ICON[tone]
  return (
    <div className={`notice notice-${tone}`} role={tone === 'danger' ? 'alert' : 'status'}>
      <Icon aria-hidden />
      <div className="grow">
        {title && <strong>{title}</strong>}
        {children}
        {action && <div style={{ marginTop: '0.6rem' }}>{action}</div>}
      </div>
    </div>
  )
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="row-tight" role="status">
      <span className="spinner" aria-hidden />
      {label ? <span>{label}</span> : <span className="sr-only">Загрузка</span>}
    </span>
  )
}

export function Loading({ label = 'Загружаем…' }: { label?: string }) {
  return <div className="card center muted" style={{ padding: '2rem' }}><Spinner label={label} /></div>
}

/**
 * Загрузка данных с устройства. Обычно это доли секунды. Если дольше 8 секунд — хранилище держит
 * другая копия платформы (как правило, старая вкладка браузера): человеку говорится, что делать.
 */
export function LocalLoading() {
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    const t = setTimeout(() => setSlow(true), 8000)
    return () => clearTimeout(t)
  }, [])
  return (
    <div className="stack">
      <Loading />
      {slow && (
        <Notice tone="warn" title="Данные на устройстве открываются дольше обычного">
          Если платформа открыта ещё и во вкладке браузера, закройте ту вкладку — приложение продолжит само.
          Ответы и скачанные курсы при этом не теряются.
        </Notice>
      )}
    </div>
  )
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="card card-flat center" style={{ padding: '2rem 1.2rem' }}>
      <h3>{title}</h3>
      {children && <p className="muted" style={{ marginTop: '0.4rem' }}>{children}</p>}
      {action && <div style={{ marginTop: '1rem' }}>{action}</div>}
    </div>
  )
}

export function ProgressBar({ value, max, label }: { value: number; max: number; label?: string }) {
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0
  return (
    <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={label ?? 'Ход выполнения'}>
      <span style={{ width: `${pct}%` }} />
    </div>
  )
}

export function Field({ label, hint, children, htmlFor }: { label: string; hint?: string; children: ReactNode; htmlFor?: string }) {
  // без htmlFor подпись оборачивает поле целиком — так она всегда связана с ним для экранных читалок
  if (!htmlFor) {
    return (
      <div className="field">
        <label className="field">
          <span className="label">{label}</span>
          {children}
        </label>
        {hint && <span className="hint">{hint}</span>}
      </div>
    )
  }
  return (
    <div className="field">
      <label htmlFor={htmlFor}>{label}</label>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </div>
  )
}

export function Modal({ title, children, onClose, size, actions }: { title: string; children: ReactNode; onClose: () => void; size?: 'wide' | 'xwide'; actions?: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  // Обработчик закрытия хранится в ref: родитель обычно передаёт новую функцию при каждой перерисовке,
  // и если бы эффект зависел от неё, фокус каждый раз возвращался бы на окно, сбрасывая ввод.
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  // Элемент, с которого открыли окно, запоминается при первой отрисовке: к моменту эффекта фокус
  // уже может стоять на поле с autoFocus внутри окна, и вернуть его при закрытии было бы некуда.
  const opener = useRef<HTMLElement | null>(null)
  const captured = useRef(false)
  if (!captured.current) {
    captured.current = true
    opener.current = document.activeElement as HTMLElement | null
  }
  useEffect(() => {
    const prev = opener.current
    // поле внутри окна уже взяло фокус (autoFocus) — не отбираем его
    if (!ref.current?.contains(document.activeElement)) ref.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeRef.current()
    }
    document.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = ''
      prev?.focus?.()
    }
  }, [])
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${size ?? ''}`} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref}>
        <div className="spread" style={{ marginBottom: '0.8rem', flexWrap: 'nowrap' }}>
          <h2>{title}</h2>
          <button className="btn btn-ghost icon-btn" onClick={onClose} aria-label="Закрыть"><X aria-hidden /></button>
        </div>
        {children}
        {actions && <div className="modal-actions">{actions}</div>}
      </div>
    </div>
  )
}

// ---------- всплывающие сообщения ----------
interface Toast { id: number; kind: 'info' | 'ok' | 'error'; text: string }
const toastStore = createStore<{ items: Toast[] }>({ items: [] })
let toastSeq = 1

export function toast(text: string, kind: Toast['kind'] = 'info', ms = 5000): void {
  const id = toastSeq++
  toastStore.set((s) => ({ items: [...s.items, { id, kind, text }].slice(-4) }))
  setTimeout(() => toastStore.set((s) => ({ items: s.items.filter((t) => t.id !== id) })), ms)
}

export function Toasts() {
  const { items } = useStore(toastStore)
  return (
    <div className="toasts" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`}>
          {t.kind === 'ok' ? <CircleCheck aria-hidden /> : t.kind === 'error' ? <CircleAlert aria-hidden /> : <Info aria-hidden />}
          <span>{t.text}</span>
        </div>
      ))}
    </div>
  )
}

// ---------- загрузка данных ----------
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | undefined; error: string | null; loading: boolean; reload: () => void; setData: (v: T) => void } {
  const [data, setData] = useState<T | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)
  const fnRef = useRef(fn)
  fnRef.current = fn
  useEffect(() => {
    let alive = true
    setLoading(true)
    fnRef.current()
      .then((v) => {
        if (alive) {
          setData(v)
          setError(null)
        }
      })
      .catch((e: Error) => alive && setError(e.message || 'Ошибка загрузки'))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])
  const reload = useCallback(() => setTick((t) => t + 1), [])
  return { data, error, loading, reload, setData }
}

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : 'Что-то пошло не так'
}
