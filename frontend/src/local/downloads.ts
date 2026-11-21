// Состояние идущих загрузок: живёт вне экранов, поэтому переход между страницами не прерывает скачивание.
//
// Уход приложения в фон загрузку приостанавливает (страница в фоне не пишет в базу — см. lifecycle.ts),
// а возврат на экран продолжает её с того же места: скачанные куски остаются в базе.
import type { UserDb } from './db'
import { DownloadError, downloadPackage, removePackage, type DownloadProgress } from './download'
import { onLifecycle } from './lifecycle'
import { createStore, useStore } from './store'

export { auditPackages } from './download'

export interface DownloadState {
  running: boolean
  doneBytes: number
  totalBytes: number
  fileName?: string
  error?: string
  errorKind?: string
}

const store = createStore<{ items: Record<string, DownloadState> }>({ items: {} })
const controllers = new Map<string, AbortController>()
const tasks = new Map<string, Promise<boolean>>()
const params = new Map<string, { db: UserDb; total: number }>()     // чтобы продолжить после паузы

function patch(versionId: string, p: Partial<DownloadState>): void {
  store.set((s) => ({
    items: { ...s.items, [versionId]: { ...(s.items[versionId] ?? { running: false, doneBytes: 0, totalBytes: 0 }), ...p } },
  }))
}

function forget(versionId: string): void {
  store.set((s) => {
    const items = { ...s.items }
    delete items[versionId]
    return { items }
  })
}

export function useDownload(versionId: string | undefined): DownloadState | undefined {
  const { items } = useStore(store)
  return versionId ? items[versionId] : undefined
}

async function run(db: UserDb, versionId: string, signal: AbortSignal): Promise<boolean> {
  try {
    await downloadPackage(db, versionId, signal, (p: DownloadProgress) => patch(versionId, { ...p }))
    patch(versionId, { running: false })
    return true
  } catch (e) {
    const err = e as DownloadError
    // В строке пакета сообщение полнее: там указано, сколько осталось скачать. Если хранилище
    // не отвечает (или страница уже в фоне), строку не ждём — хватит сообщения самой ошибки.
    const row = err.kind === 'blocked' || err.kind === 'paused'
      ? undefined
      : await Promise.race([
        db.packages.get(versionId).catch(() => undefined),
        new Promise<undefined>((resolve) => setTimeout(() => resolve(undefined), 3000)),
      ])
    patch(versionId, { running: false, error: row?.error ?? err.message, errorKind: err.kind })
    return false
  }
}

/** Запускает загрузку пакета. Возвращает true, когда курс скачан и проверен; false — если не удалось или она уже идёт. */
export function startDownload(db: UserDb, versionId: string, totalHint = 0): Promise<boolean> {
  if (tasks.has(versionId)) return Promise.resolve(false)
  const ctrl = new AbortController()
  controllers.set(versionId, ctrl)
  params.set(versionId, { db, total: totalHint })
  patch(versionId, { running: true, error: undefined, errorKind: undefined, doneBytes: 0, totalBytes: totalHint })
  const task = run(db, versionId, ctrl.signal).finally(() => {
    controllers.delete(versionId)
    tasks.delete(versionId)
    params.delete(versionId)
  })
  tasks.set(versionId, task)
  return task
}

/** Останавливает загрузку по просьбе пользователя; скачанное остаётся, её можно продолжить. */
export function cancelDownload(versionId: string): void {
  controllers.get(versionId)?.abort()
}

/** Останавливает загрузку и дожидается, пока она действительно закончит писать в базу. */
export async function stopDownload(versionId: string): Promise<void> {
  cancelDownload(versionId)
  await tasks.get(versionId)?.catch(() => undefined)
}

/**
 * Удаляет курс с устройства. Сначала останавливается его загрузка (или обновление): иначе она
 * дописала бы файлы и вернула только что удалённый курс обратно.
 */
export async function removeCourse(db: UserDb, versionId: string): Promise<void> {
  await stopDownload(versionId)
  await removePackage(db, versionId)
  forget(versionId)
}

// Загрузки вне пакетов курсов (справочники библиотеки). Их тоже должны останавливать выход и очистка устройства.
const others = new Set<{ ctrl: AbortController; task: Promise<unknown> }>()

export function trackedDownload<T>(fn: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const ctrl = new AbortController()
  const work = fn(ctrl.signal)
  const entry = { ctrl, task: work.catch(() => undefined) }
  others.add(entry)
  void entry.task.then(() => others.delete(entry))
  return work
}

/** Перед выходом и очисткой устройства: останавливает все загрузки, ждёт их завершения и забывает состояние. */
export async function resetDownloads(): Promise<void> {
  paused.clear()
  controllers.forEach((c) => c.abort())
  others.forEach((o) => o.ctrl.abort())
  await Promise.allSettled([...tasks.values(), ...[...others].map((o) => o.task)])
  store.set({ items: {} })
}

// Загрузки, приостановленные уходом приложения в фон: продолжатся при возврате на экран.
const paused = new Map<string, { db: UserDb; total: number }>()

onLifecycle({
  name: 'downloads',
  suspend: async () => {
    for (const [versionId, ctrl] of controllers) {
      const p = params.get(versionId)
      if (p) paused.set(versionId, p)
      ctrl.abort('paused')
    }
    // сохранение справочника библиотеки не продолжается само: человек нажмёт «Сохранить» ещё раз
    others.forEach((o) => o.ctrl.abort('paused'))
    await Promise.allSettled([...tasks.values(), ...[...others].map((o) => o.task)])
  },
  resume: () => {
    const list = [...paused.entries()]
    paused.clear()
    for (const [versionId, p] of list) void startDownload(p.db, versionId, p.total)
  },
})
