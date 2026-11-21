// Страница на экране или в фоне — и что из этого следует для локальной базы.
//
// Почему это нужно. Каждое изменение базы Dexie рассылает всем копиям платформы того же адреса
// (вкладкам браузера, установленному приложению, Service Worker). Копия в фоне в ответ перечитывает
// базу. На Android процесс фоновой вкладки замораживается системой в любой момент — в том числе
// посреди такого чтения. Незавершённая транзакция замороженной вкладки держит хранилище, и запись
// в установленном приложении встаёт в очередь навсегда: экран «Загружаем…», скачивание не идёт.
// Так было на POCO X7 Pro 21.11.2026; воспроизводится автотестом 08-background-tab-lock.
//
// Правило: страница, которая не на экране, к базе не обращается и соединение с ней закрывает.
// Перед уходом в фон она дописывает начатые интервалы времени, ставит загрузки на паузу и просит
// Service Worker отправить очередь (он работает со своим соединением). При возврате на экран
// соединение открывается заново, данные перечитываются, загрузки продолжаются.
import { allDatabases } from './db'
import { createStore, useStore } from './store'

export interface LifecycleState {
  active: boolean          // страница на экране и работает с базой
  changedAt: number
}

export const lifecycleStore = createStore<LifecycleState>({ active: true, changedAt: Date.now() })
export const isActive = (): boolean => lifecycleStore.get().active
export const useActive = (): boolean => useStore(lifecycleStore).active

export interface Participant {
  name: string
  /** Вызывается до закрытия базы: дописать и остановить всё, что с ней работает. */
  suspend?: () => Promise<void> | void
  /** Вызывается после открытия базы. */
  resume?: () => Promise<void> | void
}

const participants: Participant[] = []

export function onLifecycle(p: Participant): () => void {
  participants.push(p)
  return () => {
    const i = participants.indexOf(p)
    if (i >= 0) participants.splice(i, 1)
  }
}

const visible = (): boolean => document.visibilityState === 'visible'

// Переходы выполняются строго по очереди: быстрое сворачивание и разворачивание не перемешивают шаги.
let chain: Promise<void> = Promise.resolve()
let ready: Promise<unknown> = Promise.resolve()

/** Сколько раз страница уходила в фон и возвращалась — для диагностики и автотестов. */
export const lifecycleStats = { suspended: 0, resumed: 0, lastError: '' }

async function suspend(): Promise<void> {
  if (!isActive()) return
  lifecycleStore.set({ active: false, changedAt: Date.now() })      // React-хуки перестают читать базу
  for (const p of [...participants].reverse()) {
    try {
      await p.suspend?.()
    } catch (e) {
      lifecycleStats.lastError = `${p.name}: ${(e as Error).message}`
    }
  }
  // React успевает перерисоваться и отписаться от живых запросов, начатые чтения — завершиться
  await new Promise((resolve) => setTimeout(resolve, 80))
  for (const db of allDatabases()) {
    if (db.isOpen()) db.close({ disableAutoOpen: true })
  }
  lifecycleStats.suspended += 1
}

async function resume(): Promise<void> {
  if (isActive()) return
  for (const db of allDatabases()) {
    if (!db.isOpen()) {
      try {
        await db.open()
      } catch (e) {
        lifecycleStats.lastError = `open: ${(e as Error).message}`
      }
    }
  }
  lifecycleStore.set({ active: true, changedAt: Date.now() })        // хуки перечитывают базу
  for (const p of participants) {
    try {
      await p.resume?.()
    } catch (e) {
      lifecycleStats.lastError = `${p.name}: ${(e as Error).message}`
    }
  }
  lifecycleStats.resumed += 1
}

function sync(): void {
  chain = chain
    .then(() => ready)
    .then(() => (visible() ? resume() : suspend()))
    .catch(() => undefined)
}

let started = false

/** Запускается один раз после входа: `boot` — промис загрузки сессии (до него база открыта всегда). */
export function startLifecycle(boot: Promise<unknown>): void {
  if (started) return
  started = true
  ready = boot.catch(() => undefined)
  document.addEventListener('visibilitychange', sync)
  window.addEventListener('pageshow', sync)
  // Chrome сообщает о заморозке и разморозке вкладки отдельными событиями
  document.addEventListener('freeze', sync)
  document.addEventListener('resume', sync)
  sync()
  ;(window as unknown as { __lmsLifecycle?: unknown }).__lmsLifecycle = { stats: lifecycleStats, isActive }
}
