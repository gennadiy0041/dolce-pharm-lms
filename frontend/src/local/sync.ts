// Очередь отправки на странице: кладём записи в IndexedDB и отправляем автоматически —
// при появлении сервера, при возврате в приложение, сразу после сохранения и по таймеру.
import type Dexie from 'dexie'
import { logLocal, uuid, type UserDb } from './db'
import { isActive, onLifecycle } from './lifecycle'
import { checkServer, netStore, onNetChange } from './net'
import { createStore, useStore } from './store'
import { pullState, sendOutbox, currentAccount, type SyncResult } from './syncCore'

export interface SyncState {
  phase: 'idle' | 'sending' | 'offline' | 'auth' | 'error'
  message: string
  lastResult: SyncResult | null
  lastSyncAt: string | null
}

export const syncStore = createStore<SyncState>({ phase: 'idle', message: '', lastResult: null, lastSyncAt: null })
export const useSync = (): SyncState => useStore(syncStore)

let activeDb: UserDb | null = null
let activeUserId: string | undefined
let onAuthLost: (() => void) | null = null
let inFlight: Promise<SyncResult | null> | null = null

/** Сессия на сервере завершена (токен истёк или отозван): сообщаем приложению, чтобы оно попросило войти. */
export function setAuthLostHandler(fn: () => void): void {
  onAuthLost = fn
}

async function noticeAuthLost(): Promise<void> {
  const account = await currentAccount()
  if (account && account.userId === activeUserId && !account.token) onAuthLost?.()
}
let again: string | null = null
let timer: ReturnType<typeof setInterval> | null = null
const cleanups: (() => void)[] = []

/** Добавляет запись в очередь. Вызывается внутри транзакции Dexie, если нужно сохранить её вместе с данными. */
export async function enqueueTx(db: UserDb, type: string, payload: Record<string, unknown>, deviceTime?: string): Promise<string> {
  const id = uuid()
  await db.outbox.add({ id, type, payload, deviceTime: deviceTime ?? new Date().toISOString(), createdAt: Date.now(), tries: 0 })
  return id
}

/** Добавляет запись и сразу пробует отправить. */
export async function enqueue(db: UserDb, type: string, payload: Record<string, unknown>, deviceTime?: string): Promise<void> {
  await enqueueTx(db, type, payload, deviceTime)
  kick('enqueue')
}

/** Просит браузер разбудить Service Worker, когда появится связь, даже если приложение закрыто. */
export function registerBackgroundSync(): void {
  if (!('serviceWorker' in navigator)) return
  navigator.serviceWorker.ready
    .then((reg) => (reg as ServiceWorkerRegistration & { sync?: { register(tag: string): Promise<void> } }).sync?.register('lms-outbox'))
    .catch(() => undefined)
}

export function kick(trigger: string): void {
  registerBackgroundSync()
  void requestSync(trigger)
}

async function runSync(db: UserDb, userId: string | undefined, trigger: string): Promise<SyncResult | null> {
  const pending = await db.outbox.count()
  if (pending === 0) {
    if (syncStore.get().phase !== 'idle') syncStore.set({ phase: 'idle', message: '' })
    return null
  }
  if (netStore.get().reachable === false && trigger !== 'manual') {
    syncStore.set({ phase: 'offline', message: 'Нет связи с сервером' })
    return null
  }
  syncStore.set({ phase: 'sending', message: 'Отправляем…' })
  const res = await sendOutbox({
    trigger,
    appState: document.visibilityState === 'visible' ? 'visible' : 'hidden',
    restoredAt: netStore.get().restoredAt,
    userId,
  })
  if (res.status === 'ok' || res.status === 'empty') {
    syncStore.set({ phase: 'idle', message: '', lastResult: res, lastSyncAt: new Date().toISOString() })
  } else if (res.status === 'offline') {
    syncStore.set({ phase: 'offline', message: 'Нет связи с сервером', lastResult: res })
    void checkServer()
  } else if (res.status === 'auth') {
    syncStore.set({ phase: 'auth', message: 'Чтобы отправить результаты, войдите заново', lastResult: res })
    await noticeAuthLost()
  } else if (res.status === 'server') {
    syncStore.set({ phase: 'error', message: res.message ?? 'Ошибка сервера', lastResult: res })
  } else {
    syncStore.set({ phase: 'idle', message: '' })
  }
  return res
}

