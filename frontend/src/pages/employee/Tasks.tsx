// Главный экран сотрудника и страница курса. На каждом задании — одно главное действие,
// которое зависит от состояния: скачать, продолжить, пройти тест, посмотреть результат.
import { useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  Award, BookOpen, CalendarClock, Check, ChevronRight, CircleAlert, CircleCheck, CloudUpload, Download, FileText, Film, HardDrive,
  Hourglass, Link2, MessagesSquare, Presentation, RefreshCw, Sheet, Smartphone, Target, Trash2, Type, X,
} from 'lucide-react'
import { openBlob } from '../../api/client'
import { formatBytes } from '../../local/download'
import { cancelDownload, removeCourse, startDownload, useDownload } from '../../local/downloads'
import { FINAL_PHASES, discardRejected, useAssignmentView, useAssignmentViews, type AssignmentView, type Phase } from '../../local/learner'
import { useNet } from '../../local/net'
import { currentDb, deviceKind, useSession } from '../../local/session'
import { Empty, LocalLoading, Notice, Pill, ProgressBar, Spinner, dueText, fmtDateTime, fmtNum, fmtTime, plural, toast } from '../../ui/kit'
import { DEVICE_WORD } from './Shell'

const PHONE = deviceKind() === 'phone'

export const BLOCK_ICON: Record<string, typeof FileText> = {
  text: Type, link: Link2, pdf: FileText, video: Film, docx: FileText, xlsx: Sheet, pptx: Presentation, trainer: MessagesSquare,
}

function nextBlockId(v: AssignmentView): string | undefined {
  const blocks = v.manifest?.blocks ?? []
  if (!blocks.length) return undefined
  const firstOpen = blocks.find((b) => b.required && !v.done.includes(b.id)) ?? blocks.find((b) => !v.done.includes(b.id))
  if (v.lastBlockId && blocks.some((b) => b.id === v.lastBlockId) && !v.done.includes(v.lastBlockId)) return v.lastBlockId
  return (firstOpen ?? blocks[0]).id
}

/** Статус пакета: скачан ли курс и можно ли работать без сети. */
export function PackageStatus({ v }: { v: AssignmentView }) {
  const dl = useDownload(v.a.version_id)
  if (dl?.running) return <Pill tone="info" icon={<Download aria-hidden />}>Скачивается</Pill>
  if (v.pkg?.status === 'ready' && v.manifest) {
    return <Pill tone="ok" icon={<CircleCheck aria-hidden />}>Готово без сети</Pill>
  }
  if (v.pkg?.status === 'partial') return <Pill tone="warn" icon={<CircleAlert aria-hidden />}>Скачано не полностью</Pill>
  return <Pill icon={<Download aria-hidden />}>Не скачано · {formatBytes(v.a.package.total_size)}</Pill>
}

function DownloadPanel({ v, autoOpen, secondary }: { v: AssignmentView; autoOpen?: boolean; secondary?: boolean }) {
  const dl = useDownload(v.a.version_id)
  const net = useNet()
  const navigate = useNavigate()
  const total = v.a.package.total_size
  const resume = v.phase === 'resume' || (!!secondary && v.pkg?.status === 'partial')
  const run = async () => {
    // Загрузка живёт дольше экрана. На страницу курса уводим, только если человек остался там же, где
    // нажал кнопку. Сама панель к этому моменту уже снята с экрана (курс стал готовым), поэтому
    // сравнивается адрес страницы, а не то, жива ли панель.
    const startPath = window.location.pathname
    const ok = await startDownload(currentDb(), v.a.version_id, total)
    if (ok) {
      toast('Курс скачан и проверен. Можно работать без сети.', 'ok')
      if (autoOpen && !PHONE && window.location.pathname === startPath) navigate(`/a/${v.a.id}`)
    }
  }
  if (dl?.running) {
    return (
      <div className="stack-sm" aria-live="polite">
        <ProgressBar value={dl.doneBytes} max={dl.totalBytes || total} label="Скачивание курса" />
        <div className="spread small">
          <span>Скачано {formatBytes(dl.doneBytes)} из {formatBytes(dl.totalBytes || total)}</span>
          <button className="btn btn-ghost btn-sm" onClick={() => cancelDownload(v.a.version_id)}><X aria-hidden /> Остановить</button>
        </div>
        {dl.fileName && <p className="muted small">Файл: {dl.fileName}</p>}
      </div>
    )
  }
  const message = dl?.error ?? (resume ? v.pkg?.error : undefined)
  return (
    <div className="stack-sm">
      {message && (
        <Notice tone={dl?.errorKind === 'quota' ? 'danger' : 'warn'} title="Загрузка не завершена">
          {message} Курс пока не отмечен как готовый к работе без сети.
        </Notice>
      )}
      <button className={secondary ? 'btn btn-secondary btn-block' : 'btn btn-primary btn-lg btn-block'} onClick={run} disabled={net.reachable === false}>
        {resume || message ? <RefreshCw aria-hidden /> : <Download aria-hidden />}
        {resume || message ? 'Продолжить загрузку' : secondary ? 'Скачать материалы' : PHONE ? 'Скачать для работы без сети' : 'Скачать курс'}
      </button>
      <p className="muted small center">
        {net.reachable === false
          ? 'Нет связи с сервером. Скачать можно, когда связь появится.'
          : `Размер: ${formatBytes(total)}. После скачивания курс открывается без интернета.`}
      </p>
    </div>
  )
}

