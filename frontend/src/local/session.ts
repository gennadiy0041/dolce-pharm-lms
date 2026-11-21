// Вход, выход и текущая учётная запись на устройстве.
import { ApiError, NetworkError, api, setToken, setUnauthorizedHandler } from '../api/client'
import type { ClientSettings, User } from '../types'
import { deviceDb, dropUserDb, getDeviceId, kvGet, kvSet, userDb, type Account, type UserDb } from './db'
import { checkServer } from './net'
import { createStore, useStore } from './store'
import { auditPackages, resetDownloads } from './downloads'
import { isActive, onLifecycle } from './lifecycle'
import { flushSync, requestSync, setAuthLostHandler, startSyncManager, stopSyncManager } from './sync'
import { dropToken } from './syncCore'
import { startTracker, stopTracker } from './tracker'

export interface SessionState {
  status: 'loading' | 'anon' | 'ready'
  account: Account | null
  needsLogin: boolean          // токен отозван или истёк: локальная работа доступна, отправка — после входа
}

export const sessionStore = createStore<SessionState>({ status: 'loading', account: null, needsLogin: false })
export const useSession = (): SessionState => useStore(sessionStore)

export function currentDb(): UserDb {
  const a = sessionStore.get().account
  if (!a) throw new Error('Нет активного пользователя')
  return userDb(a.userId)
}

export function deviceKind(): 'phone' | 'pc' {
  const coarse = window.matchMedia?.('(pointer: coarse)').matches
  const small = Math.min(window.screen.width, window.screen.height) < 820
  return coarse && small ? 'phone' : 'pc'
}

const DEFAULT_SETTINGS: ClientSettings = { heartbeat_seconds: 15, idle_timeout_seconds: 120, reading_idle_seconds: 90, local_retention_days: 14 }

// ---- проверка пароля без сети (PBKDF2 в браузере) ----
function b64(buf: ArrayBuffer | Uint8Array): string {
  const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf)
  let s = ''
  bytes.forEach((b) => (s += String.fromCharCode(b)))
  return btoa(s)
}
function unb64(s: string): Uint8Array {
  return Uint8Array.from(atob(s), (c) => c.charCodeAt(0))
}
async function derive(password: string, salt: Uint8Array, iterations: number): Promise<string> {
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveBits'])
  const bits = await crypto.subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt: salt as BufferSource, iterations }, key, 256)
  return b64(bits)
}
async function makeVerifier(password: string): Promise<Account['verifier']> {
  if (!crypto?.subtle) return undefined
  const salt = crypto.getRandomValues(new Uint8Array(16))
  const iterations = 120000
  return { salt: b64(salt), hash: await derive(password, salt, iterations), iterations }
}
async function checkVerifier(password: string, v: NonNullable<Account['verifier']>): Promise<boolean> {
  if (!crypto?.subtle) return false
  return (await derive(password, unb64(v.salt), v.iterations)) === v.hash
}

// Вкладки одного браузера делят общую базу устройства. Когда в одной вкладке входят или выходят,
// остальные перезагружаются: иначе в них остался бы открытым кабинет уже вышедшего пользователя.
const channel = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('dolce-lms-session') : null

function announceAccountChange(): void {
  channel?.postMessage({ type: 'account-changed' })
}

async function activate(account: Account, announce = false): Promise<void> {
  setToken(account.token)
  await kvSet('currentUserId', account.userId)
  sessionStore.set({ status: 'ready', account, needsLogin: !account.token })
  const db = userDb(account.userId)
  await startTracker(db, account.settings ?? DEFAULT_SETTINGS)
  startSyncManager(db, account.userId)
  // курс, отмеченный «Готово без сети», должен быть на устройстве целиком
  void auditPackages(db)
  if (announce) announceAccountChange()
}

/** Берёт из базы устройства токен, полученный в соседней вкладке (там вошли заново под той же учётной записью). */
function adopt(stored: Account): void {
  const cur = sessionStore.get().account
  if (!cur || cur.userId !== stored.userId || !stored.token || stored.token === cur.token) return
  setToken(stored.token)
  sessionStore.set({ account: stored, needsLogin: false })
  void requestSync('startup')
}

