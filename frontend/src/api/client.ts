// Обращение к REST API. Различает: нет сети, нужна авторизация, отказ сервера.
import { setReachable } from '../local/net'

export class ApiError extends Error {
  status: number
  code: string
  errors?: string[]
  constructor(status: number, code: string, message: string, errors?: string[]) {
    super(message)
    this.status = status
    this.code = code
    this.errors = errors
  }
}

export class NetworkError extends Error {
  constructor() {
    super('Нет связи с сервером')
  }
}

let token: string | null = null
let onUnauthorized: ((failedToken: string | null) => void) | null = null

export function setToken(t: string | null): void {
  token = t
}
export function getToken(): string | null {
  return token
}
/** Обработчик получает токен, с которым ушёл отвергнутый запрос: к этому моменту в приложении уже может быть новый. */
export function setUnauthorizedHandler(fn: (failedToken: string | null) => void): void {
  onUnauthorized = fn
}

const TIMEOUT_JSON = 20_000        // обычный запрос
const TIMEOUT_FILE = 120_000       // кусок файла или документ
const TIMEOUT_UPLOAD = 600_000     // загрузка материала вместе с конвертацией

interface RequestOpts {
  signal?: AbortSignal
  headers?: Record<string, string>
  timeout?: number
  /** Читать тело как файл внутри запроса: предел ожидания и отмена действуют, пока тело не получено целиком. */
  blob?: boolean
}

async function request(method: string, path: string, body?: unknown, opts: RequestOpts = {}): Promise<Response> {
  return (await send(method, path, body, opts)).res
}

async function send(method: string, path: string, body: unknown, opts: RequestOpts): Promise<{ res: Response; blob?: Blob }> {
  // запрос отменён ещё до отправки (например, загрузку остановили между двумя кусками файла)
  if (opts.signal?.aborted) throw new DOMException('Запрос отменён', 'AbortError')
  const headers: Record<string, string> = { ...(opts.headers ?? {}) }
  const used = token
  if (used) headers.Authorization = `Bearer ${used}`
  let payload: BodyInit | undefined
  if (body instanceof FormData) payload = body
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  // Сервер может не отвечать совсем (ноутбук уснул, сеть «висит»): без предела ожидания вход,
  // выход и скачивание зависли бы навсегда. По истечении срока запрос считается сетевой ошибкой.
  const ctrl = new AbortController()
  const limit = opts.timeout ?? (body instanceof FormData ? TIMEOUT_UPLOAD : TIMEOUT_JSON)
  let timer = setTimeout(() => ctrl.abort(), limit)
  const onOuterAbort = () => ctrl.abort()
  opts.signal?.addEventListener('abort', onOuterAbort)
  let res: Response
  let blob: Blob | undefined
  try {
    res = await fetch(path, { method, headers, body: payload, signal: ctrl.signal, cache: 'no-store' })
    // Заголовки приходят сразу, а тело файла может идти долго. Пока оно читается, отмена остаётся
    // в силе, а предел ожидания считается от последней полученной порции: большой файл по медленной
    // сети может идти сколько угодно, но замершая передача обрывается.
    if (opts.blob && (res.ok || res.status === 206)) {
      if (!res.body) blob = await res.blob()
      else {
        const reader = res.body.getReader()
        const parts: BlobPart[] = []
        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          parts.push(value)
          clearTimeout(timer)
          timer = setTimeout(() => ctrl.abort(), limit)
        }
        blob = new Blob(parts, { type: res.headers.get('Content-Type') ?? '' })
      }
    }
  } catch (e) {
    if (opts.signal?.aborted) throw new DOMException('Запрос отменён', 'AbortError')
    setReachable(false)
    throw new NetworkError()
  } finally {
    clearTimeout(timer)
    opts.signal?.removeEventListener('abort', onOuterAbort)
  }
  setReachable(true)
  if (res.ok || res.status === 206) return { res, blob }
  let detail: { code?: string; message?: string; errors?: string[] } = {}
  try {
    const data = await res.json()
    detail = typeof data.detail === 'object' && data.detail ? data.detail : { message: String(data.detail ?? '') }
    if (Array.isArray(data.detail)) detail = { message: 'Проверьте заполнение полей' }
  } catch {
    /* тело не JSON */
  }
  if (res.status === 401 && detail.code !== 'bad_credentials') onUnauthorized?.(used)
  throw new ApiError(res.status, detail.code ?? '', detail.message || `Ошибка сервера (${res.status})`, detail.errors)
}

export const api = {
  async get<T>(path: string, signal?: AbortSignal): Promise<T> {
    return (await request('GET', path, undefined, { signal })).json() as Promise<T>
  },
  async post<T>(path: string, body?: unknown): Promise<T> {
    return (await request('POST', path, body ?? {})).json() as Promise<T>
  },
  async patch<T>(path: string, body: unknown): Promise<T> {
    return (await request('PATCH', path, body)).json() as Promise<T>
  },
  async del<T>(path: string): Promise<T> {
    return (await request('DELETE', path)).json() as Promise<T>
  },
  async upload<T>(path: string, form: FormData): Promise<T> {
    return (await request('POST', path, form)).json() as Promise<T>
  },
  /** Файл или его кусок (с заголовком Range) целиком: отмена и предел ожидания действуют до конца передачи. */
  async blob(path: string, opts: { signal?: AbortSignal; headers?: Record<string, string> } = {}): Promise<Blob> {
    return (await send('GET', path, undefined, { ...opts, timeout: TIMEOUT_FILE, blob: true })).blob!
  },
}

/** Скачивает файл через API (с токеном) и отдаёт пользователю: открыть в новой вкладке или сохранить. */
export async function openBlob(path: string, filename?: string, download = false): Promise<void> {
  const blob = await api.blob(path)
  presentBlob(blob, filename, download)
}

export function presentBlob(blob: Blob, filename?: string, download = false): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  if (download && filename) a.download = filename
  else a.target = '_blank'
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 120000)
}

export function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') q.set(k, String(v))
  }
  const s = q.toString()
  return s ? `?${s}` : ''
}