/** Материалы курса на сервере изменились: обновление качается отдельно, прежняя копия остаётся рабочей. */
function UpdateNotice({ v }: { v: AssignmentView }) {
  const dl = useDownload(v.a.version_id)
  const net = useNet()
  if (dl?.running) {
    return (
      <div className="stack-sm" aria-live="polite">
        <ProgressBar value={dl.doneBytes} max={dl.totalBytes || v.a.package.total_size} label="Обновление курса" />
        <div className="spread small">
          <span>Обновляем… Скачанная копия остаётся доступной.</span>
          <button className="btn btn-ghost btn-sm" onClick={() => cancelDownload(v.a.version_id)}><X aria-hidden /> Остановить</button>
        </div>
      </div>
    )
  }
  return (
    <Notice tone="info" title="Материалы курса обновлены"
      action={<button className="btn btn-secondary" disabled={net.reachable === false}
        onClick={() => void startDownload(currentDb(), v.a.version_id, v.a.package.total_size).then((ok) => ok && toast('Курс обновлён и проверен', 'ok'))}>
        <RefreshCw aria-hidden /> Обновить</button>}>
      {dl?.error ?? 'Пока обновление не скачано и не проверено, работает прежняя копия.'}
    </Notice>
  )
}

function StatusLine({ tone, icon, title, children }: { tone: 'ok' | 'warn' | 'danger' | 'info'; icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="row" style={{ alignItems: 'flex-start', flexWrap: 'nowrap' }} role="status">
      <span className={`state-ico ${tone}`} aria-hidden>{icon}</span>
      <div className="grow">
        <h3 style={{ fontSize: '1.15rem' }}>{title}</h3>
        {children && <div className="muted" style={{ marginTop: '0.2rem' }}>{children}</div>}
      </div>
    </div>
  )
}

/**
 * Одно главное действие по состоянию курса.
 * `onAgain` передаёт экран теста: там «Пройти ещё раз» и «Понятно» не меняют адрес, а начинают новую попытку.
 */