/**
 * Сервер больше не принимает токен: данные остаются на устройстве, просим войти заново.
 * `failed` — токен отвергнутого запроса. Если в приложении или в базе устройства уже лежит другой,
 * более новый токен (вошли заново в соседней вкладке), его не трогаем, а начинаем им пользоваться.
 */
async function markNeedsLogin(failed?: string | null): Promise<void> {
  const a = sessionStore.get().account
  if (!a) return
  if (failed !== undefined && failed !== a.token) return        // ответ на запрос со старым токеном
  await dropToken(a.userId, a.token)
  const stored = await deviceDb.accounts.get(a.userId)
  const cur = sessionStore.get().account
  if (!cur || cur.userId !== a.userId) return                    // за это время вышли или сменили пользователя
  if (stored?.token) {
    adopt(stored)
    return
  }
  if (cur.token !== a.token) return                              // уже вошли заново в этой же вкладке
  setToken(null)
  sessionStore.set({ needsLogin: true, account: { ...cur, token: null } })
}

/** В соседней вкладке вошли или вышли: сверяем, кто сейчас текущий пользователь устройства. */
async function checkAccountChange(): Promise<void> {
  const current = (await kvGet<string>('currentUserId')) ?? null
  const mine = sessionStore.get().account?.userId ?? null
  if (current !== mine) {
    window.location.reload()
    return
  }
  // та же учётная запись заново вошла в соседней вкладке: работаем с её новым токеном
  const stored = mine ? await deviceDb.accounts.get(mine) : undefined
  if (stored) adopt(stored)
}

let accountCheckPending = false

export async function bootSession(): Promise<void> {
  setUnauthorizedHandler((failed) => void markNeedsLogin(failed))
  setAuthLostHandler(() => void markNeedsLogin())                // то же самое, если об этом узнала очередь отправки
  channel?.addEventListener('message', (event) => {
    if ((event.data as { type?: string } | undefined)?.type !== 'account-changed') return
    // страница в фоне к базе не обращается — проверим, когда она вернётся на экран
    if (!isActive()) {
      accountCheckPending = true
      return
    }
    void checkAccountChange()
  })
  onLifecycle({
    name: 'session',
    resume: () => {
      // пока приложение было в фоне, браузер мог очистить часть хранилища
      const a = sessionStore.get().account
      if (a) void auditPackages(userDb(a.userId))
      if (!accountCheckPending) return
      accountCheckPending = false
      void checkAccountChange()
    },
  })
  try {
    const userId = await kvGet<string>('currentUserId')
    const account = userId ? await deviceDb.accounts.get(userId) : undefined
    if (!account) {
      sessionStore.set({ status: 'anon', account: null })
      return
    }
    await activate(account)
    // обновляем профиль и настройки, если сервер доступен
    if (account.token && (await checkServer())) {
      try {
        const me = await api.get<{ user: User; settings: ClientSettings }>('/api/auth/me')
        const fresh: Account = { ...account, user: me.user, settings: me.settings }
        await deviceDb.accounts.put(fresh)
        sessionStore.set({ account: fresh })
      } catch {
        /* профиль обновится при следующем запуске */
      }
    }
  } catch {
    sessionStore.set({ status: 'anon', account: null })
  }
}

export interface LoginOutcome {
  ok: boolean
  offline?: boolean
  message?: string
}

