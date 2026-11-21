// Скачивание пакета редакции для работы без сети.
//
// Файлы качаются кусками по 1 МБ (Range), каждый кусок сразу сохраняется — обрыв не теряет
// скачанное, загрузку можно продолжить. Готовность выставляется только после проверки размера
// и SHA-256 каждого файла. Пока новая редакция не проверена, прежняя готовая копия остаётся рабочей.
import { ApiError, NetworkError, api } from '../api/client'
import type { FileRef, Manifest, PackageRow } from '../types'
import { logLocal, type UserDb } from './db'

const CHUNK = 1024 * 1024

export class DownloadError extends Error {
  kind: NonNullable<PackageRow['errorKind']>
  constructor(kind: NonNullable<PackageRow['errorKind']>, message: string) {
    super(message)
    this.kind = kind
  }
}

export interface DownloadProgress {
  doneBytes: number
  totalBytes: number
  fileName?: string
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} Б`
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} КБ`
  const mb = n / (1024 * 1024)
  return `${mb < 10 ? mb.toFixed(1).replace('.', ',') : Math.round(mb)} МБ`
}

async function sha256Hex(blob: Blob): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

function isQuota(e: unknown): boolean {
  const name = (e as { name?: string; inner?: { name?: string } })?.name
  const inner = (e as { inner?: { name?: string } })?.inner?.name
  return name === 'QuotaExceededError' || inner === 'QuotaExceededError'
}

/** Проверяет, что оболочка приложения сохранена и запустится без сети. */
export async function shellReady(timeoutMs = 8000): Promise<{ ok: boolean; reason?: string }> {
  if (!('serviceWorker' in navigator)) {
    return { ok: false, reason: 'Этот браузер не умеет запускать приложение без сети. Откройте платформу в Chrome или Safari.' }
  }
  if (!window.isSecureContext) {
    return { ok: false, reason: 'Адрес не защищён (нужен https://). Откройте платформу по защищённому адресу — см. «Подключение телефона».' }
  }
  const ready = navigator.serviceWorker.ready.then((reg) => !!reg.active)
  const timeout = new Promise<boolean>((resolve) => setTimeout(() => resolve(false), timeoutMs))
  const ok = await Promise.race([ready, timeout])
  if (!ok) return { ok: false, reason: 'Приложение ещё не сохранилось на устройстве. Обновите страницу и повторите.' }
  try {
    const hit = await caches.match('/index.html', { ignoreSearch: true })
    if (!hit) return { ok: false, reason: 'Оболочка приложения не сохранена. Обновите страницу и повторите.' }
  } catch {
    /* Cache API недоступен — полагаемся на активный Service Worker */
  }
  return { ok: true }
}

async function haveFile(db: UserDb, ref: FileRef): Promise<boolean> {
  const row = await db.files.get(ref.sha256)
  return !!row && row.size === ref.size
}

/** Загрузку остановили: пользователь — кнопкой, или приложение ушло в фон (тогда она продолжится сама). */
const stopped = (signal?: AbortSignal): DownloadError =>
  signal?.reason === 'paused'
    ? new DownloadError('paused', 'Загрузка приостановлена: приложение свёрнуто. Продолжится, когда вы вернётесь в него')
    : new DownloadError('cancelled', 'Загрузка остановлена')

// Сторож хранилища. Обычно запись в IndexedDB занимает миллисекунды. Если её держит другая копия
// платформы (старая вкладка браузера, замороженная системой), запись ждала бы бесконечно и экран
// показывал бы «Скачивается» без конца. Через 20 секунд загрузка прерывается с понятным текстом;
// всё уже скачанное остаётся, «Продолжить загрузку» продолжает с того же места.
const STORAGE_TIMEOUT = 20_000
const BLOCKED_TEXT = 'Хранилище телефона не отвечает: его держит другая копия платформы — скорее всего, вкладка в браузере. '
  + 'Закройте её и нажмите «Продолжить загрузку»'