export function MainAction({ v, compact, onAgain }: { v: AssignmentView; compact?: boolean; onAgain?: () => void }) {
  const navigate = useNavigate()
  const net = useNet()
  const { needsLogin } = useSession()
  const last = v.lastAttempt
  const openCert = async () => {
    if (!last?.certificate_id) return
    try {
      const cert = (await currentDb().state.get('server'))?.value as { certificates?: { id: string; file_id: string; number: string }[] } | undefined
      const c = cert?.certificates?.find((x) => x.id === last.certificate_id)
      if (c) await openBlob(`/api/files/${c.file_id}/content`, `Сертификат ${c.number}.pdf`)
    } catch {
      toast('Сертификат откроется, когда появится связь с сервером.', 'error')
    }
  }

  switch (v.phase) {
    case 'download':
    case 'resume':
      return <DownloadPanel v={v} autoOpen />
    case 'learn': {
      const started = v.done.length > 0 || !!v.lastBlockId
      const target = nextBlockId(v)
      return (
        <div className="stack-sm">
          <button className="btn btn-primary btn-lg btn-block" onClick={() => target && navigate(`/a/${v.a.id}/b/${target}`)}>
            {started ? 'Продолжить' : 'Начать изучение'} <ChevronRight aria-hidden />
          </button>
          {!compact && v.requiredLeft > 0 && (
            <p className="muted small center">До теста осталось изучить: {v.requiredLeft} {plural(v.requiredLeft, 'материал', 'материала', 'материалов')}</p>
          )}
        </div>
      )
    }
    case 'test':
      return (
        <button className="btn btn-primary btn-lg btn-block" onClick={() => navigate(`/a/${v.a.id}/test`)}>
          Пройти тест <ChevronRight aria-hidden />
        </button>
      )
    case 'draft':
      return (
        <div className="stack-sm">
          <button className="btn btn-primary btn-lg btn-block" onClick={() => navigate(`/a/${v.a.id}/test`)}>
            Продолжить тест <ChevronRight aria-hidden />
          </button>
          <p className="muted small center">Ваши ответы сохранены {DEVICE_WORD}. Тест не сдан, пока вы не нажмёте «Сдать».</p>
        </div>
      )
    case 'saved':
      return v.savedQueued ? (
        <StatusLine tone="info" icon={PHONE ? <Smartphone /> : <HardDrive />} title={`Ответы сохранены ${DEVICE_WORD}`}>
          {needsLogin
            ? 'Сессия завершена. Войдите заново — ответы уйдут сразу после входа.'
            : net.reachable === false
              ? 'Связи нет. Отправим автоматически, как только она появится, — ничего нажимать не нужно.'
              : 'Отправляем в отдел обучения…'}
          <div className="small">Сдано в {fmtTime(v.savedLocal?.submittedAt)}</div>
        </StatusLine>
      ) : (
        <StatusLine tone="info" icon={<CloudUpload />} title="Ответы отправлены">Получаем подтверждение от сервера…</StatusLine>
      )
    case 'review':
      return (
        <StatusLine tone="ok" icon={<CircleCheck />} title="Результат получен отделом обучения">
          <div className="row" style={{ margin: '0.35rem 0' }}><Pill tone="warn" icon={<Hourglass aria-hidden />}>На проверке</Pill></div>
          В тесте есть открытый ответ — его читает методист. Итог появится здесь после проверки.
          {last && <div className="small">Получено сервером: {fmtDateTime(last.received_at)}</div>}
        </StatusLine>
      )
    case 'passed':
      return (
        <div className="stack-sm">
          <StatusLine tone="ok" icon={<Award />} title="Сдал">
            Результат: {fmtNum(last?.percent)} % ({fmtNum(last?.score)} из {fmtNum(last?.max_score)}). Проходной — {v.a.version.pass_percent} %.
          </StatusLine>
          {last?.certificate_id && (
            <button className="btn btn-secondary btn-block" onClick={openCert}><Award aria-hidden /> Открыть сертификат</button>
          )}
        </div>
      )
    case 'retry':
      return (
        <div className="stack-sm">
          <StatusLine tone="danger" icon={<CircleAlert />} title="Не сдал">
            Результат: {fmtNum(last?.percent)} %, нужно {v.a.version.pass_percent} %. {plural(v.attemptsLeft, 'Осталась', 'Осталось', 'Осталось')} {v.attemptsLeft} {plural(v.attemptsLeft, 'попытка', 'попытки', 'попыток')}.
          </StatusLine>
          <button className="btn btn-primary btn-lg btn-block" onClick={() => (onAgain ? onAgain() : navigate(`/a/${v.a.id}/test`))}>Пройти тест ещё раз</button>
        </div>
      )
    case 'exhausted':
      return (
        <StatusLine tone="danger" icon={<CircleAlert />} title="Не сдал — попытки закончились">
          Результат последней попытки: {fmtNum(last?.percent)} %. Обратитесь в отдел обучения.
        </StatusLine>
      )
    case 'rejected':
      return (
        <Notice tone="danger" title="Отдел обучения не принял ответы"
          action={<button className="btn btn-secondary" onClick={() => {
            if (v.rejected) void discardRejected(currentDb(), v.rejected.id)
            onAgain?.()
          }}>Понятно</button>}>
          {v.rejected?.reason}
        </Notice>
      )
    case 'studied':
      return <StatusLine tone="ok" icon={<CircleCheck />} title="Материалы изучены">В этом курсе нет теста.</StatusLine>
    case 'replaced':
      return (
        <StatusLine tone="info" icon={<RefreshCw />} title="Редакция заменена новой">
          Эту редакцию больше сдавать не нужно. Действующая редакция курса — в списке заданий.
          {last && <div className="small">Последняя попытка по этой редакции: {last.status_label}{last.percent !== null ? `, ${fmtNum(last.percent)} %` : ''}.</div>}
        </StatusLine>
      )
  }
}

