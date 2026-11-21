import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import '@fontsource-variable/golos-text/index.css'
import './styles.css'
import { App } from './App'
import { watchInstall } from './local/install'
import { startLifecycle } from './local/lifecycle'
import { startNetMonitor } from './local/net'
import { bootSession } from './local/session'
import { refreshFromServer } from './local/sync'

// Service Worker регистрируется сразу: после первой загрузки приложение открывается без сети.
if ('serviceWorker' in navigator && import.meta.env.PROD) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => undefined)
  })
  navigator.serviceWorker.addEventListener('message', (event) => {
    // фоновая отправка завершилась — обновляем результаты на экране
    if ((event.data as { type?: string } | undefined)?.type === 'sync-result') void refreshFromServer()
  })
}

watchInstall()
startNetMonitor()
// Уход в фон закрывает локальную базу, возврат открывает её снова (см. local/lifecycle.ts).
// До окончания загрузки сессии база открыта всегда.
startLifecycle(bootSession())

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