export function requestSync(trigger: string): Promise<SyncResult | null> {
  if (!activeDb) return Promise.resolve(null)
  if (!isActive()) {
    // страница в фоне и к базе не обращается: очередь отправит Service Worker со своим соединением
    registerBackgroundSync()
    return Promise.resolve(null)
  }
  if (inFlight) {
    again = trigger
    return Promise.resolve(null)
  }
  const mine: Promise<SyncResult | null> = runSync(activeDb, activeUserId, trigger)
    .catch(() => {
      syncStore.set({ phase: 'error', message: 'Не удалось отправить. Повторим автоматически.' })
      return null
    })
    .finally(() => {
      if (inFlight === mine) inFlight = null
      if (again) {
        const t = again
        again = null
        void requestSync(t)
      }
    })
  inFlight = mine
  return mine
}

/**
 * Дожидается отправки, которая идёт прямо сейчас, и делает ещё одну. Нужно перед выходом:
 * иначе запрос, ушедший секундой раньше, вернулся бы уже после отзыва токена.
 */
export async function flushSync(trigger = 'manual'): Promise<SyncResult | null> {
  // Перед каждой попыткой дожидаемся отправки этой вкладки: тогда requestSync действительно запускает
  // новую, а не «присоединяется» к идущей (в этом случае он вернул бы null, и ждать было бы нечего).
  const settle = async () => {
    for (let i = 0; i < 5 && inFlight; i++) {
      again = null
      await inFlight
    }
  }
  await settle()
  let res = await requestSync(trigger)
  // очередь может отправлять не эта вкладка, а Service Worker или соседняя вкладка — тогда ответ «занято».
  // Ждём до пяти секунд, чтобы выход не отозвал токен посреди чужой отправки.
  for (let i = 0; i < 10 && res?.status === 'busy'; i++) {
    await new Promise((resolve) => setTimeout(resolve, 500))
    await settle()
    res = await requestSync(trigger)
  }
  return res
}

/** Обновляет назначения и результаты с сервера (когда очередь пуста и связь есть). */
export async function refreshFromServer(): Promise<boolean> {
  if (!isActive()) return false              // в фоне база закрыта; обновим при возврате на экран
  const account = await currentAccount()
  if (!account || (activeUserId && account.userId !== activeUserId)) return false
  const ok = await pullState(account)
  if (!ok) await noticeAuthLost()         // сервер мог ответить «сессия завершена»
  return ok
}

export function startSyncManager(db: UserDb, userId?: string): void {
  stopSyncManager()
  activeDb = db
  activeUserId = userId
  cleanups.push(onNetChange((ok, justRestored) => {
    if (ok) void requestSync(justRestored ? 'online' : 'timer')
    else syncStore.set({ phase: 'offline', message: 'Нет связи с сервером' })
  }))
  // Возврат на экран: база снова открыта — отправляем сразу. Уход в фон: дожидаемся уже идущей
  // отправки (её записи в базу короткие), а остальное поручаем Service Worker.
  cleanups.push(onLifecycle({
    name: 'sync',
    suspend: async () => {
      const current = inFlight
      if (current) await Promise.race([current, new Promise((resolve) => setTimeout(resolve, 3000))])
      registerBackgroundSync()
    },
    resume: () => {
      void requestSync('visible')
    },
  }))
  const onFocus = () => void requestSync('visible')
  window.addEventListener('focus', onFocus)
  cleanups.push(() => window.removeEventListener('focus', onFocus))
  timer = setInterval(() => {
    if (document.visibilityState === 'visible') void requestSync('timer')
  }, 15000)
  if (isActive()) {
    void logLocal(db, 'app', 'Приложение открыто.')
    void requestSync('startup')
  }
  registerBackgroundSync()
}

export function stopSyncManager(): void {
  cleanups.splice(0).forEach((fn) => fn())
  if (timer) clearInterval(timer)
  timer = null
  activeDb = null
  activeUserId = undefined
  syncStore.set({ phase: 'idle', message: '', lastResult: null })
}

export type { Dexie }