/** Сколько материалов курса изучено: считаются только материалы этой редакции. */
export function studiedCount(v: AssignmentView): { studied: number; total: number } {
  const blocks = v.manifest?.blocks
  if (blocks) {
    const ids = new Set(blocks.map((b) => b.id))
    return { studied: v.done.filter((id) => ids.has(id)).length, total: blocks.length }
  }
  const total = v.a.version.blocks_count
  return { studied: Math.min(new Set(v.done).size, total), total }
}

function TaskMeta({ v }: { v: AssignmentView }) {
  const due = dueText(v.a.due_at)
  const final = v.phase === 'passed' || v.phase === 'studied' || v.phase === 'replaced'
  const { studied, total } = studiedCount(v)
  return (
    <div className="meta">
      <span>{v.a.version.sop_code ? `${v.a.version.sop_code} · ` : ''}редакция {v.a.version.revision}</span>
      {total > 0 && v.phase !== 'replaced' && (
        <span className="studied-count"><BookOpen aria-hidden />изучено {studied} из {total}</span>
      )}
      {!final && <span style={due.tone === 'danger' ? { color: 'var(--danger)', fontWeight: 600 } : undefined}><CalendarClock aria-hidden />{due.text}</span>}
    </div>
  )
}

/** Номер текущего шага пути задания; у завершённого задания пройдены все шаги. */
const JOURNEY_AT: Record<Phase, number> = {
  download: 0, resume: 0, learn: 1, test: 2, draft: 2, retry: 2, saved: 3, rejected: 3, review: 3,
  passed: 4, exhausted: 4, replaced: 4, studied: 4,
}

/**
 * Путь задания: «Скачать → Изучить → Тест → Итог». Человеку, который впервые открыл приложение,
 * одной кнопки мало — ему нужно видеть, сколько всего шагов и на каком он сейчас.
 */
function Journey({ v }: { v: AssignmentView }) {
  const hasTest = v.a.version.questions_count > 0
  const steps = hasTest ? ['Скачать', 'Изучить', 'Тест', 'Итог'] : ['Скачать', 'Изучить', 'Готово']
  const current = Math.min(JOURNEY_AT[v.phase], steps.length)
  return (
    <ol className="journey" aria-label="Шаги задания">
      {steps.map((label, i) => {
        const state = i < current ? 'done' : i === current ? 'current' : 'todo'
        return (
          <li key={label} className={state} aria-current={state === 'current' ? 'step' : undefined}>
            <span className="j-dot" aria-hidden>{state === 'done' ? <Check /> : i + 1}</span>
            <span className="j-label">
              {label}
              <span className="sr-only">{state === 'done' ? ' — выполнено' : state === 'current' ? ' — сейчас' : ' — впереди'}</span>
            </span>
          </li>
        )
      })}
    </ol>
  )
}

const PHASE_SHORT: Record<string, { text: string; tone: 'ok' | 'warn' | 'danger' | 'info' | 'none' }> = {
  download: { text: 'Нужно скачать', tone: 'none' }, resume: { text: 'Скачано не полностью', tone: 'warn' },
  learn: { text: 'Изучение', tone: 'info' }, test: { text: 'Готов к тесту', tone: 'info' }, draft: { text: 'Тест начат', tone: 'warn' },
  saved: { text: 'Ответы сохранены', tone: 'info' }, review: { text: 'На проверке', tone: 'warn' }, passed: { text: 'Сдал', tone: 'ok' },
  retry: { text: 'Не сдал', tone: 'danger' }, exhausted: { text: 'Не сдал', tone: 'danger' }, rejected: { text: 'Не принято', tone: 'danger' },
  studied: { text: 'Изучено', tone: 'ok' }, replaced: { text: 'Заменено новой редакцией', tone: 'none' },
}