export async function login(loginName: string, password: string): Promise<LoginOutcome> {
  const name = loginName.trim().toLowerCase()
  try {
    const res = await api.post<{ token: string; user: User; settings: ClientSettings }>('/api/auth/login', {
      login: name, password, device_id: await getDeviceId(), device_kind: deviceKind(),
    })
    const account: Account = {
      userId: res.user.id, login: res.user.login, user: res.user, token: res.token, settings: res.settings,
      verifier: await makeVerifier(password), lastLoginAt: new Date().toISOString(),
    }
    await deviceDb.accounts.put(account)
    await activate(account, true)
    return { ok: true }
  } catch (e) {
    if (e instanceof NetworkError) {
      // сервера нет: пускаем по сохранённой на устройстве проверке пароля
      const local = await deviceDb.accounts.where('login').equals(name).first()
      if (local?.user.role === 'methodist') {
        // кабинет методиста целиком работает с данными сервера — без связи в нём нечего показать
        return { ok: false, message: 'Кабинет методиста работает только при связи с сервером. Проверьте, что сервер запущен, и войдите ещё раз.' }
      }
      if (local?.verifier && (await checkVerifier(password, local.verifier))) {
        await activate(local, true)
        return { ok: true, offline: true }
      }
      return {
        ok: false,
        message: local
          ? 'Нет связи с сервером, а пароль не совпал с сохранённым на этом устройстве.'
          : 'Нет связи с сервером. Первый вход на этом устройстве возможен только при доступном сервере.',
      }
    }
    if (e instanceof ApiError) return { ok: false, message: e.message }
    return { ok: false, message: 'Не удалось войти' }
  }
}

export async function pendingCount(): Promise<number> {
  const a = sessionStore.get().account
  return a ? userDb(a.userId).outbox.count() : 0
}

export async function logout(): Promise<void> {
  const a = sessionStore.get().account
  if (!a) return
  await stopTracker(true)                 // записывает явный выход в очередь
  await resetDownloads()                  // незавершённая загрузка не должна достаться следующему пользователю
  await flushSync('manual')               // ждём идущую отправку и последний раз отправляем всё накопленное
  try {
    if (a.token) await api.post('/api/auth/logout')
  } catch {
    /* без сети токен останется на сервере до истечения срока */
  }
  stopSyncManager()
  await deviceDb.accounts.update(a.userId, { token: null })
  await kvSet('currentUserId', null)
  setToken(null)
  sessionStore.set({ status: 'anon', account: null, needsLogin: false })
  announceAccountChange()
}

/** Контролируемая очистка устройства: удаляет курсы, файлы и историю, но не трогает неотправленные записи. */
export async function wipeLocalData(): Promise<{ kept: number }> {
  const db = currentDb()
  await resetDownloads()                  // идущая загрузка иначе дописала бы файлы в только что очищенную базу
  const kept = await db.outbox.count()
  await db.transaction('rw', [db.packages, db.files, db.chunks, db.libraryPins, db.log, db.trainer, db.rejected, db.attempts], async () => {
    // попытки, которые сервер не принял, удаляются вместе с сообщениями об отказе: без сообщения
    // такая попытка выглядела бы как «отправлена, ждём ответ» и не давала начать новую
    const refused = (await db.rejected.toArray()).filter((r) => r.type === 'attempt.submit').map((r) => String(r.payload.attempt_id))
    if (refused.length) await db.attempts.where('id').anyOf(refused).and((t) => t.status === 'submitted').delete()
    await Promise.all([db.packages.clear(), db.files.clear(), db.chunks.clear(), db.libraryPins.clear(),
      db.log.clear(), db.trainer.clear(), db.rejected.clear()])
  })
  if (kept === 0) {
    await db.transaction('rw', [db.attempts, db.progress], async () => {
      await db.attempts.where('status').equals('submitted').delete()
      await db.progress.clear()
    })
  }
  // базы других учётных записей, которые входили на этом устройстве и всё отправили, удаляются целиком
  const me = sessionStore.get().account?.userId
  for (const other of await deviceDb.accounts.toArray()) {
    if (other.userId === me) continue
    try {
      // чужую базу удаляем, только если в ней не осталось ничего несданного:
      // ни очереди отправки, ни начатого теста, ни незакрытого интервала времени
      const odb = userDb(other.userId)
      const [queued, drafts, open] = await Promise.all([
        odb.outbox.count(), odb.attempts.where('status').equals('draft').count(), odb.openIntervals.count(),
      ])
      if (queued === 0 && drafts === 0 && open === 0) {
        await dropUserDb(other.userId)
        await deviceDb.accounts.delete(other.userId)
      }
    } catch {
      /* база занята другой вкладкой — останется до следующей очистки */
    }
  }
  return { kept }
}
