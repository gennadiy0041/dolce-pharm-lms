// Очередь проверки, карточка попытки с оценкой открытых ответов и сводный протокол.
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Award, Check, Download, Printer, X } from 'lucide-react'
import { api, openBlob, query } from '../../api/client'
import { Empty, Loading, Notice, Pill, Spinner, errorText, fmtDateTime, fmtNum, todayIso, toast, useAsync } from '../../ui/kit'
import { answerText, type AnswerItem } from '../../ui/answers'
import { ResultPill, downloadCsv, useOptions } from './Reports'

interface QueueItem {
  id: string; status: string; status_label: string; score: number; max_score: number; submitted_at: string; received_at: string
  attempt_no: number; course_title: string; revision: number; user: { full_name: string; department: string | null }
}

export function ReviewPage() {
  const { data, error, reload } = useAsync(() => api.get<QueueItem[]>('/api/review/queue'), [])
  useEffect(() => {
    const t = setInterval(() => document.visibilityState === 'visible' && reload(), 4000)
    return () => clearInterval(t)
  }, [reload])
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Проверка ответов</h1>
          <p>Попытки с открытыми ответами. Объективные вопросы уже проверены сервером; после вашей оценки сотрудник получит итог.</p>
        </div>
      </div>
      {error && <Notice tone="danger" title="Не удалось загрузить">{error}</Notice>}
      {!data && !error && <Loading />}
      {data?.length === 0 && <Empty title="Очередь пуста">Новые попытки появятся здесь автоматически.</Empty>}
      {data && data.length > 0 && (
        <div className="table-wrap"><table className="grid">
          <thead><tr><th>Сотрудник</th><th>Курс</th><th>Сдано</th><th>Получено</th><th>Предварительно</th><th></th></tr></thead>
          <tbody>{data.map((a) => (
            <tr key={a.id}>
              <td><div className="cell-main">{a.user.full_name}</div><div className="cell-sub">{a.user.department}</div></td>
              <td>{a.course_title}<div className="cell-sub">редакция {a.revision} · попытка {a.attempt_no}</div></td>
              <td className="nowrap">{fmtDateTime(a.submitted_at)}</td>
              <td className="nowrap">{fmtDateTime(a.received_at)}</td>
              <td className="nowrap">{fmtNum(a.score)} из {fmtNum(a.max_score)} <span className="cell-sub">без открытых</span></td>
              <td className="actions"><Link className="btn btn-primary btn-sm" to={`/m/attempts/${a.id}`}>Проверить</Link></td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </div>
  )
}

interface AttemptDetail {
  id: string; status: 'review' | 'passed' | 'failed'; status_label: string; score: number; max_score: number; percent: number | null
  pass_percent: number; auto_points: number; manual_points: number; submitted_at: string; received_at: string; reviewed_at: string | null
  review_comment: string; attempt_no: number; source: string; course_title: string; sop_code: string; revision: number
  certificate_id: string | null; certificate_number: string | null; certificate_file_id: string | null
  user: { full_name: string; department: string | null; personnel_no: string; position: string }
  items: AnswerItem[]
}

export function AttemptPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { data, error, loading, setData } = useAsync(() => api.get<AttemptDetail>(`/api/attempts/${id}`), [id])
  const [grades, setGrades] = useState<Record<string, { points: string; comment: string }>>({})
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  useEffect(() => {
    if (!data) return
    const g: Record<string, { points: string; comment: string }> = {}
    data.items.filter((i) => i.type === 'open').forEach((i) => {
      g[i.question_id] = { points: i.points === null ? '' : String(i.points), comment: i.comment ?? '' }
    })
    setGrades(g)
    setComment(data.review_comment ?? '')
  }, [data])

  const openItems = useMemo(() => data?.items.filter((i) => i.type === 'open') ?? [], [data])
  if (loading && !data) return <Loading />
  if (error || !data) return <Notice tone="danger" title="Попытка не открылась">{error}</Notice>
  const canGrade = !data.certificate_id && openItems.length > 0
  const preview = data.auto_points + openItems.reduce((s, i) => s + (Number(grades[i.question_id]?.points.replace(',', '.')) || 0), 0)
  const previewPct = data.max_score ? Math.round((preview / data.max_score) * 1000) / 10 : 0

  const save = async () => {
    setFormError(null)
    const payload = []
    for (const i of openItems) {
      const raw = grades[i.question_id]?.points.replace(',', '.') ?? ''
      const pts = Number(raw)
      if (raw === '' || Number.isNaN(pts) || pts < 0 || pts > i.max_points) {
        setFormError(`Поставьте баллы за каждый открытый ответ: от 0 до ${i.max_points}.`)
        return
      }
      payload.push({ question_id: i.question_id, points: pts, comment: grades[i.question_id]?.comment ?? '' })
    }
    setBusy(true)
    try {
      const next = await api.post<AttemptDetail>(`/api/attempts/${data.id}/review`, { grades: payload, comment })
      setData(next)
      toast(next.status === 'passed' ? 'Итог: сдал. Сертификат сформирован.' : 'Итог: не сдал.', next.status === 'passed' ? 'ok' : 'info')
    } catch (e) {
      setFormError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="stack" style={{ maxWidth: '56rem' }}>
      <div>
        <button className="btn btn-ghost btn-sm" style={{ marginLeft: '-0.6rem' }} onClick={() => navigate(-1)}>← Назад</button>
        <div className="page-head" style={{ marginTop: '0.3rem' }}>
          <div>
            <h1>{data.user.full_name}</h1>
            <p>{data.user.position}{data.user.department ? ` · ${data.user.department}` : ''} · таб. {data.user.personnel_no}</p>
          </div>
          <ResultPill status={data.status} label={data.status_label} />
        </div>
      </div>
      <div className="kpis">
        <div className="kpi"><b>{data.course_title}</b><span>{data.sop_code ? `${data.sop_code} · ` : ''}редакция {data.revision} · попытка {data.attempt_no}</span></div>
        <div className="kpi"><b className="num">{fmtNum(data.score)} из {fmtNum(data.max_score)}</b><span>{data.percent !== null ? `${fmtNum(data.percent)} % при пороге ${data.pass_percent} %` : `порог ${data.pass_percent} % — итог после проверки`}</span></div>
        <div className="kpi"><b>{fmtDateTime(data.submitted_at)}</b><span>сдано на устройстве · получено {fmtDateTime(data.received_at)}</span></div>
      </div>
      {data.certificate_file_id && (
        <Notice tone="ok" title={`Сертификат № ${data.certificate_number} сформирован`}
          action={<button className="btn btn-primary" onClick={() => void openBlob(`/api/files/${data.certificate_file_id}/content`).catch((e) => toast(errorText(e), 'error'))}><Award aria-hidden /> Открыть сертификат</button>}>
          Оценка зафиксирована и больше не меняется.
        </Notice>
      )}

      {data.items.map((item, i) => (
        <section key={item.question_id} className="card stack-sm">
          <div className="spread">
            <span className="q-count">Вопрос {i + 1}</span>
            {item.type !== 'open'
              ? item.is_correct
                ? <Pill tone="ok" icon={<Check aria-hidden />}>Верно · {fmtNum(item.points)} из {item.max_points}</Pill>
                : <Pill tone="danger" icon={<X aria-hidden />}>Неверно · 0 из {item.max_points}</Pill>
              : item.points === null
                ? <Pill tone="warn">Ждёт вашей оценки</Pill>
                : <Pill tone="info">Оценено · {fmtNum(item.points)} из {item.max_points}</Pill>}
          </div>
          <h3>{item.text}</h3>
          <div>
            <p className="label">Ответ сотрудника</p>
            <p className={item.type === 'open' ? 'quote' : ''} style={{ fontSize: item.type === 'open' ? '1.1rem' : undefined }}>{answerText(item, item.answer)}</p>
          </div>
          {item.type !== 'open' && !item.is_correct && (
            <p className="muted small">Верный ответ: {answerText(item, item.correct)}</p>
          )}
          {item.type === 'open' && (
            <>
              {item.criteria && <p className="muted small"><b>Критерии:</b> {item.criteria}</p>}
              {canGrade ? (
                <div className="form-grid">
                  <div className="field">
                    <label htmlFor={`pts-${item.question_id}`}>Баллы (0–{item.max_points})</label>
                    <input id={`pts-${item.question_id}`} className="input" inputMode="decimal" value={grades[item.question_id]?.points ?? ''}
                      onChange={(e) => setGrades((g) => ({ ...g, [item.question_id]: { ...g[item.question_id], points: e.target.value } }))} />
                    <div className="row-tight" style={{ marginTop: '0.3rem' }}>
                      {Array.from({ length: item.max_points + 1 }, (_, n) => (
                        <button key={n} type="button" className="btn btn-secondary btn-sm"
                          onClick={() => setGrades((g) => ({ ...g, [item.question_id]: { ...g[item.question_id], points: String(n) } }))}>{n}</button>
                      ))}
                    </div>
                  </div>
                  <div className="field">
                    <label htmlFor={`cm-${item.question_id}`}>Комментарий сотруднику</label>
                    <input id={`cm-${item.question_id}`} className="input" value={grades[item.question_id]?.comment ?? ''}
                      onChange={(e) => setGrades((g) => ({ ...g, [item.question_id]: { ...g[item.question_id], comment: e.target.value } }))} />
                  </div>
                </div>
              ) : item.comment && <p className="muted small">Комментарий: {item.comment}</p>}
            </>
          )}
        </section>
      ))}

      {canGrade && (
        <section className="card stack-sm">
          <h2>Итог проверки</h2>
          <p>После оценки: <b>{fmtNum(preview)} из {fmtNum(data.max_score)}</b> ({fmtNum(previewPct)} %) —{' '}
            {previewPct >= data.pass_percent ? <Pill tone="ok">Сдал</Pill> : <Pill tone="danger">Не сдал</Pill>}</p>
          <div className="field">
            <label htmlFor="rv-comment">Общий комментарий (по желанию)</label>
            <input id="rv-comment" className="input" value={comment} onChange={(e) => setComment(e.target.value)} />
          </div>
          {formError && <p className="error-text" role="alert">{formError}</p>}
          <button className="btn btn-primary btn-lg" onClick={() => void save()} disabled={busy}>
            {busy ? <Spinner /> : <Check aria-hidden />} {data.status === 'review' ? 'Выставить итог' : 'Изменить оценку'}
          </button>
          <p className="muted small">При положительном итоге сертификат формируется автоматически, после этого оценка не меняется.</p>
        </section>
      )}
    </div>
  )
}

interface ProtocolRow {
  attempt_id: string; full_name: string; personnel_no: string; department: string; submitted_at: string; revision: number
  score: number; max_score: number; percent: number | null; status: string; result_label: string
}
interface ProtocolData {
  course_title: string; sop_code: string; revisions: number[]; rows: ProtocolRow[]; pending: ProtocolRow[]
  summary: { total: number; passed: number; failed: number; pending: number }
}

export function ProtocolPage({ staff = false, courses: staffCourses }: { staff?: boolean; courses?: { id: string; title: string }[] }) {
  const options = useOptions(staff)
  const courses = staff ? (staffCourses ?? []) : options.courses
  const [courseId, setCourseId] = useState('')
  const [versionId, setVersionId] = useState('')
  const [departmentId, setDepartmentId] = useState('')
  const [from, setFrom] = useState(todayIso())
  const [to, setTo] = useState(todayIso())
  const [latestOnly, setLatestOnly] = useState(true)
  useEffect(() => {
    if (!courseId && courses.length) setCourseId(courses[0].id)
  }, [courses, courseId])
  const q = query({ course_id: courseId, version_id: versionId, department_id: departmentId, date_from: from, date_to: to, latest_only: latestOnly })
  const { data, error, reload } = useAsync(() => (courseId ? api.get<ProtocolData>(`/api/protocol${q}`) : Promise.resolve(undefined)), [q])
  useEffect(() => {
    const t = setInterval(() => document.visibilityState === 'visible' && reload(), 5000)
    return () => clearInterval(t)
  }, [reload])
  const course = options.courses.find((c) => c.id === courseId)

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Сводный протокол аттестации</h1>
          <p>Выберите программу и дату проверки знаний. PDF и таблица строятся из той же выборки, что показана на экране.</p>
        </div>
        <div className="row no-print">
          <button className="btn btn-primary" disabled={!courseId} onClick={() => void openBlob(`/api/protocol.pdf${q}`).catch((e) => toast(errorText(e), 'error'))}>
            <Printer aria-hidden /> Открыть PDF для печати
          </button>
          <button className="btn btn-secondary" disabled={!courseId} onClick={() => void openBlob(`/api/protocol.pdf${q}`, 'Протокол аттестации.pdf', true).catch((e) => toast(errorText(e), 'error'))}>
            <Download aria-hidden /> Скачать PDF
          </button>
          <button className="btn btn-secondary" disabled={!courseId} onClick={() => void downloadCsv(`/api/protocol.csv${q}`, 'Протокол аттестации.csv')}>
            <Download aria-hidden /> Таблица (CSV)
          </button>
        </div>
      </div>
      <div className="card card-flat filters no-print">
        <div className="field">
          <label htmlFor="p-course">Программа</label>
          <select id="p-course" className="select" value={courseId} onChange={(e) => { setCourseId(e.target.value); setVersionId('') }}>
            {courses.length === 0 && <option value="">Нет курсов</option>}
            {courses.map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
          </select>
        </div>
        {course && (
          <div className="field">
            <label htmlFor="p-ver">Редакция</label>
            <select id="p-ver" className="select" value={versionId} onChange={(e) => setVersionId(e.target.value)}>
              <option value="">Все редакции</option>
              {course.versions.filter((v) => v.status !== 'draft').map((v) => <option key={v.id} value={v.id}>Редакция {v.revision}</option>)}
            </select>
          </div>
        )}
        <div className="field">
          <label htmlFor="p-from">Дата проверки с</label>
          <input id="p-from" type="date" className="input" value={from} onChange={(e) => setFrom(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="p-to">по</label>
          <input id="p-to" type="date" className="input" value={to} onChange={(e) => setTo(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="p-dept">Подразделение</label>
          <select id="p-dept" className="select" value={departmentId} onChange={(e) => setDepartmentId(e.target.value)}>
            <option value="">Все</option>
            {options.departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        </div>
        <label className="check"><input type="checkbox" checked={latestOnly} onChange={(e) => setLatestOnly(e.target.checked)} /> Только последняя попытка сотрудника</label>
      </div>
      {error && <Notice tone="danger" title="Не удалось построить протокол">{error}</Notice>}
      {data && (
        <>
          <div className="kpis">
            <div className="kpi"><b className="num">{data.summary.total}</b><span>проверено</span></div>
            <div className="kpi"><b className="num">{data.summary.passed}</b><span>аттестовано</span></div>
            <div className="kpi"><b className="num">{data.summary.failed}</b><span>не аттестовано</span></div>
            <div className="kpi"><b className="num">{data.summary.pending}</b><span>незавершённые проверки</span></div>
          </div>
          <ProtocolTable title="Результаты аттестации" rows={data.rows} empty="За выбранный период завершённых проверок нет." />
          <ProtocolTable title="Незавершённые проверки" rows={data.pending} empty="Нет." pending />
        </>
      )}
    </div>
  )
}

function ProtocolTable({ title, rows, empty, pending }: { title: string; rows: ProtocolRow[]; empty: string; pending?: boolean }) {
  return (
    <section className="stack-sm">
      <h2>{title}</h2>
      {rows.length === 0 ? <p className="muted">{empty}</p> : (
        <div className="table-wrap"><table className="grid">
          <thead><tr><th>№</th><th>ФИО</th><th>Таб. №</th><th>Подразделение</th><th>Дата проверки</th><th>Редакция</th><th>Баллы</th><th>%</th><th>Итог</th></tr></thead>
          <tbody>{rows.map((r, i) => (
            <tr key={r.attempt_id}>
              <td>{i + 1}</td><td className="cell-main">{r.full_name}</td><td>{r.personnel_no}</td><td>{r.department}</td>
              <td className="nowrap">{fmtDateTime(r.submitted_at)}</td><td>{r.revision}</td>
              <td className="nowrap">{fmtNum(r.score)} из {fmtNum(r.max_score)}</td><td>{r.percent !== null ? fmtNum(r.percent) : '—'}</td>
              <td>{pending ? <Pill tone="warn">На проверке</Pill> : r.status === 'passed' ? <Pill tone="ok">Аттестован</Pill> : <Pill tone="danger">Не аттестован</Pill>}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </section>
  )
}
