// Локальные базы IndexedDB (Dexie).
//
// «База устройства» хранит идентификатор устройства и учётные записи, которые на нём входили.
// У каждого сотрудника — собственная база: пакеты курсов, ответы, очередь отправки. Профиль
// другого сотрудника открывает другую базу и не видит чужих данных.
import Dexie, { type Table } from 'dexie'
import type {
  ClientSettings, LibraryItem, LocalAttempt, OutboxEvent, PackageRow, RejectedEvent, User,
} from '../types'

export interface Account {
  userId: string
  login: string
  user: User
  token: string | null            // null — нужен повторный вход
  settings: ClientSettings
  verifier?: { salt: string; hash: string; iterations: number }   // проверка пароля без сети
  lastLoginAt: string
}

class DeviceDb extends Dexie {
  kv!: Table<{ key: string; value: unknown }, string>
  accounts!: Table<Account, string>
  constructor() {
    super('dolce-lms-device')
    this.version(1).stores({ kv: 'key', accounts: 'userId, login' })
  }
}

export const deviceDb = new DeviceDb()

export async function kvGet<T>(key: string): Promise<T | undefined> {
  return (await deviceDb.kv.get(key))?.value as T | undefined
}
export async function kvSet(key: string, value: unknown): Promise<void> {
  await deviceDb.kv.put({ key, value })
}

export function uuid(): string {
  const c = globalThis.crypto as Crypto & { randomUUID?: () => string }
  if (typeof c.randomUUID === 'function') return c.randomUUID()   // есть только в защищённом контексте
  const b = new Uint8Array(16)
  c.getRandomValues(b)
  b[6] = (b[6] & 0x0f) | 0x40
  b[8] = (b[8] & 0x3f) | 0x80
  const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}

export async function getDeviceId(): Promise<string> {
  let id = await kvGet<string>('deviceId')
  if (!id) {
    id = uuid()
    await kvSet('deviceId', id)
  }
  return id
}

export interface StoredBlob { sha256: string; blob: Blob; size: number; mime: string }
export interface ChunkRow { sha256: string; index: number; blob: Blob }
export interface ProgressRow { versionId: string; done: string[]; lastBlockId: string | null; updatedAt: string }
export interface OpenInterval {
  id: string
  tabId: string
  kind: 'platform' | 'material'
  versionId?: string
  blockId?: string
  libraryItemId?: string
  sessionId: string
  start: number
  end: number
  updatedAt: number
}
export interface LibraryPin { itemId: string; item: LibraryItem; savedAt: string }
export interface LocalTrainerResult {
  id: string; versionId: string; blockId: string; endingId: string; title: string; outcome: string; finishedAt: string
}
export interface LogRow { id?: number; at: string; kind: string; text: string }

export class UserDb extends Dexie {
  state!: Table<{ key: string; value: unknown; savedAt: string }, string>
  packages!: Table<PackageRow, string>
  files!: Table<StoredBlob, string>
  chunks!: Table<ChunkRow, [string, number]>
  progress!: Table<ProgressRow, string>
  attempts!: Table<LocalAttempt, string>
  outbox!: Table<OutboxEvent, string>
  rejected!: Table<RejectedEvent, string>
  openIntervals!: Table<OpenInterval, string>
  libraryPins!: Table<LibraryPin, string>
  trainer!: Table<LocalTrainerResult, string>
  log!: Table<LogRow, number>

  constructor(userId: string) {
    super(`dolce-lms-u-${userId}`)
    this.version(1).stores({
      state: 'key',
      packages: 'versionId, status',
      files: 'sha256',
      chunks: '[sha256+index], sha256',
      progress: 'versionId',
      attempts: 'id, assignmentId, versionId, status',
      outbox: 'id, createdAt, type',
      rejected: 'id',
      openIntervals: 'id, tabId',
      libraryPins: 'itemId',
      trainer: 'id, versionId',
      log: '++id',
    })
  }
}

const cache = new Map<string, UserDb>()

/** Все базы, открытые этой страницей: их закрывают, когда страница уходит в фон (см. lifecycle.ts). */
export function allDatabases(): Dexie[] {
  return [deviceDb, ...cache.values()]
}

export function userDb(userId: string): UserDb {
  let db = cache.get(userId)
  if (!db) {
    db = new UserDb(userId)
    cache.set(userId, db)
  }
  return db
}

/** Удаляет локальную базу сотрудника целиком (после того как всё отправлено). */
export async function dropUserDb(userId: string): Promise<void> {
  const db = userDb(userId)
  cache.delete(userId)
  await db.delete()
}

export async function logLocal(db: UserDb, kind: string, text: string): Promise<void> {
  try {
    await db.log.add({ at: new Date().toISOString(), kind, text })
    const count = await db.log.count()
    if (count > 300) {
      const old = await db.log.orderBy('id').limit(count - 200).primaryKeys()
      await db.log.bulkDelete(old)
    }
  } catch {
    /* журнал не должен мешать работе */
  }
}