export function HomePage() {
  const { account } = useSession()
  const { views, loaded } = useAssignmentViews()
  const net = useNet()
  if (!loaded) return <LocalLoading />
  const parts = (account?.user.full_name ?? '').split(' ')
  const greeting = parts.length >= 2 ? parts.slice(1).join(' ') : parts[0]
  const active = views.filter((v) => !FINAL_PHASES.includes(v.phase))
  const finished = views.filter((v) => FINAL_PHASES.includes(v.phase))
  const [first, ...rest] = active
  const pendingAssessments = 0

  return (
    <div className="stack">
      <p className="muted">Здравствуйте, {greeting}</p>
      {!first && (
        <Empty title={views.length ? 'Все задания выполнены' : 'Заданий пока нет'}>
          {net.reachable === false
            ? 'Нет связи с сервером. Новые задания появятся, когда связь вернётся.'
            : 'Когда отдел обучения назначит курс, он появится здесь.'}
        </Empty>
      )}
      {first && (
        <section className={`hero ${['saved', 'review'].includes(first.phase) ? 'done' : first.phase === 'draft' || first.phase === 'resume' ? 'attention' : ''}`} aria-labelledby="hero-title">
          <div className="spread">
            <span className="eyebrow">Ближайшее задание</span>
            {first.a.kind === 'reacquaint' && <Pill tone="brand">Повторное ознакомление</Pill>}
          </div>
          <h1 id="hero-title">{first.a.version.title}</h1>
          <TaskMeta v={first} />
          <Journey v={first} />
          <div className="row" style={{ margin: '0.7rem 0 1rem' }}>
            <PackageStatus v={first} />
            {first.a.version.questions_count > 0 && (
              <Pill icon={<Target aria-hidden />}>Тест: {first.a.version.questions_count} {plural(first.a.version.questions_count, 'вопрос', 'вопроса', 'вопросов')}</Pill>
            )}
          </div>
          <MainAction v={first} />
          <div className="center" style={{ marginTop: '0.8rem' }}>
            <Link to={`/a/${first.a.id}`} className="btn btn-ghost">Состав курса <ChevronRight aria-hidden /></Link>
          </div>
        </section>
      )}
      {pendingAssessments > 0 && null}
      {rest.length > 0 && (
        <section className="stack-sm" aria-label="Другие задания">
          <h2>Другие задания</h2>
          <div className="card listcard">{rest.map((v) => <TaskRow key={v.a.id} v={v} />)}</div>
        </section>
      )}
      {finished.length > 0 && (
        <section className="stack-sm" aria-label="Завершённые">
          <h2>Завершённые</h2>
          <div className="card listcard">{finished.map((v) => <TaskRow key={v.a.id} v={v} />)}</div>
        </section>
      )}
    </div>
  )
}

function TaskRow({ v }: { v: AssignmentView }) {
  const s = PHASE_SHORT[v.phase]
  return (
    <Link to={`/a/${v.a.id}`} className="task">
      <div className="grow stack-sm" style={{ gap: '0.3rem' }}>
        <b>{v.a.version.title}</b>
        <TaskMeta v={v} />
        <div className="row"><Pill tone={s.tone}>{s.text}</Pill></div>
      </div>
      <ChevronRight className="chev" aria-hidden />
    </Link>
  )
}