function guard<T>(op: PromiseLike<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new DownloadError('blocked', BLOCKED_TEXT)), STORAGE_TIMEOUT)
  })
  return Promise.race([Promise.resolve(op), timeout]).finally(() => clearTimeout(timer))
}

// Файлы идущих загрузок. Пока файл качается, на него может ещё не ссылаться ни пакет, ни справочник —
// уборка мусора, запущенная в это время другим действием, не должна его удалить.
const held = new Map<string, number>()
function hold(files: FileRef[]): () => void {
  for (const f of files) held.set(f.sha256, (held.get(f.sha256) ?? 0) + 1)
  let released = false
  return () => {
    if (released) return
    released = true
    for (const f of files) {
      const n = (held.get(f.sha256) ?? 1) - 1
      if (n > 0) held.set(f.sha256, n)
      else held.delete(f.sha256)
    }
  }
}

async function fetchFile(db: UserDb, ref: FileRef, signal: AbortSignal, onBytes: (n: number) => void): Promise<void> {
  const total = Math.max(1, Math.ceil(ref.size / CHUNK))
  const existing = await guard(db.chunks.where('sha256').equals(ref.sha256).toArray())
  const have = new Map(existing.map((c) => [c.index, c.blob]))
  for (let i = 0; i < total; i++) {
    if (signal.aborted) throw stopped(signal)
    const start = i * CHUNK
    const end = Math.min(ref.size, start + CHUNK) - 1
    const expected = end - start + 1
    const cached = have.get(i)
    if (cached && cached.size === expected) {
      onBytes(expected)
      continue
    }
    let blob: Blob
    try {
      // кусок читается целиком внутри запроса: его можно остановить и он не зависнет дольше предела ожидания
      blob = await api.blob(`/api/files/${ref.id}/content`, { signal, headers: { Range: `bytes=${start}-${end}` } })
    } catch (e) {
      if (signal.aborted) throw stopped(signal)
      if (e instanceof NetworkError || (e as Error).name === 'AbortError') throw new DownloadError('network', 'Связь прервалась')
      if (e instanceof ApiError && e.status === 401) throw new DownloadError('auth', 'Нужен повторный вход')
      throw new DownloadError('other', (e as Error).message)
    }
    if (signal.aborted) throw stopped(signal)
    if (blob.size !== expected) throw new DownloadError('network', 'Файл получен не полностью')
    try {
      await guard(db.chunks.put({ sha256: ref.sha256, index: i, blob }))
    } catch (e) {
      if (e instanceof DownloadError) throw e
      throw new DownloadError(isQuota(e) ? 'quota' : 'other', isQuota(e) ? 'На устройстве не хватает места' : 'Не удалось сохранить файл')
    }
    have.set(i, blob)
    onBytes(expected)
  }
  const parts: Blob[] = []
  for (let i = 0; i < total; i++) parts.push(have.get(i)!)
  const full = new Blob(parts, { type: ref.mime })
  if (full.size !== ref.size || (await sha256Hex(full)) !== ref.sha256) {
    await db.chunks.where('sha256').equals(ref.sha256).delete()
    throw new DownloadError('integrity', `Файл «${ref.name}» повреждён при загрузке`)
  }
  try {
    await guard(db.transaction('rw', db.files, db.chunks, async () => {
      await db.files.put({ sha256: ref.sha256, blob: full, size: ref.size, mime: ref.mime })
      await db.chunks.where('sha256').equals(ref.sha256).delete()
    }))
  } catch (e) {
    if (e instanceof DownloadError) throw e
    throw new DownloadError(isQuota(e) ? 'quota' : 'other', isQuota(e) ? 'На устройстве не хватает места' : 'Не удалось сохранить файл')
  }
}

