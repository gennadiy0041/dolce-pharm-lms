// Сводка «Обучение смены»: работа начальника отдела обучения на одном экране —
// 1) действующая инструкция, 2) кто её изучил и сдал, 3) протокол. Отчёт по попыткам отвечает на вопрос
// «что пришло», эта страница — на вопрос «кто ещё не обучен». Обновляется сама, как и отчёт.
// Данные берутся из тех же запросов, что и остальные экраны методиста: курсы, назначения, отчёт по попыткам.
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Award, CalendarClock, ClipboardCheck, Eye, GitBranch, Pencil, Printer, ScrollText, Send, Users } from 'lucide-react'
import { api, openBlob, query } from '../../api/client'
import { useNet } from '../../local/net'
import { Empty, Loading, Notice, Pill, errorText, fmtDate, fmtDateTime, fmtDayMonth, fmtDuration, fmtNum, fmtTime, plural, toast } from '../../ui/kit'
import { AssignDialog, AssignmentsDialog, type VersionBrief } from './Courses'
import { ResultPill, SourcePill, type ReportRow } from './Reports'

interface CourseRow { id: string; title: string; sop_code: string; description: string; versions: VersionBrief[] }

type LearnState = 'passed' | 'review' | 'failed' | 'in_progress' | 'not_started'

interface AssignmentRow {
  id: string; course_id: string; version_id: string; kind: string; due_at: string | null; overdue: boolean
  user: { id: string; full_name: string; department: string | null; position: string }
  state: LearnState; state_label: string
}

/** Состояния в том порядке, в каком человек движется к аттестации; у каждого — слово и свой цвет в полосе. */
const STATES: { key: LearnState; many: string; tone: 'ok' | 'warn' | 'danger' | 'info' | 'none' }[] = [
  { key: 'passed', many: 'Сдали', tone: 'ok' },
  { key: 'review', many: 'На проверке', tone: 'warn' },
  { key: 'failed', many: 'Не сдали', tone: 'danger' },
  { key: 'in_progress', many: 'Изучают', tone: 'info' },
  { key: 'not_started', many: 'Не начали', tone: 'none' },
]
const STATE_TONE = Object.fromEntries(STATES.map((s) => [s.key, s.tone])) as Record<LearnState, 'ok' | 'warn' | 'danger' | 'info' | 'none'>

interface Loaded { courses: CourseRow[]; assignments: AssignmentRow[]; rows: ReportRow[] }

