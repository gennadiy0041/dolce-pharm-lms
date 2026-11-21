// Каркас кабинета сотрудника: шапка со статусом отправки, содержимое, нижняя панель.
import { useEffect, useState, type ReactNode } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useLiveQuery } from '../../local/live'
import {
  BookOpen, CircleCheck, ClipboardList, CloudUpload, History, RefreshCw, Target, TriangleAlert, User as UserIcon, Users, WifiOff,
} from 'lucide-react'
import { useNet } from '../../local/net'
import { currentDb, deviceKind, useSession } from '../../local/session'
import { refreshFromServer, requestSync, useSync } from '../../local/sync'
import type { ServerState } from '../../types'
import { Modal, Notice, Spinner, fmtDateTime, plural } from '../../ui/kit'

export const DEVICE_WORD = deviceKind() === 'phone' ? 'на телефоне' : 'на этом компьютере'

export function usePending(): number {
  return useLiveQuery(() => currentDb().outbox.count(), [], 0)
}

function SyncChip() {
  const net = useNet()
  const sync = useSync()
  const { needsLogin } = useSession()
  const pending = usePending()
  const [open, setOpen] = useState(false)
  const rejected = useLiveQuery(() => currentDb().rejected.toArray(), [], [])
  const log = useLiveQuery(() => (open ? currentDb().log.orderBy('id').reverse().limit(12).toArray() : []), [open], [])
  const lastSync = useLiveQuery(() => currentDb().state.get('lastSync'), [], undefined)
  const offline = net.reachable === false

  let label: string
  let icon: ReactNode
  let warn = false
  if (needsLogin) { label = 'Нужен вход'; icon = <TriangleAlert aria-hidden />; warn = true }
  else if (offline) { label = 'Нет связи'; icon = <WifiOff aria-hidden />; warn = true }
  else if (sync.phase === 'sending') { label = 'Отправляем…'; icon = <CloudUpload aria-hidden /> }
  else if (pending > 0) { label = `Ждут отправки: ${pending}`; icon = <CloudUpload aria-hidden />; warn = sync.phase === 'error' }
  else { label = 'Всё отправлено'; icon = <CircleCheck aria-hidden /> }

  return (
    <>
      <button className={`syncchip ${warn ? 'warn' : ''}`} onClick={() => setOpen(true)} aria-label={`Состояние связи: ${label}. Подробнее`}>
        {icon}<span>{label}</span>
      </button>
      {open && (
        <Modal title="Связь и отправка" onClose={() => setOpen(false)}>
          <div className="stack">
            {needsLogin
              ? <Notice tone="warn" title="Нужен повторный вход" action={<NavLink className="btn btn-primary" to="/relogin" onClick={() => setOpen(false)}>Войти</NavLink>}>
                Сессия завершена. Данные сохранены {DEVICE_WORD} и уйдут сразу после входа{offline ? ', когда появится связь' : ''}.
              </Notice>
              : offline
                ? <Notice tone="warn" title="Нет связи с сервером">Можно продолжать: всё сохраняется {DEVICE_WORD} и отправится само, когда связь вернётся.</Notice>
                : <Notice tone="ok" title="Сервер доступен">Записи отправляются автоматически.</Notice>}
            <div className="card card-flat card-tint stack-sm">
              <div className="spread"><span>Ждут отправки</span><b className="num">{pending} {plural(pending, 'запись', 'записи', 'записей')}</b></div>
              <div className="spread"><span>Последняя отправка</span><b>{fmtDateTime((lastSync?.value as { at?: string } | undefined)?.at ?? null)}</b></div>
              {sync.phase === 'error' && <p className="error-text">{sync.message}</p>}
            </div>
            {rejected.length > 0 && (
              <Notice tone="danger" title={`Отклонено сервером: ${rejected.length}`}>
                <ul style={{ margin: '0.3rem 0 0', paddingLeft: '1.2rem' }}>
                  {rejected.slice(0, 5).map((r) => <li key={r.id}>{r.reason}</li>)}
                </ul>
              </Notice>
            )}
            {!needsLogin && (
              <>
                <button className="btn btn-secondary btn-block" disabled={sync.phase === 'sending'}
                  onClick={() => void requestSync('manual').then(() => refreshFromServer())}>
                  {sync.phase === 'sending' ? <Spinner /> : <RefreshCw aria-hidden />} Отправить сейчас
                </button>
                <p className="muted small">Кнопка нужна только после ошибки. Обычно отправка идёт сама.</p>
              </>
            )}
            {log.length > 0 && (
              <details>
                <summary className="label" style={{ cursor: 'pointer', minHeight: '2.5rem' }}>Журнал на устройстве</summary>
                <ul className="small muted" style={{ paddingLeft: '1.1rem', margin: '0.3rem 0 0' }}>
                  {log.map((l) => <li key={l.id}>{fmtDateTime(l.at)} — {l.text}</li>)}
                </ul>
              </details>
            )}
          </div>
        </Modal>
      )}
    </>
  )
}

