// Учёт времени. Два независимых «канала»:
//   platform — активное время в приложении (экран виден, пользователь не бездействует);
//   material — изучение конкретного блока (чтение: экран виден и были действия; видео: идёт воспроизведение).
// Интервал сохраняется в IndexedDB каждые heartbeat секунд, закрывается при паузе, смене экрана,
// сворачивании и не реже раза в минуту. После аварийного закрытия незакрытый интервал отправляется
// с пометкой «оценка»: его конец — последняя сохранённая запись.
import { uuid, type OpenInterval, type UserDb } from './db'
import { isActive, onLifecycle } from './lifecycle'
import { enqueueTx, kick, registerBackgroundSync } from './sync'
import type { ClientSettings } from '../types'

interface MaterialTarget {
  versionId?: string
  blockId?: string
  libraryItemId?: string
  mode: 'reading' | 'video'
}

interface Channel {
  kind: 'platform' | 'material'
  current: OpenInterval | null
  savedAt: number
}

const TAB_ID = uuid()
const ROLLOVER_MS = 60000
const GAP_MS = 5000          // если таймер молчал дольше — устройство спало, время не засчитываем
const MIN_MS = 250           // совсем короткие обрывки (мгновенное пролистывание) не записываем

let db: UserDb | null = null
let settings: ClientSettings = { heartbeat_seconds: 15, idle_timeout_seconds: 120, reading_idle_seconds: 90, local_retention_days: 14 }
let sessionId = ''
let lastInput = Date.now()
let lastTick = Date.now()
let ticker: ReturnType<typeof setInterval> | null = null
let material: MaterialTarget | null = null
let videoPlaying = false
const platform: Channel = { kind: 'platform', current: null, savedAt: 0 }
const mat: Channel = { kind: 'material', current: null, savedAt: 0 }
const cleanups: (() => void)[] = []

export function currentSessionId(): string {
  return sessionId
}

function visible(): boolean {
  return document.visibilityState === 'visible'
}

function platformActive(now: number): boolean {
  if (!visible()) return false
  if (material?.mode === 'video' && videoPlaying) return true
  return now - lastInput < settings.idle_timeout_seconds * 1000
}

function materialActive(now: number): boolean {
  if (!material || !visible()) return false
  if (material.mode === 'video') return videoPlaying
  return now - lastInput < settings.reading_idle_seconds * 1000
}

async function persist(ch: Channel, now: number): Promise<void> {
  if (!db || !ch.current) return
  ch.current.updatedAt = now
  ch.savedAt = now
  await db.openIntervals.put({ ...ch.current })
}

async function closeChannel(ch: Channel, end: number, estimated = false): Promise<void> {
  const cur = ch.current
  ch.current = null
  if (!db || !cur) return
  const finish = Math.max(cur.start, end)
  const target = db
  await target.transaction('rw', target.openIntervals, target.outbox, async () => {
    await target.openIntervals.delete(cur.id)
    if (finish - cur.start >= MIN_MS) {
      await enqueueTx(target, 'time.interval', {
        interval_id: cur.id, kind: cur.kind, session_id: cur.sessionId,
        version_id: cur.versionId ?? null, block_id: cur.blockId ?? null, library_item_id: cur.libraryItemId ?? null,
        start: new Date(cur.start).toISOString(), end: new Date(finish).toISOString(), estimated,
      }, new Date(finish).toISOString())
    }
  })
}

function openChannel(ch: Channel, now: number): void {
  ch.current = {
    id: uuid(), tabId: TAB_ID, kind: ch.kind, sessionId, start: now, end: now, updatedAt: now,
    versionId: ch.kind === 'material' ? material?.versionId : undefined,
    blockId: ch.kind === 'material' ? material?.blockId : undefined,
    libraryItemId: ch.kind === 'material' ? material?.libraryItemId : undefined,
  }
  ch.savedAt = 0
}

async function step(ch: Channel, active: boolean, now: number, gap: boolean): Promise<void> {
  if (ch.current && gap) {
    // таймер был заморожен (экран заблокирован, вкладка усыплена): закрываем по последней отметке
    await closeChannel(ch, ch.current.end)
  }
  if (active) {
    if (!ch.current) {
      openChannel(ch, now)
      await persist(ch, now)
      return
    }
    ch.current.end = now
    if (now - ch.current.start >= ROLLOVER_MS) {
      await closeChannel(ch, now)
      openChannel(ch, now)
      await persist(ch, now)
      kick('timer')
    } else if (now - ch.savedAt >= settings.heartbeat_seconds * 1000) {
      await persist(ch, now)
    }
  } else if (ch.current) {
    // пауза наступила только что (таймер не спал) — засчитываем время до текущего момента
    await closeChannel(ch, now)
  }
}

let busy = false
async function tick(): Promise<void> {
  if (busy || !db || !isActive()) return       // в фоне база закрыта, учёт остановлен
  busy = true
  try {
    const now = Date.now()
    const gap = now - lastTick > GAP_MS
    lastTick = now
    await step(platform, platformActive(now), now, gap)
    await step(mat, materialActive(now), now, gap)
  } finally {
    busy = false
  }
}

/** Мгновенно закрывает интервалы — при сворачивании, блокировке, закрытии страницы. */
async function flush(reason: 'hide' | 'switch' | 'stop'): Promise<void> {
  const now = Date.now()
  const gap = now - lastTick > GAP_MS
  const had = !!(platform.current || mat.current)
  if (platform.current && reason !== 'switch') await closeChannel(platform, gap ? platform.current.end : now)
  if (mat.current) await closeChannel(mat, gap ? mat.current.end : now)
  lastTick = now
  // Закрытый интервал лежит в очереди. Страница сейчас уходит в фон и сама его не отправит —
  // просим Service Worker: он отправит сразу, если связь есть, или как только она появится.
  if (had && reason !== 'switch') registerBackgroundSync()
}