export function OverviewPage() {
  const net = useNet()
  const navigate = useNavigate()
  const [data, setData] = useState<Loaded | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<string | null>(null)
  const [fresh, setFresh] = useState<Set<string>>(new Set())
  const [assign, setAssign] = useState<VersionBrief | null>(null)
  const [list, setList] = useState<VersionBrief | null>(null)
  const [tick, setTick] = useState(0)
  const seen = useRef<Set<string> | null>(null)
  const status = useRef<Map<string, string>>(new Map())

  useEffect(() => {
    let alive = true
    const load = async () => {
      if (document.visibilityState !== 'visible') return
      try {
        const [courses, assignments, report] = await Promise.all([
          api.get<CourseRow[]>('/api/courses'),
          api.get<AssignmentRow[]>('/api/assignments'),
          api.get<{ rows: ReportRow[]; server_time: string }>('/api/reports/attempts'),
        ])
        if (!alive) return
        // новая или изменившаяся попытка подсвечивается: она пришла без действий на этом экране
        if (seen.current) {
          const added = report.rows.filter((r) => !seen.current!.has(r.attempt_id))
          const changed = report.rows.filter((r) => {
            const prev = status.current.get(r.attempt_id)
            return prev !== undefined && prev !== r.status
          })
          if (added.length || changed.length) setFresh((f) => new Set([...f, ...added.map((r) => r.attempt_id), ...changed.map((r) => r.attempt_id)]))
          added.forEach((r) => toast(`Новый результат: ${r.full_name} — ${r.status_label}`, 'ok', 8000))
        }
        seen.current = new Set(report.rows.map((r) => r.attempt_id))
        status.current = new Map(report.rows.map((r) => [r.attempt_id, r.status]))
        setData({ courses, assignments, rows: report.rows })
        setUpdatedAt(report.server_time)
        setError(null)
      } catch (e) {
        if (alive) setError(errorText(e))
      }
    }
    void load()
    const t = setInterval(load, 3000)
    const onVisible = () => void load()
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      alive = false
      clearInterval(t)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [tick])

  const cards = useMemo(() => {
    if (!data) return []
    return data.courses.flatMap((c) => {
      const published = c.versions.find((v) => v.status === 'published')
      if (!published) return []
      const draft = c.versions.find((v) => v.status === 'draft')
      const people = data.assignments.filter((a) => a.version_id === published.id)
      const count = Object.fromEntries(STATES.map((s) => [s.key, people.filter((a) => a.state === s.key).length])) as Record<LearnState, number>
      const waiting = people.filter((a) => a.state !== 'passed')
        .sort((a, b) => Number(b.overdue) - Number(a.overdue) || (a.due_at ?? '9').localeCompare(b.due_at ?? '9') || a.user.full_name.localeCompare(b.user.full_name, 'ru'))
      const attempts = data.rows.filter((r) => r.version_id === published.id)
      const toReview = attempts.filter((r) => r.status === 'review')
      return [{ course: c, published, draft, people, count, waiting, toReview, overdue: people.filter((a) => a.overdue).length,
        isFresh: attempts.some((r) => fresh.has(r.attempt_id)) }]
    })
      // недавно выпущенная редакция — первой: после обновления инструкции отдел обучения следит именно за ней
      .sort((a, b) => (b.published.published_at ?? '').localeCompare(a.published.published_at ?? '')
        || a.course.title.localeCompare(b.course.title, 'ru'))
  }, [data, fresh])

  const drafts = useMemo(() => (data?.courses ?? []).filter((c) => !c.versions.some((v) => v.status === 'published')), [data])
  const latest = useMemo(() => [...(data?.rows ?? [])].sort((a, b) => b.received_at.localeCompare(a.received_at)).slice(0, 6), [data])

  const newVersion = async (courseId: string) => {
    try {
      const res = await api.post<{ version_id: string }>(`/api/courses/${courseId}/new-version`)
      navigate(`/m/courses/${res.version_id}`)
    } catch (e) {
      toast(errorText(e), 'error')
    }
  }
  const openProtocol = (courseId: string, versionId: string) =>
    void openBlob(`/api/protocol.pdf${query({ course_id: courseId, version_id: versionId, latest_only: true })}`).catch((e) => toast(errorText(e), 'error'))

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Обучение смены</h1>
          <p>Три шага на одном экране: какая инструкция действует, кто её изучил и сдал, готов ли протокол.
            Экран обновляется сам — результат с телефона сотрудника появляется без перезагрузки страницы.</p>
        </div>
        <div className="row no-print">
          {net.reachable !== false && updatedAt && <span className="live" aria-live="polite"><i />обновлено в {fmtTime(updatedAt)}</span>}
        </div>
      </div>
      {error && <Notice tone="danger" title="Сводка не обновилась">{error}</Notice>}
      {!data && !error && <Loading />}
      {data && cards.length === 0 && (
        <Empty title="Действующих курсов пока нет" action={<Link className="btn btn-primary" to="/m/courses">Перейти к курсам</Link>}>
          Соберите курс из инструкции и опубликуйте его — здесь появится ход обучения.
        </Empty>
      )}

      {cards.map(({ course, published: v, draft, people, count, waiting, toReview, overdue, isFresh }) => {
        const total = people.length
        const allPassed = total > 0 && count.passed === total
        return (
          <section key={course.id} className={`card ov-card ${isFresh ? 'fresh' : ''}`} aria-labelledby={`ov-${course.id}`}>
            <div className="spread" style={{ alignItems: 'flex-start' }}>
              <div className="grow">
                <h2 id={`ov-${course.id}`}>{v.title}</h2>
                <p className="muted small">{v.sop_code ? `${v.sop_code} · ` : ''}редакция {v.revision}{v.published_at ? ` · действует с ${fmtDate(v.published_at)}` : ''}</p>
              </div>
              {total === 0
                ? <Pill>Никому не назначено</Pill>
                : allPassed
                  ? <Pill tone="ok" icon={<Award aria-hidden />}>Все сдали</Pill>
                  : <Pill tone={overdue ? 'danger' : 'warn'} icon={<CalendarClock aria-hidden />}>Ждём: {total - count.passed}{overdue ? ` · просрочено: ${overdue}` : ''}</Pill>}
            </div>

            <div className="ov-steps">
              <div className="ov-step">
                <div className="ov-step-head"><span className="ov-num" aria-hidden>1</span>Инструкция</div>
                <p><b>Редакция {v.revision}</b> действует</p>
                <p className="muted small">
                  {v.blocks_count} {plural(v.blocks_count, 'материал', 'материала', 'материалов')}, {v.questions_count} {plural(v.questions_count, 'вопрос', 'вопроса', 'вопросов')} в тесте
                  {draft ? `. Готовится редакция ${draft.revision} — пока она не опубликована, сотрудники сдают редакцию ${v.revision}.` : ''}
                </p>
                <div className="ov-actions">
                  {draft
                    ? <Link className="btn btn-secondary btn-sm" to={`/m/courses/${draft.id}`}><Pencil aria-hidden /> Черновик редакции {draft.revision}</Link>
                    : <button className="btn btn-secondary btn-sm" onClick={() => void newVersion(course.id)}><GitBranch aria-hidden /> Новая редакция</button>}
                  <Link className="btn btn-ghost btn-sm" to={`/m/preview/${v.id}`}><Eye aria-hidden /> Глазами сотрудника</Link>
                </div>
              </div>

              <div className="ov-step">
                <div className="ov-step-head"><span className="ov-num" aria-hidden>2</span>Обучение</div>
                {total === 0 ? (
                  <p className="muted">Курс опубликован, но ещё никому не назначен. Назначьте его сотруднику или подразделению — задание появится у них на первом экране.</p>
                ) : (
                  <>
                    <p><b className="ov-big num">{count.passed} из {total}</b> <span className="muted">сдали</span></p>
                    <div className="segbar" role="img"
                      aria-label={STATES.filter((s) => count[s.key]).map((s) => `${s.many}: ${count[s.key]}`).join(', ')}>
                      {STATES.filter((s) => count[s.key]).map((s) => <span key={s.key} className={`seg-${s.key}`} style={{ flexGrow: count[s.key] }} />)}
                    </div>
                    <div className="legend">
                      {STATES.filter((s) => count[s.key]).map((s) => <span key={s.key}><i className={`seg-${s.key}`} aria-hidden />{s.many}: <b className="num">{count[s.key]}</b></span>)}
                    </div>
                    {waiting.length > 0 && (
                      <ul className="waitlist" aria-label="Ещё не сдали">
                        {waiting.slice(0, 5).map((a) => (
                          <li key={a.id}>
                            <span className="grow"><b>{a.user.full_name}</b><span className="muted small" style={{ display: 'block' }}>
                              {a.user.department ?? '—'}{a.due_at ? ` · срок ${fmtDayMonth(a.due_at)}` : ''}{a.overdue ? ' — просрочено' : ''}</span></span>
                            <Pill tone={STATE_TONE[a.state]}>{a.state_label}</Pill>
                          </li>
                        ))}
                        {waiting.length > 5 && <li className="muted small">и ещё {waiting.length - 5} — в списке «Кому назначено»</li>}
                      </ul>
                    )}
                  </>
                )}
                <div className="ov-actions">
                  <button className={`btn btn-sm ${total === 0 ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setAssign(v)}><Send aria-hidden /> Назначить</button>
                  {total > 0 && <button className="btn btn-ghost btn-sm" onClick={() => setList(v)}><Users aria-hidden /> Кому назначено</button>}
                </div>
              </div>

              <div className="ov-step">
                <div className="ov-step-head"><span className="ov-num" aria-hidden>3</span>Протокол</div>
                <p><b>Аттестовано: {count.passed}</b>{count.failed ? ` · не аттестовано: ${count.failed}` : ''}</p>
                {toReview.length > 0 ? (
                  <Notice tone="warn" title={`${toReview.length} ${plural(toReview.length, 'ответ ждёт', 'ответа ждут', 'ответов ждут')} вашей проверки`}
                    action={<Link className="btn btn-primary btn-sm" to={`/m/attempts/${toReview[0].attempt_id}`}><ClipboardCheck aria-hidden /> Проверить</Link>}>
                    Сотрудник написал ответ своими словами. Пока он не оценён, в протоколе строка стоит отдельно — «на проверке».
                  </Notice>
                ) : (
                  <p className="muted small">{count.passed + count.failed > 0 ? 'Незавершённых проверок нет — протокол можно печатать.' : 'Протокол наполнится, когда появятся первые результаты.'}</p>
                )}
                <div className="ov-actions">
                  <button className={`btn btn-sm ${count.passed + count.failed > 0 && toReview.length === 0 ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => openProtocol(course.id, v.id)}><Printer aria-hidden /> Протокол (PDF)</button>
                  <Link className="btn btn-ghost btn-sm" to="/m/protocol"><ScrollText aria-hidden /> Период и таблица</Link>
                </div>
              </div>
            </div>
          </section>
        )
      })}

      {drafts.length > 0 && (
        <section className="card card-flat stack-sm" aria-label="Черновики">
          <h3>Ещё не опубликованы</h3>
          {drafts.map((c) => (
            <div key={c.id} className="spread">
              <span>{c.title}<span className="muted small"> · {c.sop_code || 'без кода СОП'}</span></span>
              {c.versions[0] && <Link className="btn btn-ghost btn-sm" to={`/m/courses/${c.versions[0].id}`}><Pencil aria-hidden /> Открыть черновик</Link>}
            </div>
          ))}
        </section>
      )}

      {data && (
        <section className="stack-sm" aria-labelledby="ov-latest">
          <div className="spread">
            <h2 id="ov-latest">Последние результаты</h2>
            <Link className="btn btn-ghost btn-sm" to="/m/reports">Весь отчёт с фильтрами и выгрузкой</Link>
          </div>
          {latest.length === 0 ? (
            <Empty title="Результатов пока нет">Когда сотрудник сдаст тест и его телефон выйдет на связь, строка появится здесь сама.</Empty>
          ) : (
            <div className="table-wrap">
              <table className="grid report">
                <thead><tr><th>Получено</th><th>Сотрудник</th><th>Курс</th><th>Как доставлено</th><th>Время изучения</th><th>Итог</th><th></th></tr></thead>
                <tbody>
                  {latest.map((r) => (
                    <tr key={r.attempt_id} className={fresh.has(r.attempt_id) ? 'fresh' : ''}>
                      <td className="nowrap">{fmtDateTime(r.received_at)}</td>
                      <td><div className="cell-main">{r.full_name}</div><div className="cell-sub">{r.department}</div></td>
                      <td>{r.course_title}<div className="cell-sub">{r.sop_code ? `${r.sop_code} · ` : ''}<b>редакция {r.revision}</b> · попытка {r.attempt_no}</div></td>
                      <td>
                        <SourcePill source={r.source} label={r.source_label} />
                        {r.sync_trigger && <div className="cell-sub">{r.sync_trigger_label}{r.sync_app_state_label ? `, ${r.sync_app_state_label}` : ''}</div>}
                      </td>
                      <td className="nowrap">{fmtDuration(r.material_seconds)}</td>
                      <td>
                        <ResultPill status={r.status} label={r.status_label} />
                        <div className="cell-sub" style={{ marginTop: '0.25rem' }}>{fmtNum(r.score)} из {fmtNum(r.max_score)}{r.percent !== null ? ` · ${fmtNum(r.percent)} %` : ' · итог после проверки'}</div>
                        {r.open_answers.map((a, i) => <div key={i} className="quote" title={a.question}>«{a.answer || '— нет ответа —'}»</div>)}
                      </td>
                      <td className="actions">
                        <Link className={`btn btn-sm ${r.status === 'review' ? 'btn-primary' : 'btn-secondary'}`} to={`/m/attempts/${r.attempt_id}`}>
                          {r.status === 'review' ? 'Проверить' : 'Открыть'}
                        </Link>
                        {r.certificate_file_id && (
                          <button className="btn btn-ghost btn-sm" onClick={() => void openBlob(`/api/files/${r.certificate_file_id}/content`).catch((e) => toast(errorText(e), 'error'))}
                            aria-label={`Сертификат ${r.certificate_number}`}><Award aria-hidden /> Сертификат</button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {assign && <AssignDialog version={assign} onClose={() => { setAssign(null); setTick((t) => t + 1) }} />}
      {list && <AssignmentsDialog version={list} onClose={() => { setList(null); setTick((t) => t + 1) }} />}
    </div>
  )
}