/** Контрольная проверка: все файлы на месте, размер и SHA-256 совпадают с манифестом. */
export async function verifyFiles(db: UserDb, files: FileRef[]): Promise<{ ok: boolean; bad: string[] }> {
  const bad: string[] = []
  for (const ref of files) {
    const row = await guard(db.files.get(ref.sha256))
    if (!row || row.size !== ref.size || (await sha256Hex(row.blob)) !== ref.sha256) bad.push(ref.name)
  }
  return { ok: bad.length === 0, bad }
}

async function ensureSpace(need: number): Promise<void> {
  if (need <= 0 || !navigator.storage?.estimate) return
  try {
    const { quota = 0, usage = 0 } = await navigator.storage.estimate()
    const free = quota - usage
    if (quota > 0 && free < need * 1.1) {
      throw new DownloadError('quota', `На устройстве не хватает места: нужно ${formatBytes(need)}, свободно ${formatBytes(Math.max(0, free))}`)
    }
  } catch (e) {
    if (e instanceof DownloadError) throw e
  }
}

async function downloadFiles(db: UserDb, files: FileRef[], signal: AbortSignal,
  onProgress: (p: DownloadProgress) => void, base: number, totalBytes: number): Promise<void> {
  let done = base
  const missing: FileRef[] = []
  for (const ref of files) {
    if (await haveFile(db, ref)) done += ref.size
    else missing.push(ref)
  }
  onProgress({ doneBytes: done, totalBytes })
  await ensureSpace(missing.reduce((s, f) => s + f.size, 0))
  try {
    // Некоторые браузеры спрашивают разрешение у человека и не отвечают, пока он не решит.
    // Загрузку (а с ней удаление курса, выход и очистку устройства) это держать не должно.
    await Promise.race([navigator.storage?.persist?.(), new Promise((resolve) => setTimeout(resolve, 3000))])
  } catch {
    /* браузер может отказать — это не ошибка */
  }
  for (const ref of missing) {
    await fetchFile(db, ref, signal, (n) => {
      done += n
      onProgress({ doneBytes: Math.min(done, totalBytes), totalBytes, fileName: ref.name })
    })
  }
}

