/// <reference lib="webworker" />
// Service Worker: хранит оболочку приложения для запуска без сети и отправляет очередь,
// когда браузер сообщает о возврате связи (Background Sync), даже если приложение закрыто.
import { clientsClaim } from 'workbox-core'
import { cleanupOutdatedCaches, createHandlerBoundToURL, precacheAndRoute } from 'workbox-precaching'
import { NavigationRoute, registerRoute } from 'workbox-routing'
import { sendOutbox } from './local/syncCore'

declare const self: ServiceWorkerGlobalScope & { __WB_MANIFEST: { url: string; revision: string | null }[] }

interface SyncEvent extends ExtendableEvent {
  readonly tag: string
  readonly lastChance: boolean
}

self.skipWaiting()
clientsClaim()
cleanupOutdatedCaches()
// список файлов оболочки подставляется при сборке (в исходнике должен встречаться один раз)
const shellFiles = self.__WB_MANIFEST
precacheAndRoute(shellFiles)

// Любой экран приложения открывается из сохранённой оболочки; API и служебные страницы — только из сети.
registerRoute(new NavigationRoute(createHandlerBoundToURL('/index.html'), {
  denylist: [/^\/api\//, /^\/phone/, /^\/ca\.crt/, /^\/swagger\//],
}))

async function appState(): Promise<'visible' | 'hidden' | 'closed'> {
  const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
  if (windows.length === 0) return 'closed'
  return windows.some((c) => (c as WindowClient).visibilityState === 'visible') ? 'visible' : 'hidden'
}

async function backgroundSend(): Promise<void> {
  const state = await appState()
  const result = await sendOutbox({ trigger: 'background-sync', appState: state, restoredAt: null })
  const windows = await self.clients.matchAll({ type: 'window' })
  windows.forEach((c) => c.postMessage({ type: 'sync-result', result }))
  // При сетевой ошибке бросаем исключение: браузер сам повторит попытку позже.
  if (result.status === 'offline' || result.status === 'server') throw new Error(result.message ?? 'retry')
}

self.addEventListener('sync', ((event: SyncEvent) => {
  if (event.tag === 'lms-outbox') event.waitUntil(backgroundSend())
}) as EventListener)

self.addEventListener('message', (event) => {
  const data = event.data as { type?: string } | undefined
  if (data?.type === 'ping') event.source?.postMessage({ type: 'pong', files: shellFiles.length })
  // Та же отправка, что и по событию sync, но по просьбе страницы. Нужна для проверки фонового пути
  // в браузерах, где Background Sync недоступен; принимаются сообщения только со своего адреса.
  if (data?.type === 'sync-now') event.waitUntil(backgroundSend().catch(() => undefined))
})