export function EmployeeShell() {
  const { account, needsLogin } = useSession()
  const net = useNet()
  const location = useLocation()
  const state = useLiveQuery(() => currentDb().state.get('server'), [], undefined)
  const pendingAssessments = (state?.value as ServerState | undefined)?.pending_assessments ?? 0
  const isManager = account?.user.role === 'manager'

  // назначения и результаты подтягиваются сами: при входе, при возврате связи,
  // при возврате в приложение, при открытии списка заданий и раз в 10 секунд
  useEffect(() => {
    if (net.reachable !== true) return
    void refreshFromServer()
    const t = setInterval(() => {
      if (document.visibilityState === 'visible') void refreshFromServer()
    }, 10000)
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refreshFromServer()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      clearInterval(t)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [net.reachable])

  useEffect(() => {
    window.scrollTo(0, 0)
    if (location.pathname === '/' && net.reachable === true) void refreshFromServer()
  }, [location.pathname]) // eslint-disable-line react-hooks/exhaustive-deps

  const tabs = [
    { to: '/', label: 'Задания', icon: ClipboardList, end: true },
    { to: '/library', label: 'Библиотека', icon: BookOpen },
    { to: '/history', label: 'Результаты', icon: History },
    { to: '/assessments', label: 'Опросы', icon: Target, dot: pendingAssessments },
    ...(isManager ? [{ to: '/team', label: 'Участок', icon: Users }] : []),
    { to: '/profile', label: 'Профиль', icon: UserIcon },
  ]

  return (
    <div className="shell">
      <a className="sr-only" href="#main">К содержимому</a>
      <header className="topbar">
        <div className="topbar-in">
          <NavLink to="/" className="brand" aria-label="Dolce-Pharm, обучение — на главную">
            <img src="/brand/dolce-pharm.png" alt="" width={398} height={134} />
            <span>Обучение</span>
          </NavLink>
          <span className="grow" />
          <SyncChip />
        </div>
      </header>
      <main className="main" id="main">
        {needsLogin && (
          <div style={{ marginBottom: '1rem' }}>
            <Notice tone="warn" title="Войдите заново, чтобы отправить результаты"
              action={<NavLink className="btn btn-primary" to="/relogin">Войти</NavLink>}>
              Сессия завершена. Скачанные курсы и ответы остаются {DEVICE_WORD}.
            </Notice>
          </div>
        )}
        <Outlet />
      </main>
      <nav className="tabbar" aria-label="Разделы">
        <div className={`tabbar-in ${tabs.length > 5 ? 'many' : ''}`}>
          {tabs.map((t) => (
            <NavLink key={t.to} to={t.to} end={t.end} className={({ isActive }) => `tab ${isActive ? 'active' : ''}`}
              onClick={() => { if (net.reachable === true) void refreshFromServer() }}>
              <t.icon aria-hidden />
              <span>{t.label}</span>
              {!!t.dot && <span className="dot" aria-label={`Новых: ${t.dot}`}>{t.dot}</span>}
            </NavLink>
          ))}
        </div>
      </nav>
    </div>
  )
}