/** Скачивает (или докачивает) пакет редакции курса и проверяет его. */
export async function downloadPackage(db: UserDb, versionId: string, signal: AbortSignal,
  onProgress: (p: DownloadProgress) => void): Promise<PackageRow> {
  const prev = await guard(db.packages.get(versionId))
  const save = (patch: Partial<PackageRow>): Promise<PackageRow> => guard(db.transaction('rw', db.packages, async () => {
    const cur = (await db.packages.get(versionId)) ?? { versionId, status: 'downloading' as const, doneBytes: 0, totalBytes: 0 }
    const next = { ...cur, ...patch }
    await db.packages.put(next)
    return next
  }))
  let manifest: Manifest | undefined
  let done = 0
  let rowWritten = false
  let release = (): void => undefined
  try {
    const shell = await shellReady()
    if (!shell.ok) throw new DownloadError('other', shell.reason ?? 'Приложение не готово к работе без сети')
    try {
      manifest = await api.get<Manifest>(`/api/me/packages/${versionId}`, signal)
    } catch (e) {
      if (signal.aborted) throw stopped(signal)
      if (e instanceof NetworkError || (e as Error).name === 'AbortError') throw new DownloadError('network', 'Связь прервалась')
      if (e instanceof ApiError && e.status === 401) throw new DownloadError('auth', 'Нужен повторный вход')
      throw new DownloadError('other', (e as Error).message)
    }
    const total = manifest.total_size
    const contentBytes = total - manifest.files.reduce((s, f) => s + f.size, 0)
    // Прежняя проверенная копия остаётся рабочей всё время обновления: статус «готово» не снимается,
    // новый манифест лежит рядом в pending. Если приложение закроют посреди загрузки, курс
    // по-прежнему откроется без сети.
    const keepReady = prev?.status === 'ready' && !!prev.manifest
    release = hold(manifest.files)
    await save({ status: keepReady ? 'ready' : 'downloading', pending: manifest, totalBytes: total, doneBytes: contentBytes, error: undefined, errorKind: undefined })
    rowWritten = true
    let savedAt = contentBytes
    await downloadFiles(db, manifest.files, signal, (p) => {
      done = p.doneBytes
      // Ход загрузки записывается в базу примерно каждые 8 МБ: если приложение закроют посреди
      // скачивания, экран покажет, сколько на самом деле занято. update() не воссоздаёт удалённую
      // строку и не трогает статус и манифест.
      if (p.doneBytes - savedAt >= 8 * CHUNK) {
        savedAt = p.doneBytes
        void db.packages.update(versionId, { doneBytes: p.doneBytes }).catch(() => undefined)
      }
      onProgress(p)
    }, contentBytes, total)
    const check = await verifyFiles(db, manifest.files)
    if (!check.ok) {
      for (const ref of manifest.files) if (check.bad.includes(ref.name)) await guard(db.files.delete(ref.sha256))
      throw new DownloadError('integrity', `Проверка не пройдена: ${check.bad.join(', ')}`)
    }
    // Загрузку остановили или курс удалили с устройства, пока шла проверка: готовым его не объявляем.
    // «Готово без сети» ставится только здесь — после проверки размера и SHA-256 каждого файла пакета.
    if (signal.aborted || !(await guard(db.packages.get(versionId)))) throw stopped(signal)
    const row = await save({ status: 'ready', manifest, pending: undefined, doneBytes: total, totalBytes: total,
      verifiedAt: new Date().toISOString(), error: undefined, errorKind: undefined })
    release()
    // журнал и уборка старых файлов — служебные; курс уже готов, их задержка не должна держать экран
    await guard(logLocal(db, 'download', `Пакет «${manifest.title}», редакция ${manifest.revision}: скачан и проверен (${formatBytes(total)}).`)).catch(() => undefined)
    await guard(collectGarbage(db)).catch(() => undefined)
    onProgress({ doneBytes: total, totalBytes: total })
    return row
  } catch (e) {
    release()
    const err = e instanceof DownloadError ? e : new DownloadError('other', (e as Error).message || 'Не удалось скачать')
    // хранилище не отвечает — записать в него состояние тоже не выйдет; сообщение покажет экран
    if (err.kind === 'blocked') throw err
    // Состояние берём из базы, а не из снимка на момент старта: за время загрузки пакет могли удалить
    // (удаление курса, очистка устройства). Удалённую строку не воссоздаём — иначе курс «воскрес» бы.
    let cur: PackageRow | undefined
    try {
      cur = await guard(db.packages.get(versionId))
    } catch {
      throw err
    }
    if (!cur && rowWritten) throw err
    const total = manifest?.total_size ?? cur?.totalBytes ?? 0
    // Сбой мог случиться раньше, чем загрузка пересчитала уже скачанное (например, не пришёл манифест
    // при продолжении). Тогда верим прежнему значению из базы, а не обнуляем его.
    const doneNow = done > 0 ? done : Math.min(cur?.doneBytes ?? 0, total)
    const left = Math.max(0, total - doneNow)
    // если есть прежняя проверенная копия — она остаётся рабочей
    const hasOld = cur?.status === 'ready' && !!cur.manifest
    const tail = total && ['network', 'cancelled', 'paused'].includes(err.kind) ? `. Осталось скачать ${formatBytes(left)}.` : ''
    try {
      const row = await save({
        status: hasOld ? 'ready' : 'partial',
        doneBytes: doneNow, totalBytes: total, errorKind: err.kind, error: `${err.message}${tail}`,
      })
      await logLocal(db, 'download', `Загрузка прервана: ${row.error}`)
    } catch {
      /* состояние не записалось — экран всё равно покажет ошибку из памяти */
    }
    throw err
  }
}

