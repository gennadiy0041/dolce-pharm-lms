// Доступность сервера. navigator.onLine ненадёжен (Wi-Fi есть, а сервера нет), поэтому
// состояние определяется реальным запросом к /api/health.
import { createStore, useStore } from './store'

export interface NetState {
  reachable: boolean | null      // null — ещё не проверяли
  restoredAt: number | null      // когда сервер снова стал доступен после перерыва
  lostAt: number | null
  checkedAt: number | null
}

export const netStore = createStore<NetState>({ reachable: null, restoredAt: null, lostAt: null, checkedAt: null })

type Listener = (reachable: boolean, justRestored: boolean) => void
const listeners = new Set<Listener>()
export function onNetChange(fn: Listener): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

let checking: Promise<boolean> | null = null

export function checkServer(): Promise<boolean> {
  if (checking) return checking
  checking = (async () => {
    let ok = false
    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), 4000)
    try {
      const res = await fetch('/api/health', { cache: 'no-store', signal: ctrl.signal })
      ok = res.ok
    } catch {
      ok = false
    } finally {
      clearTimeout(timer)
    }
    setReachable(ok)
    return ok
  })().finally(() => {
    checking = null
  })
  return checking
}

export function setReachable(ok: boolean): void {
  const prev = netStore.get()
  const now = Date.now()
  if (prev.reachable === ok) {
    netStore.set({ checkedAt: now })
    return
  }
  const justRestored = ok && prev.reachable === false
  netStore.set({
    reachable: ok,
    checkedAt: now,
    restoredAt: justRestored ? now : prev.restoredAt,
    lostAt: !ok ? now : prev.lostAt,
  })
  listeners.forEach((fn) => fn(ok, justRestored))
}

let started = false
export function startNetMonitor(): void {
  if (started) return
  started = true
  window.addEventListener('online', () => void checkServer())
  window.addEventListener('offline', () => setReachable(false))
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') void checkServer()
  })
  const loop = () => {
    const { reachable } = netStore.get()
    // пока связи нет — проверяем часто, чтобы поймать её возврат за секунды
    const delay = reachable === false ? 3000 : 20000
    setTimeout(async () => {
      if (document.visibilityState === 'visible') await checkServer()
      loop()
    }, delay)
  }
  void checkServer().then(loop)
}

export function useNet(): NetState {
  return useStore(netStore)
}
