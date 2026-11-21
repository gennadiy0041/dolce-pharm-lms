// Результаты и сертификаты, опросы 180/360, профиль и состояние устройства.
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useLiveQuery } from '../../local/live'
import { Award, CircleAlert, CircleCheck, ListChecks, LogOut, Printer, RefreshCw, Smartphone, Trash2 } from 'lucide-react'
import { api, openBlob } from '../../api/client'
import { formatBytes } from '../../local/download'
import { removeCourse } from '../../local/downloads'
import { promptInstall, useCanInstall } from '../../local/install'
import { isActive } from '../../local/lifecycle'
import { answerText, type AnswerItem } from '../../ui/answers'
import { useNet } from '../../local/net'
import { currentDb, deviceKind, logout, useSession, wipeLocalData } from '../../local/session'
import { refreshFromServer } from '../../local/sync'
import type { ServerState } from '../../types'
import { Empty, Loading, Modal, Notice, Pill, Spinner, errorText, fmtDateTime, fmtNum, plural, toast, useAsync } from '../../ui/kit'
import { DEVICE_WORD, usePending } from './Shell'

// ---------- результаты ----------
export function HistoryPage() {
  const state = useLiveQuery(() => currentDb().state.get('server'), [], undefined)
  const net = useNet()
  const [review, setReview] = useState<string | null>(null)
  const data = state?.value as ServerState | undefined
  if (!data) return net.reachable === false ? <Empty title="Нет данных">Результаты появятся после первой синхронизации.</Empty> : <Loading />
  const attempts = data.assignments.flatMap((a) => a.attempts.map((t) => ({ t, a }))).sort((x, y) => Date.parse(y.t.submitted_at) - Date.parse(x.t.submitted_at))
  const open = async (fileId: string, number: string, download: boolean) => {
    try {
      await openBlob(`/api/files/${fileId}/content`, `Сертификат ${number}.pdf`, download)
    } catch (e) {
      toast(net.reachable === false ? 'Сертификат откроется, когда появится связь.' : errorText(e), 'error')
    }
  }
  return (
    <div className="stack">
      <h1>Результаты</h1>
      <section className="stack-sm">
        <h2>Сертификаты</h2>
        {data.certificates.length === 0 && <p className="muted">Сертификат появляется автоматически после успешной аттестации.</p>}
        {data.certificates.map((c) => (
          <article key={c.id} className="card stack-sm">
            <div className="row"><Pill tone="ok" icon={<Award aria-hidden />}>Аттестован</Pill><span className="muted small">№ {c.number}</span></div>
            <h3>{c.title}</h3>
            <p className="muted small">{c.sop_code ? `${c.sop_code} · ` : ''}редакция {c.revision} · выдан {fmtDateTime(c.issued_at)}</p>
            <div className="row">
              <button className="btn btn-primary grow" onClick={() => void open(c.file_id, c.number, false)}><Printer aria-hidden /> Открыть и распечатать</button>
              <button className="btn btn-secondary" onClick={() => void open(c.file_id, c.number, true)}>Скачать PDF</button>
            </div>
          </article>
        ))}
      </section>
      <section className="stack-sm">
        <h2>История аттестаций</h2>
        {attempts.length === 0 && <p className="muted">Пока нет ни одной сданной попытки.</p>}
        {attempts.length > 0 && (
          <div className="card listcard">
            {attempts.map(({ t, a }) => (
              <div key={t.id} className="task" style={{ cursor: 'default' }}>
                <div className="grow stack-sm" style={{ gap: '0.25rem' }}>
                  <b>{a.version.title}</b>
                  <span className="muted small">редакция {a.version.revision} · попытка {t.attempt_no} · {fmtDateTime(t.submitted_at)}</span>
                  <div className="row">
                    <Pill tone={t.status === 'passed' ? 'ok' : t.status === 'failed' ? 'danger' : 'warn'}
                      icon={t.status === 'passed' ? <CircleCheck aria-hidden /> : <CircleAlert aria-hidden />}>{t.status_label}</Pill>
                    <span className="small">{t.percent !== null ? `${fmtNum(t.percent)} % (${fmtNum(t.score)} из ${fmtNum(t.max_score)})` : 'итог после проверки открытого ответа'}</span>
                  </div>
                  {t.review_comment && <p className="quote">Комментарий методиста: {t.review_comment}</p>}
                  <div><button className="btn btn-ghost btn-sm" style={{ marginLeft: '-0.6rem' }} onClick={() => setReview(t.id)}>
                    <ListChecks aria-hidden /> Разбор ответов</button></div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
      {review && <AttemptReview id={review} onClose={() => setReview(null)} />}
    </div>
  )
}

interface AttemptReviewData {
  id: string; status: string; status_label: string; score: number; max_score: number; percent: number | null
  pass_percent: number; review_comment: string; course_title: string; revision: number; attempt_no: number
  items: AnswerItem[]
}

/** Разбор попытки: что верно, что нет, баллы и комментарии методиста. Правильные ответы не раскрываются —
 *  иначе следующая попытка проверяла бы память на ключ, а не знание инструкции. Разбор, открытый один раз,
 *  сохраняется на устройстве и открывается без сети. */
function AttemptReview({ id, onClose }: { id: string; onClose: () => void }) {
  const net = useNet()
  const cached = useLiveQuery(() => currentDb().state.get(`attempt:${id}`), [id], undefined)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (net.reachable === false) return
    api.get<AttemptReviewData>(`/api/attempts/${id}`)
      .then((value) => (isActive() ? currentDb().state.put({ key: `attempt:${id}`, value, savedAt: new Date().toISOString() }) : undefined))
      .catch((e) => setError(errorText(e)))
  }, [id, net.reachable])
  const data = cached?.value as AttemptReviewData | undefined
  const wrong = data?.items.filter((i) => i.is_correct === false).length ?? 0
  return (
    <Modal title="Разбор ответов" size="wide" onClose={onClose}>
      {!data && error && <Notice tone="danger" title="Разбор не загрузился">{error}</Notice>}
      {!data && !error && (net.reachable === false
        ? <Empty title="Нужна связь">Разбор загрузится, когда телефон будет в сети, и потом откроется и без неё.</Empty>
        : <Loading />)}
      {data && (
        <div className="stack">
          <div>
            <b>{data.course_title}</b>
            <p className="muted small">редакция {data.revision} · попытка {data.attempt_no} · {data.status_label}
              {data.percent !== null ? ` · ${fmtNum(data.percent)} % при пороге ${data.pass_percent} %` : ''}</p>
          </div>
          {wrong > 0 && data.status !== 'passed' && (
            <Notice tone="info">Ошибок: {wrong}. Правильные ответы не показываются — перечитайте материалы курса перед следующей попыткой.</Notice>
          )}
          {data.review_comment && <p className="quote">Комментарий методиста: {data.review_comment}</p>}
          <ol className="stack-sm review-list">
            {data.items.map((item, i) => {
              const partial = item.is_correct === false && (item.points ?? 0) > 0
              return (
                <li key={item.question_id} className="card stack-sm">
                  <p><b>{i + 1}. {item.text}</b></p>
                  <p className="small"><span className="muted">Ваш ответ: </span>{answerText(item, item.answer)}</p>
                  <div>
                    {item.is_correct === true && <Pill tone="ok" icon={<CircleCheck aria-hidden />}>Верно · {fmtNum(item.points ?? item.max_points)} из {item.max_points}</Pill>}
                    {partial && <Pill tone="warn" icon={<CircleAlert aria-hidden />}>Частично · {fmtNum(item.points ?? 0)} из {item.max_points}</Pill>}
                    {item.is_correct === false && !partial && <Pill tone="danger" icon={<CircleAlert aria-hidden />}>Неверно · 0 из {item.max_points}</Pill>}
                    {item.is_correct === null && <Pill tone="warn">{item.answer === null ? 'Нет ответа' : 'На проверке у методиста'}</Pill>}
                  </div>
                  {item.comment && <p className="quote">Комментарий методиста: {item.comment}</p>}
                </li>
              )
            })}
          </ol>
        </div>
      )}
    </Modal>
  )
}

// ---------- опросы 180/360 ----------
interface Task {
  rater_row_id: string; title: string; mode: string; status: string; role: string; role_label: string
  subject_name: string; subject_position: string; submitted_at: string | null
  scores: Record<string, number | null>; comment: string
  competencies: { id: string; name: string; description: string }[]
}

export function AssessmentsPage() {
  const net = useNet()
  const { data, error, loading, reload } = useAsync(() => api.get<Task[]>('/api/me/assessments'), [net.reachable])
  const [open, setOpen] = useState<Task | null>(null)
  if (net.reachable === false) return <Empty title="Опросы доступны при связи с сервером">Оценки коллег отправляются сразу и на устройстве не хранятся.</Empty>
  if (loading && !data) return <Loading />
  if (error) return <Notice tone="danger" title="Не удалось загрузить опросы">{error}</Notice>
  return (
    <div className="stack">
      <h1>Опросы</h1>
      <p className="muted">Оценка 180/360: вы оцениваете себя или коллегу по шкале от 1 до 5. Ответы видит только отдел обучения — в виде сводки.</p>
      {data?.length === 0 && <Empty title="Опросов нет">Когда вас попросят оценить коллегу, опрос появится здесь.</Empty>}
      {data?.map((t) => (
        <article key={t.rater_row_id} className="card stack-sm">
          <div className="row">
            <Pill tone="brand">{t.mode}°</Pill><Pill>{t.role_label}</Pill>
            {t.submitted_at ? <Pill tone="ok" icon={<CircleCheck aria-hidden />}>Отправлено</Pill> : <Pill tone="warn">Ждёт вашего ответа</Pill>}
          </div>
          <h3>{t.role === 'self' ? 'Оцените себя' : `Оцените: ${t.subject_name}`}</h3>
          <p className="muted small">{t.subject_position}{t.submitted_at ? ` · отправлено ${fmtDateTime(t.submitted_at)}` : ''}</p>
          <button className={`btn ${t.submitted_at ? 'btn-secondary' : 'btn-primary'} btn-block`} disabled={t.status !== 'open'} onClick={() => setOpen(t)}>
            {t.status !== 'open' ? 'Сбор ответов завершён' : t.submitted_at ? 'Изменить оценки' : 'Заполнить'}
          </button>
        </article>
      ))}
      {open && <AssessmentForm task={open} onClose={() => setOpen(null)} onSaved={() => { setOpen(null); reload(); void refreshFromServer() }} />}
    </div>
  )
}

function AssessmentForm({ task, onClose, onSaved }: { task: Task; onClose: () => void; onSaved: () => void }) {
  const [scores, setScores] = useState<Record<string, number | null>>(() => ({ ...task.scores }))
  const [comment, setComment] = useState(task.comment ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      const body: Record<string, number | null> = {}
      task.competencies.forEach((c) => { body[c.id] = scores[c.id] ?? null })
      await api.post(`/api/me/assessments/${task.rater_row_id}`, { scores: body, comment })
      toast('Оценки отправлены', 'ok')
      onSaved()
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal title={task.role === 'self' ? 'Самооценка' : task.subject_name} onClose={onClose} actions={<>
      <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
      <button className="btn btn-primary" onClick={() => void save()} disabled={busy}>{busy ? <Spinner /> : null} Отправить</button>
    </>}>
      <div className="stack">
        <p className="muted">1 — проявляется редко, 5 — проявляется всегда. Если не можете оценить — нажмите «Не могу оценить»: пропуск не считается нулём.</p>
        {task.competencies.map((c) => {
          const value = scores[c.id] ?? null
          return (
            <fieldset key={c.id} style={{ border: 0, padding: 0, margin: 0 }} className="stack-sm">
              <legend className="label" style={{ padding: 0 }}>{c.name}</legend>
              <span className="hint">{c.description}</span>
              <div className="scale" role="group" aria-label={c.name}>
                {[1, 2, 3, 4, 5].map((n) => (
                  <button key={n} type="button" aria-pressed={value === n} onClick={() => setScores((s) => ({ ...s, [c.id]: n }))}>{n}</button>
                ))}
                <button type="button" className="skip" aria-pressed={value === null} onClick={() => setScores((s) => ({ ...s, [c.id]: null }))}>Не могу оценить</button>
              </div>
            </fieldset>
          )
        })}
        <div className="field">
          <label htmlFor="as-comment">Комментарий (по желанию)</label>
          <textarea id="as-comment" className="textarea" rows={3} value={comment} onChange={(e) => setComment(e.target.value)} />
        </div>
        {error && <p className="error-text" role="alert">{error}</p>}
      </div>
    </Modal>
  )
}

// ---------- профиль и устройство ----------
interface Diagnostics {
  secure: boolean; sw: 'active' | 'none' | 'unsupported'; shell: boolean; persisted: boolean | null
  backgroundSync: boolean; usage: number | null; quota: number | null; standalone: boolean
}

async function diagnose(): Promise<Diagnostics> {
  const d: Diagnostics = {
    secure: window.isSecureContext, sw: 'unsupported', shell: false, persisted: null, backgroundSync: false,
    usage: null, quota: null, standalone: window.matchMedia('(display-mode: standalone)').matches || (navigator as Navigator & { standalone?: boolean }).standalone === true,
  }
  if ('serviceWorker' in navigator) {
    const reg = await navigator.serviceWorker.getRegistration()
    d.sw = reg?.active ? 'active' : 'none'
    d.backgroundSync = !!reg && 'sync' in reg
    try {
      d.shell = !!(await caches.match('/index.html', { ignoreSearch: true }))
    } catch {
      d.shell = false
    }
  }
  try {
    d.persisted = (await navigator.storage?.persisted?.()) ?? null
    const est = await navigator.storage?.estimate?.()
    d.usage = est?.usage ?? null
    d.quota = est?.quota ?? null
  } catch {
    /* оценка хранилища недоступна */
  }
  return d
}

function Check({ ok, children, hint }: { ok: boolean | null; children: string; hint?: string }) {
  return (
    <li className="row" style={{ alignItems: 'flex-start', flexWrap: 'nowrap', padding: '0.35rem 0' }}>
      <span style={{ color: ok ? 'var(--ok)' : ok === null ? 'var(--muted)' : 'var(--danger)', flex: 'none' }} aria-hidden>
        {ok ? <CircleCheck /> : <CircleAlert />}
      </span>
      <span><b>{ok ? 'Да' : ok === null ? 'Неизвестно' : 'Нет'}</b> — {children}{hint && <span className="muted small" style={{ display: 'block' }}>{hint}</span>}</span>
    </li>
  )
}

export function ProfilePage() {
  const { account } = useSession()
  const navigate = useNavigate()
  const pending = usePending()
  const [diag, setDiag] = useState<Diagnostics | null>(null)
  const [confirmWipe, setConfirmWipe] = useState(false)
  const [confirmLogout, setConfirmLogout] = useState(false)
  const [busy, setBusy] = useState(false)
  const packages = useLiveQuery(() => currentDb().packages.toArray(), [], [])
  const pins = useLiveQuery(() => currentDb().libraryPins.count(), [], 0)
  const canInstall = useCanInstall()
  const run = () => void diagnose().then(setDiag)
  useEffect(run, [])
  if (!account) return null
  const u = account.user

  const doLogout = async () => {
    setBusy(true)
    await logout()
    navigate('/', { replace: true })
  }

  return (
    <div className="stack">
      <h1>Профиль</h1>
      <div className="card stack-sm">
        <h2>{u.full_name}</h2>
        <p className="muted">{u.position}{u.department ? ` · ${u.department}` : ''}</p>
        <p className="muted small">Табельный № {u.personnel_no} · {u.role_label} · логин {u.login}</p>
      </div>

      <section className="card stack-sm" aria-labelledby="diag-title">
        <div className="spread">
          <h2 id="diag-title">Проверка автономной работы</h2>
          <button className="btn btn-ghost btn-sm" onClick={run}><RefreshCw aria-hidden /> Проверить</button>
        </div>
        {!diag ? <Spinner label="Проверяем…" /> : (
          <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
            <Check ok={diag.secure} hint={diag.secure ? undefined : 'Откройте платформу по адресу https:// — см. «Подключение телефона».'}>защищённый адрес (HTTPS)</Check>
            <Check ok={diag.sw === 'active'} hint={diag.sw === 'active' ? undefined : 'Обновите страницу. Без этого приложение не откроется без сети.'}>Service Worker зарегистрирован</Check>
            <Check ok={diag.shell}>оболочка приложения сохранена на устройстве</Check>
            <Check ok={diag.standalone} hint={diag.standalone ? undefined : 'Меню браузера → «Добавить на главный экран».'}>запущено с домашнего экрана</Check>
            <Check ok={diag.backgroundSync} hint={diag.backgroundSync ? 'Ответы уйдут даже при закрытом приложении.' : 'В этом браузере ответы уйдут при следующем открытии приложения.'}>фоновая отправка (Background Sync)</Check>
            <Check ok={diag.persisted} hint={diag.persisted ? undefined : 'Браузер может очистить данные при нехватке места. Установка на домашний экран обычно включает защиту.'}>хранилище защищено от автоочистки</Check>
          </ul>
        )}
        {canInstall && !diag?.standalone && (
          <button className="btn btn-primary btn-block" onClick={() => void promptInstall().then((ok) => ok && toast('Ярлык добавлен на главный экран', 'ok'))}>
            <Smartphone aria-hidden /> Установить на главный экран
          </button>
        )}
        {diag?.usage !== null && diag?.quota && (
          <p className="muted small">Занято {formatBytes(diag.usage ?? 0)} из доступных {formatBytes(diag.quota)}.</p>
        )}
      </section>

      <section className="card stack-sm">
        <h2>Данные {DEVICE_WORD}</h2>
        <p className="muted small">
          Скачано курсов: {packages.filter((p) => p.status === 'ready').length}. Справочников: {pins}. Ждут отправки: {pending} {plural(pending, 'запись', 'записи', 'записей')}.
        </p>
        {packages.map((p) => {
          // не до конца скачанный курс тоже занимает место — его должно быть видно и можно удалить
          const m = p.manifest ?? p.pending
          const title = m?.title ?? 'Курс, скачанный не полностью'
          const complete = p.status === 'ready' && !!p.manifest
          return (
            <div key={p.versionId} className="spread">
              <span className="grow">{title} <span className="muted small">
                {m ? `· ред. ${m.revision} ` : ''}· {complete ? formatBytes(p.totalBytes) : `скачано ${formatBytes(p.doneBytes)} из ${formatBytes(p.totalBytes)}`}
              </span></span>
              <button className="btn btn-ghost btn-sm" onClick={() => void removeCourse(currentDb(), p.versionId)} aria-label={`Удалить курс ${title} с устройства`}><Trash2 aria-hidden /> Удалить</button>
            </div>
          )
        })}
        <button className="btn btn-danger btn-block" onClick={() => setConfirmWipe(true)}><Trash2 aria-hidden /> Очистить данные на этом устройстве</button>
        <p className="muted small">Удаляются скачанные курсы, справочники и журнал. Неотправленные ответы остаются до отправки. Рекомендуется для личного телефона через {account.settings.local_retention_days} {plural(account.settings.local_retention_days, 'день', 'дня', 'дней')} после сдачи.</p>
      </section>

      <button className="btn btn-secondary btn-block" onClick={() => setConfirmLogout(true)}><LogOut aria-hidden /> Выйти</button>
      <p className="muted small center">Устройство: {deviceKind() === 'phone' ? 'телефон' : 'компьютер'}</p>

      {confirmWipe && (
        <Modal title="Очистить данные?" onClose={() => setConfirmWipe(false)} actions={<>
          <button className="btn btn-secondary" onClick={() => setConfirmWipe(false)}>Отмена</button>
          <button className="btn btn-danger" onClick={async () => {
            const { kept } = await wipeLocalData()
            setConfirmWipe(false)
            run()
            toast(kept ? `Очищено. Неотправленных записей сохранено: ${kept}` : 'Данные на устройстве очищены', 'ok')
          }}>Очистить</button>
        </>}>
          <p>Курсы и справочники будут удалены с устройства. Их можно скачать снова. Данные других учётных записей,
            которые входили на этом устройстве и уже всё отправили, тоже удаляются.</p>
          {pending > 0 && <Notice tone="warn" title={`Не отправлено записей: ${pending}`}>Они останутся на устройстве и уйдут при появлении связи.</Notice>}
        </Modal>
      )}
      {confirmLogout && (
        <Modal title="Выйти из учётной записи?" onClose={() => setConfirmLogout(false)} actions={<>
          <button className="btn btn-secondary" onClick={() => setConfirmLogout(false)} disabled={busy}>Остаться</button>
          <button className="btn btn-primary" onClick={() => void doLogout()} disabled={busy}>{busy ? <Spinner /> : null} Выйти</button>
        </>}>
          {pending > 0
            ? <Notice tone="warn" title={`Не отправлено записей: ${pending}`}>Они сохранятся на устройстве для вашей учётной записи и уйдут после следующего входа.</Notice>
            : <p>Скачанные курсы останутся на устройстве и будут доступны после входа.</p>}
        </Modal>
      )}
    </div>
  )
}
