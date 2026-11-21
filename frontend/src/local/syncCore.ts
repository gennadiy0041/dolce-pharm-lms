// Отправка очереди на сервер. Модуль не зависит от DOM: его вызывают и страница, и Service Worker
// (Background Sync). Одновременно работает только одна отправка — через Web Locks.
import { deviceDb, getDeviceId, kvGet, logLocal, userDb, type Account } from './db'
import type { OutboxEvent, ServerState } from '../types'

export type SyncStatus = 'ok' | 'empty' | 'offline' | 'auth' | 'server' | 'nouser' | 'busy'

export interface SyncResult {
  status: SyncStatus
  accepted: number
  rejected: number
  left: number
  message?: string
}

export interface SyncOptions {
  trigger: string
  appState: 'visible' | 'hidden' | 'closed'
  restoredAt?: number | null
  /** Чья очередь отправляется. Страница передаёт своего пользователя: если в другой вкладке
   *  вошёл кто-то ещё, чужая очередь от его имени не уйдёт. */
  userId?: string
}

const BATCH = 100
const TIMEOUT_MS = 20000

export async function currentAccount(): Promise<Account | undefined> {
  const userId = await kvGet<string>('currentUserId')
  if (!userId) return undefined
  return deviceDb.accounts.get(userId)
}

/**
 * Сервер отверг токен. Стираем его, только если в базе устройства всё ещё лежит он же:
 * в соседней вкладке за это время могли войти заново, и новый токен трогать нельзя.
 */
export async function dropToken(userId: string, failed: string | null): Promise<void> {
  await deviceDb.transaction('rw', deviceDb.accounts, async () => {
    const row = await deviceDb.accounts.get(userId)
    if (row && row.token && row.token === failed) await deviceDb.accounts.update(userId, { token: null })
  })
}

async function timedFetch(url: string, init: RequestInit, ms = TIMEOUT_MS): Promise<Response> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), ms)
  try {
    return await fetch(url, { ...init, signal: ctrl.signal, cache: 'no-store' })
  } finally {
    clearTimeout(timer)
  }
}

async function withLock<T>(fn: () => Promise<T>, busy: T): Promise<T> {
  const locks = (globalThis.navigator as Navigator | undefined)?.locks
  if (!locks) return fn()
  return locks.request('dolce-lms-sync', { ifAvailable: true }, async (lock) => (lock ? fn() : busy)) as Promise<T>
}

/** Запрашивает у сервера свежие назначения и результаты и кладёт их в локальную базу. */
export async function pullState(account: Account): Promise<boolean> {
  if (!account.token) return false
  try {
    const res = await timedFetch('/api/me/state', { headers: { Authorization: `Bearer ${account.token}` } })
    if (res.status === 401) {
      await dropToken(account.userId, account.token)
      return false
    }
    if (!res.ok) return false
    const value = (await res.json()) as ServerState
    await userDb(account.userId).state.put({ key: 'server', value, savedAt: new Date().toISOString() })
    return true
  } catch {
    return false
  }
}

export async function sendOutbox(opts: SyncOptions): Promise<SyncResult> {
  const account = await currentAccount()
  if (!account || (opts.userId && account.userId !== opts.userId)) return { status: 'nouser', accepted: 0, rejected: 0, left: 0 }
  const db = userDb(account.userId)
  const pending = await db.outbox.count()
  if (pending === 0) return { status: 'empty', accepted: 0, rejected: 0, left: 0 }
  if (!account.token) return { status: 'auth', accepted: 0, rejected: 0, left: pending, message: 'Нужен повторный вход' }

  const busy: SyncResult = { status: 'busy', accepted: 0, rejected: 0, left: pending }
  return withLock(async () => {
    const deviceId = await getDeviceId()
    let accepted = 0
    let rejected = 0
    for (;;) {
      const batch: OutboxEvent[] = await db.outbox.orderBy('createdAt').limit(BATCH).toArray()
      if (batch.length === 0) break
      // «возврат связи» сообщаем, только если в пакете есть записи, сделанные до него
      const oldest = Math.min(...batch.map((e) => e.createdAt))
      const restoredAt = opts.restoredAt && oldest < opts.restoredAt ? new Date(opts.restoredAt).toISOString() : null
      let res: Response
      try {
        res = await timedFetch('/api/sync/events', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${account.token}` },
          body: JSON.stringify({
            device_id: deviceId,
            sent_at: new Date().toISOString(),
            restored_at: restoredAt,
            trigger: opts.trigger,
            app_state: opts.appState,
            events: batch.map((e) => ({ id: e.id, type: e.type, device_time: e.deviceTime, payload: e.payload })),
          }),
        })
      } catch {
        await db.outbox.bulkPut(batch.map((e) => ({ ...e, tries: e.tries + 1, lastError: 'network' })))
        return { status: 'offline', accepted, rejected, left: await db.outbox.count(), message: 'Нет связи с сервером' }
      }
      if (res.status === 401) {
        await dropToken(account.userId, account.token)
        await logLocal(db, 'sync', 'Сервер требует повторный вход. Записи сохранены на устройстве.')
        return { status: 'auth', accepted, rejected, left: await db.outbox.count(), message: 'Нужен повторный вход' }
      }
      if (!res.ok) {
        await logLocal(db, 'sync', `Сервер ответил ошибкой ${res.status}. Повторим позже.`)
        return { status: 'server', accepted, rejected, left: await db.outbox.count(), message: `Ошибка сервера (${res.status})` }
      }
      const data = (await res.json()) as { results: { id: string; status: string; code?: string; reason?: string }[] }
      const byId = new Map(batch.map((e) => [e.id, e]))
      const confirmed: string[] = []
      const refused: { id: string; code: string; reason: string }[] = []
      for (const r of data.results) {
        if (r.status === 'accepted' || r.status === 'duplicate') confirmed.push(r.id)
        else if (r.status === 'rejected') refused.push({ id: r.id, code: r.code ?? '', reason: r.reason ?? 'Запись отклонена' })
      }
      // из очереди удаляются только записи, которые сервер подтвердил или окончательно отклонил
      await db.transaction('rw', db.outbox, db.rejected, async () => {
        await db.outbox.bulkDelete(confirmed)
        for (const r of refused) {
          const ev = byId.get(r.id)
          if (ev) {
            await db.rejected.put({ id: ev.id, type: ev.type, payload: ev.payload, code: r.code, reason: r.reason, at: new Date().toISOString() })
            await db.outbox.delete(ev.id)
          }
        }
      })
      accepted += confirmed.length
      rejected += refused.length
      // Запись, которую сервер не подтвердил и не отклонил (например, попросил повторить),
      // остаётся в очереди. Чтобы не крутить её в цикле, заканчиваем этот заход — повтор по таймеру.
      if (confirmed.length + refused.length < batch.length) {
        if (accepted > 0) await pullState(account)
        return { status: 'server', accepted, rejected, left: await db.outbox.count(), message: 'Часть записей будет отправлена повторно' }
      }
    }
    await db.state.put({ key: 'lastSync', value: { at: new Date().toISOString(), trigger: opts.trigger, accepted, rejected }, savedAt: new Date().toISOString() })
    await logLocal(db, 'sync', `Отправлено записей: ${accepted}${rejected ? `, отклонено: ${rejected}` : ''} (${opts.trigger}).`)
    await pullState(account)
    return { status: 'ok', accepted, rejected, left: 0 }
  }, busy)
}