/**
 * Курс, отмеченный «Готово без сети», должен лежать на устройстве целиком. Файлы могли пропасть:
 * браузер очистил хранилище при нехватке места, данные частично удалили. Такой курс снова
 * становится «скачанным не полностью», и «Продолжить загрузку» докачивает недостающее.
 * Сверяются наличие и размер файлов — без пересчёта контрольных сумм, поэтому проверка быстрая.
 */
export async function auditPackages(db: UserDb): Promise<number> {
  let broken = 0
  try {
    const ready = await guard(db.packages.where('status').equals('ready').toArray())
    for (const p of ready) {
      const files = p.manifest?.files ?? []
      if (!files.length) continue
      const rows = await guard(db.files.bulkGet(files.map((f) => f.sha256)))
      const ok = (i: number) => !!rows[i] && rows[i]!.size === files[i].size
      const missing = files.filter((_, i) => !ok(i))
      if (!missing.length) continue
      broken += 1
      const contentBytes = p.totalBytes - files.reduce((s, f) => s + f.size, 0)
      const present = files.reduce((s, f, i) => s + (ok(i) ? f.size : 0), 0)
      await guard(db.packages.update(p.versionId, {
        status: 'partial', doneBytes: Math.max(0, contentBytes) + present, errorKind: 'integrity',
        error: `На устройстве не хватает файлов курса (${missing.map((f) => f.name).join(', ')}). Нажмите «Продолжить загрузку»`,
      }))
      await logLocal(db, 'download', `Пакет «${p.manifest?.title}»: не хватает файлов, отметка «Готово без сети» снята.`)
    }
  } catch {
    /* проверка повторится при следующем открытии */
  }
  return broken
}

/** Удаляет файлы, на которые не ссылается ни один пакет и ни один сохранённый справочник. */
export async function collectGarbage(db: UserDb): Promise<void> {
  const keep = new Set<string>()
  for (const p of await db.packages.toArray()) {
    for (const f of p.manifest?.files ?? []) keep.add(f.sha256)
    for (const f of p.pending?.files ?? []) keep.add(f.sha256)
  }
  for (const pin of await db.libraryPins.toArray()) {
    keep.add(pin.item.file.sha256)
    if (pin.item.view) keep.add(pin.item.view.sha256)
  }
  for (const sha of held.keys()) keep.add(sha)       // файлы загрузок, которые идут прямо сейчас
  const all = await db.files.toCollection().primaryKeys()
  const drop = all.filter((k) => !keep.has(k))
  if (drop.length) await db.files.bulkDelete(drop)
  const chunkKeys = await db.chunks.toCollection().primaryKeys()
  const dropChunks = chunkKeys.filter(([sha]) => !keep.has(sha))
  if (dropChunks.length) await db.chunks.bulkDelete(dropChunks)
}

/** Удаляет пакет курса с устройства (ответы и очередь отправки не затрагиваются). */
export async function removePackage(db: UserDb, versionId: string): Promise<void> {
  await db.packages.delete(versionId)
  await collectGarbage(db)
}

/**
 * Сохраняет справочник библиотеки на устройство. `commit` записывает отметку «сохранено»:
 * он выполняется, пока файлы ещё защищены от уборки мусора.
 */
export async function downloadLibraryFiles(db: UserDb, refs: FileRef[], signal: AbortSignal,
  onProgress: (p: DownloadProgress) => void, commit?: () => Promise<unknown>): Promise<void> {
  const unique = [...new Map(refs.map((r) => [r.sha256, r])).values()]
  const total = unique.reduce((s, f) => s + f.size, 0)
  const release = hold(unique)
  try {
    await downloadFiles(db, unique, signal, onProgress, 0, total)
    const check = await verifyFiles(db, unique)
    if (!check.ok) throw new DownloadError('integrity', 'Файл повреждён при загрузке')
    if (signal.aborted) throw stopped()
    await commit?.()
  } finally {
    release()
  }
}

export async function fileBlob(db: UserDb, sha256: string): Promise<Blob | undefined> {
  return (await db.files.get(sha256))?.blob
}