let recoverTimer: ReturnType<typeof setInterval> | null = null

function startTimers(target: UserDb): void {
  stopTimers()
  ticker = setInterval(() => void tick(), 1000)
  // Хвост после аварийного закрытия считается брошенным, только когда его запись «состарилась».
  // Если приложение открыли сразу после сбоя, при старте он ещё свежий — поэтому проверка повторяется.
  recoverTimer = setInterval(() => {
    if (!isActive()) return
    void recoverLeftovers(target).then((n) => { if (n) kick('timer') })
  }, 20000)
}

function stopTimers(): void {
  if (ticker) clearInterval(ticker)
  ticker = null
  if (recoverTimer) clearInterval(recoverTimer)
  recoverTimer = null
}

/** Страница уходит в фон: дописать начатые интервалы, пока база ещё открыта, и остановить таймеры. */
async function pauseTracker(): Promise<void> {
  stopTimers()
  if (db) await flush('hide')
}

/** Страница вернулась на экран: учёт продолжается с текущего момента. */
function resumeTracker(): void {
  if (!db) return
  lastInput = Date.now()
  lastTick = Date.now()
  startTimers(db)
  void tick()
}

/** Закрывает текущие интервалы и сразу продолжает учёт: вызывается перед сдачей теста,
 * чтобы время изучения ушло на сервер вместе с ответами. */
export async function checkpoint(): Promise<void> {
  if (!db) return
  const now = Date.now()
  if (now - lastTick > GAP_MS) return
  if (platform.current) await closeChannel(platform, now)
  if (mat.current) await closeChannel(mat, now)
  await tick()
}

function onInput(): void {
  lastInput = Date.now()
}

/** Интервалы, оставшиеся после аварийного закрытия, отправляются как оценка по последней записи. */
async function recoverLeftovers(target: UserDb): Promise<number> {
  // живая вкладка обновляет свою запись каждые heartbeat секунд; молчание дольше двух шагов — признак сбоя
  const cutoff = Date.now() - Math.max(30000, settings.heartbeat_seconds * 2000 + 5000)
  const stale = (await target.openIntervals.toArray()).filter((r) => r.tabId !== TAB_ID && r.updatedAt < cutoff)
  if (!stale.length) return 0
  for (const r of stale) {
    await target.transaction('rw', target.openIntervals, target.outbox, async () => {
      await target.openIntervals.delete(r.id)
      if (r.end - r.start >= MIN_MS) {
        await enqueueTx(target, 'time.interval', {
          interval_id: r.id, kind: r.kind, session_id: r.sessionId, version_id: r.versionId ?? null,
          block_id: r.blockId ?? null, library_item_id: r.libraryItemId ?? null,
          start: new Date(r.start).toISOString(), end: new Date(r.end).toISOString(), estimated: true,
        }, new Date(r.end).toISOString())
      }
    })
  }
  return stale.length
}

export async function startTracker(target: UserDb, s: ClientSettings): Promise<void> {
  await stopTracker()
  db = target
  settings = s
  sessionId = uuid()
  lastInput = Date.now()
  lastTick = Date.now()
  await recoverLeftovers(target)
  await enqueueTx(target, 'session.start', { session_id: sessionId, started_at: new Date().toISOString() })

  const events: (keyof DocumentEventMap)[] = ['pointerdown', 'keydown', 'scroll', 'touchstart', 'wheel', 'pointermove']
  events.forEach((e) => document.addEventListener(e, onInput, { passive: true, capture: true }))
  cleanups.push(() => events.forEach((e) => document.removeEventListener(e, onInput, { capture: true } as EventListenerOptions)))

  // Уход в фон и возврат обрабатывает общий жизненный цикл страницы: интервалы дописываются
  // до того, как закроется соединение с базой (см. lifecycle.ts).
  cleanups.push(onLifecycle({ name: 'tracker', suspend: pauseTracker, resume: resumeTracker }))
  const onHide = () => {
    if (isActive()) void flush('hide')
  }
  window.addEventListener('pagehide', onHide)
  cleanups.push(() => window.removeEventListener('pagehide', onHide))

  if (isActive()) {
    startTimers(target)
    void tick()
  }
}

export async function stopTracker(endSession = false): Promise<void> {
  stopTimers()
  cleanups.splice(0).forEach((fn) => fn())
  if (db && isActive()) {
    await flush('stop')
    if (endSession && sessionId) {
      await enqueueTx(db, 'session.end', { session_id: sessionId, ended_at: new Date().toISOString() })
    }
  }
  material = null
  videoPlaying = false
  db = null
}

/** Экран материала открыт: начинаем учёт изучения. */
export function beginMaterial(target: MaterialTarget): void {
  material = target
  videoPlaying = false
  lastInput = Date.now()
  void (async () => {
    if (mat.current) await closeChannel(mat, Date.now())
    await tick()
  })()
}

/** Экран материала закрыт: остаток интервала сохраняется сразу. */
export function endMaterial(): void {
  material = null
  videoPlaying = false
  void flush('switch')
}

export function setVideoPlaying(playing: boolean): void {
  videoPlaying = playing
  if (playing) lastInput = Date.now()
  void tick()
}