export function CoursePage() {
  const { id } = useParams()
  const { view: v, loaded } = useAssignmentView(id)
  const navigate = useNavigate()
  const [confirmDelete, setConfirmDelete] = useState(false)
  if (!loaded) return <LocalLoading />
  if (!v) return <Empty title="Задание не найдено" action={<Link className="btn btn-primary" to="/">К заданиям</Link>} />
  const ready = v.pkg?.status === 'ready' && !!v.manifest
  const blocks = v.manifest?.blocks ?? []
  const gated = v.a.version.gate_test && v.requiredLeft > 0
  const canTest = ready && v.a.version.questions_count > 0 && ['learn', 'test', 'draft', 'retry'].includes(v.phase) && !gated

  return (
    <div className="stack">
      <div>
        <Link to="/" className="btn btn-ghost btn-sm" style={{ marginLeft: '-0.6rem' }}>← Все задания</Link>
        <h1 style={{ marginTop: '0.3rem' }}>{v.a.version.title}</h1>
        <div style={{ marginTop: '0.4rem' }}><TaskMeta v={v} /></div>
        {v.a.kind === 'reacquaint' && (
          <div style={{ marginTop: '0.7rem' }}>
            <Notice tone="info" title="Вышла новая редакция">{v.a.version.change_note || 'Ознакомьтесь с изменениями и пройдите проверку заново.'}</Notice>
          </div>
        )}
      </div>

      <div className="card stack">
        <div className="spread">
          <PackageStatus v={v} />
          {ready && v.pkg?.verifiedAt && <span className="muted small">{formatBytes(v.pkg.totalBytes)} · проверено {fmtDateTime(v.pkg.verifiedAt)}</span>}
        </div>
        <MainAction v={v} />
        {v.updateAvailable && <UpdateNotice v={v} />}
        {/* курс пройден или ждёт проверки, но на этом устройстве его нет: материалы можно скачать и перечитать */}
        {!ready && !['download', 'resume', 'replaced'].includes(v.phase) && (
          <div className="stack-sm">
            <hr className="divider" />
            <p className="muted small">Материалы курса не сохранены на этом устройстве.</p>
            <DownloadPanel v={v} secondary />
          </div>
        )}
      </div>

      <section className="stack-sm">
        <h2>Материалы</h2>
        {!ready && <p className="muted">Список появится после скачивания: {v.a.version.blocks_count} {plural(v.a.version.blocks_count, 'материал', 'материала', 'материалов')}.</p>}
        {ready && (
          <div className="card listcard">
            <ul className="steps">
              {blocks.map((b, i) => {
                const Icon = BLOCK_ICON[b.type] ?? FileText
                const done = v.done.includes(b.id)
                return (
                  <li key={b.id}>
                    <Link to={`/a/${v.a.id}/b/${b.id}`} className="step">
                      <span className={`step-ico ${done ? 'done' : ''}`} aria-hidden>{done ? <CircleCheck /> : <Icon />}</span>
                      <span className="grow">
                        <span className="step-title">{i + 1}. {b.title || 'Материал'}</span>
                        <span className="muted small" style={{ display: 'block' }}>
                          {done ? 'Изучено' : b.required ? 'Обязательно' : 'По желанию'}
                          {b.type === 'link' && b.needs_internet ? ' · нужен интернет' : ''}
                          {b.file ? ` · ${formatBytes(b.file.size)}` : ''}
                        </span>
                      </span>
                      <ChevronRight className="muted" aria-hidden />
                    </Link>
                  </li>
                )
              })}
            </ul>
          </div>
        )}
      </section>

      {v.a.version.questions_count > 0 && (
        <section className="stack-sm">
          <h2>Тест</h2>
          <div className="card stack-sm">
            <div className="meta">
              <span>{v.a.version.questions_count} {plural(v.a.version.questions_count, 'вопрос', 'вопроса', 'вопросов')}</span>
              <span>проходной балл — {v.a.version.pass_percent} %</span>
              <span>попыток: {v.attemptsUsed} из {v.a.version.max_attempts}</span>
            </div>
            {gated && ['download', 'resume', 'learn'].includes(v.phase) && (
              <Notice tone="info">Тест откроется после изучения обязательных материалов (осталось {v.requiredLeft}).</Notice>
            )}
            {canTest && v.phase === 'learn' && (
              <button className="btn btn-secondary btn-block" onClick={() => navigate(`/a/${v.a.id}/test`)}>Перейти к тесту</button>
            )}
            {v.a.attempts.length > 0 && (
              <ul className="stack-sm" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                {v.a.attempts.map((t) => (
                  <li key={t.id} className="spread small">
                    <span>Попытка {t.attempt_no} · {fmtDateTime(t.submitted_at)}</span>
                    <Pill tone={t.status === 'passed' ? 'ok' : t.status === 'failed' ? 'danger' : 'warn'}>
                      {t.status_label}{t.percent !== null ? ` · ${fmtNum(t.percent)} %` : ''}
                    </Pill>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      )}

      {/* удалить можно и не до конца скачанный курс — иначе его куски занимали бы место без возможности убрать */}
      {(ready || !!v.pkg) && (
        <div className="center">
          <button className="btn btn-ghost btn-sm" onClick={() => setConfirmDelete(true)}>
            <Trash2 aria-hidden /> {ready ? 'Удалить курс с устройства' : 'Удалить скачанную часть курса'}
          </button>
        </div>
      )}
      {confirmDelete && (
        <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && setConfirmDelete(false)}>
          <div className="modal" role="dialog" aria-modal="true" aria-label="Удалить курс с устройства">
            <h2>Удалить курс с устройства?</h2>
            <p style={{ marginTop: '0.5rem' }}>Освободится {formatBytes(ready ? v.pkg?.totalBytes ?? 0 : v.pkg?.doneBytes ?? 0)}. Ответы и результаты останутся. Курс можно скачать снова.</p>
            <div className="modal-actions">
              <button className="btn btn-secondary" onClick={() => setConfirmDelete(false)}>Оставить</button>
              <button className="btn btn-danger" onClick={async () => {
                await removeCourse(currentDb(), v.a.version_id)
                setConfirmDelete(false)
                toast('Курс удалён с устройства')
              }}>Удалить</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export { Spinner }
