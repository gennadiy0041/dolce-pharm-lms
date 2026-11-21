// Список курсов, создание, назначение сотрудникам и подразделениям, контроль назначений.
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { CalendarClock, Eye, GitBranch, Pencil, Plus, Send, Users } from 'lucide-react'
import { api, query } from '../../api/client'
import { formatBytes } from '../../local/download'
import { Empty, Field, Loading, Modal, Notice, Pill, Spinner, errorText, fmtDate, fmtDateTime, plural, todayIso, toast, useAsync } from '../../ui/kit'

export interface VersionBrief {
  id: string; course_id: string; revision: number; status: 'draft' | 'published' | 'superseded'; title: string; sop_code: string
  blocks_count: number; questions_count: number; published_at: string | null; assigned?: number; change_note: string
}
interface CourseRow { id: string; title: string; sop_code: string; description: string; versions: VersionBrief[] }

export const VERSION_STATUS: Record<string, { label: string; tone: 'ok' | 'warn' | 'none' }> = {
  draft: { label: 'Черновик', tone: 'warn' }, published: { label: 'Действует', tone: 'ok' }, superseded: { label: 'Заменена новой', tone: 'none' },
}

export function CoursesPage() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const { data, error, reload } = useAsync(() => api.get<CourseRow[]>('/api/courses'), [])
  const [creating, setCreating] = useState(false)
  const [assign, setAssign] = useState<VersionBrief | null>(null)
  const [list, setList] = useState<VersionBrief | null>(null)

  // после публикации редактор возвращает сюда с просьбой сразу открыть назначение
  useEffect(() => {
    const id = params.get('assign')
    if (!id || !data) return
    const v = data.flatMap((c) => c.versions).find((x) => x.id === id)
    if (v) setAssign(v)
    setParams({}, { replace: true })
  }, [params, data, setParams])

  const newVersion = async (courseId: string) => {
    try {
      const res = await api.post<{ version_id: string }>(`/api/courses/${courseId}/new-version`)
      navigate(`/m/courses/${res.version_id}`)
    } catch (e) {
      toast(errorText(e), 'error')
    }
  }

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Курсы и назначения</h1>
          <p>Курс собирается из блоков прямо в браузере, без SCORM. Опубликованная редакция не меняется — для правок создаётся новая.</p>
        </div>
        <button className="btn btn-primary" onClick={() => setCreating(true)}><Plus aria-hidden /> Новый курс</button>
      </div>
      {error && <Notice tone="danger" title="Не удалось загрузить">{error}</Notice>}
      {!data && !error && <Loading />}
      {data?.length === 0 && <Empty title="Курсов пока нет" action={<button className="btn btn-primary" onClick={() => setCreating(true)}>Создать первый курс</button>} />}
      {data?.map((c) => {
        const hasDraft = c.versions.some((v) => v.status === 'draft')
        return (
          <section key={c.id} className="card stack-sm">
            <div className="spread">
              <div>
                <h2>{c.title}</h2>
                <p className="muted small">{c.sop_code || 'без кода СОП'}{c.description ? ` · ${c.description}` : ''}</p>
              </div>
            </div>
            <div className="table-wrap" style={{ boxShadow: 'none' }}>
              <table className="grid">
                <thead><tr><th>Редакция</th><th>Состояние</th><th>Состав</th><th>Назначено</th><th></th></tr></thead>
                <tbody>
                  {[...c.versions].reverse().map((v) => (
                    <tr key={v.id}>
                      <td className="cell-main nowrap">Редакция {v.revision}</td>
                      <td>
                        <Pill tone={VERSION_STATUS[v.status].tone}>{VERSION_STATUS[v.status].label}</Pill>
                        {v.published_at && <div className="cell-sub">опубликована {fmtDate(v.published_at)}</div>}
                      </td>
                      <td className="nowrap">{v.blocks_count} {plural(v.blocks_count, 'материал', 'материала', 'материалов')}, {v.questions_count} {plural(v.questions_count, 'вопрос', 'вопроса', 'вопросов')}</td>
                      <td>{v.status === 'draft' ? '—' : v.assigned}</td>
                      <td className="actions">
                        <div className="row" style={{ justifyContent: 'flex-end' }}>
                          {v.status === 'draft' && <Link className="btn btn-primary btn-sm" to={`/m/courses/${v.id}`}><Pencil aria-hidden /> Редактировать</Link>}
                          {v.status === 'published' && <button className="btn btn-primary btn-sm" onClick={() => setAssign(v)}><Send aria-hidden /> Назначить</button>}
                          {v.status !== 'draft' && <button className="btn btn-secondary btn-sm" onClick={() => setList(v)}><Users aria-hidden /> Кому назначено</button>}
                          {v.status !== 'draft' && <Link className="btn btn-ghost btn-sm" to={`/m/courses/${v.id}`}>Состав</Link>}
                          <Link className="btn btn-ghost btn-sm" to={`/m/preview/${v.id}`}><Eye aria-hidden /> Глазами сотрудника</Link>
                          {v.status === 'published' && !hasDraft && (
                            <button className="btn btn-ghost btn-sm" onClick={() => void newVersion(c.id)}><GitBranch aria-hidden /> Новая редакция</button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )
      })}
      {creating && <CreateCourse onClose={() => setCreating(false)} />}
      {assign && <AssignDialog version={assign} onClose={() => { setAssign(null); reload() }} />}
      {list && <AssignmentsDialog version={list} onClose={() => { setList(null); reload() }} />}
    </div>
  )
}

function CreateCourse({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate()
  const [title, setTitle] = useState('')
  const [code, setCode] = useState('')
  const [revision, setRevision] = useState('1')
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const create = async () => {
    if (!title.trim()) {
      setError('Укажите название курса')
      return
    }
    const rev = Number.parseInt(revision, 10)
    if (!(rev >= 1 && rev <= 999)) {
      setError('Номер редакции — от 1 до 999')
      return
    }
    setBusy(true)
    try {
      const res = await api.post<{ version_id: string }>('/api/courses', { title, sop_code: code, description, revision: rev })
      navigate(`/m/courses/${res.version_id}`)
    } catch (e) {
      setError(errorText(e))
      setBusy(false)
    }
  }
  return (
    <Modal title="Новый курс" onClose={onClose} actions={<>
      <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
      <button className="btn btn-primary" onClick={() => void create()} disabled={busy}>{busy && <Spinner />} Создать черновик</button>
    </>}>
      <div className="stack">
        <Field label="Название" htmlFor="nc-title"><input id="nc-title" className="input" value={title} onChange={(e) => setTitle(e.target.value)} autoFocus /></Field>
        <div className="form-grid">
          <Field label="Код СОП" htmlFor="nc-code" hint="Например: СОП-ОК-015. Можно оставить пустым."><input id="nc-code" className="input" value={code} onChange={(e) => setCode(e.target.value)} /></Field>
          <Field label="Редакция" htmlFor="nc-rev" hint="Номер редакции документа, по которому строится курс."><input id="nc-rev" className="input" type="number" min={1} max={999} value={revision} onChange={(e) => setRevision(e.target.value)} /></Field>
        </div>
        <Field label="Короткое описание" htmlFor="nc-desc"><textarea id="nc-desc" className="textarea" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        {error && <p className="error-text" role="alert">{error}</p>}
      </div>
    </Modal>
  )
}

interface UserRow { id: string; full_name: string; department_id: string | null; department: string | null; role: string; position: string; is_active: boolean }

export function AssignDialog({ version, onClose }: { version: VersionBrief; onClose: () => void }) {
  const users = useAsync(() => api.get<UserRow[]>('/api/users'), [])
  const depts = useAsync(() => api.get<{ id: string; name: string }[]>('/api/departments'), [])
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [pickedDepts, setPickedDepts] = useState<Set<string>>(new Set())
  const [due, setDue] = useState(todayIso(7))
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<{ created: string[]; skipped: string[] } | null>(null)

  const people = useMemo(() => (users.data ?? []).filter((u) => u.is_active && u.role !== 'methodist'), [users.data])
  const shown = people.filter((u) => !q.trim() || `${u.full_name} ${u.department ?? ''}`.toLocaleLowerCase('ru').includes(q.trim().toLocaleLowerCase('ru')))
  const toggle = (set: Set<string>, id: string): Set<string> => {
    const next = new Set(set)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  }
  const total = new Set([...picked, ...people.filter((u) => u.department_id && pickedDepts.has(u.department_id)).map((u) => u.id)]).size

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const res = await api.post<{ created: string[]; skipped: string[] }>('/api/assignments', {
        version_id: version.id, user_ids: [...picked], department_ids: [...pickedDepts],
        due_at: due ? new Date(`${due}T23:59:00`).toISOString() : null,
      })
      setDone(res)
      toast(`Назначено: ${res.created.length}`, 'ok')
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  if (done) {
    return (
      <Modal title="Курс назначен" onClose={onClose} actions={<button className="btn btn-primary" onClick={onClose}>Готово</button>}>
        <div className="stack">
          <Notice tone="ok" title={`Назначено сотрудникам: ${done.created.length}`}>
            {done.created.join(', ') || '—'}. Задание появится у них на первом экране.
          </Notice>
          {done.skipped.length > 0 && <Notice tone="info" title="Уже было назначено">{done.skipped.join(', ')}</Notice>}
          <p className="muted">Результаты появятся в отчёте автоматически. <Link to="/m/reports">Открыть отчёт</Link></p>
        </div>
      </Modal>
    )
  }

  return (
    <Modal title="Назначить курс" size="wide" onClose={onClose} actions={<>
      <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
      <button className="btn btn-primary" onClick={() => void submit()} disabled={busy || total === 0}>
        {busy ? <Spinner /> : <Send aria-hidden />} Назначить{total ? ` (${total})` : ''}
      </button>
    </>}>
      <div className="stack">
        <p><b>{version.title}</b> · редакция {version.revision}</p>
        <Field label="Срок выполнения" htmlFor="as-due">
          <input id="as-due" type="date" className="input" style={{ maxWidth: '14rem' }} value={due} min={todayIso()} onChange={(e) => setDue(e.target.value)} />
        </Field>
        <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="label">Подразделению целиком</legend>
          <div className="row">
            {(depts.data ?? []).map((d) => (
              <label key={d.id} className="check" style={{ minHeight: '2.6rem' }}>
                <input type="checkbox" checked={pickedDepts.has(d.id)} onChange={() => setPickedDepts((s) => toggle(s, d.id))} /> {d.name}
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="label">Отдельным сотрудникам</legend>
          <input className="input" type="search" placeholder="Поиск по ФИО" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Поиск сотрудника" />
          {users.loading && <Spinner label="Загружаем сотрудников…" />}
          <div style={{ maxHeight: '16rem', overflowY: 'auto', border: '1px solid var(--line)', borderRadius: '0.6rem', padding: '0 0.7rem' }}>
            {shown.map((u) => {
              const viaDept = !!u.department_id && pickedDepts.has(u.department_id)
              return (
                <label key={u.id} className="check">
                  <input type="checkbox" checked={picked.has(u.id) || viaDept} disabled={viaDept} onChange={() => setPicked((s) => toggle(s, u.id))} />
                  <span><b>{u.full_name}</b><span className="muted small" style={{ display: 'block' }}>{u.position}{u.department ? ` · ${u.department}` : ''}</span></span>
                </label>
              )
            })}
            {shown.length === 0 && <p className="muted" style={{ padding: '0.8rem 0' }}>Никого не найдено</p>}
          </div>
        </fieldset>
        {error && <p className="error-text" role="alert">{error}</p>}
      </div>
    </Modal>
  )
}

interface AssignmentRow {
  id: string; kind: string; status: string; due_at: string | null; assigned_at: string; overdue: boolean; revision: number
  user: { full_name: string; department: string | null }; state: string; state_label: string; attempts_used: number; max_attempts: number
  last_attempt: { percent: number | null; submitted_at: string } | null
  blocks_total?: number; blocks_done?: number
}

const STATE_TONE: Record<string, 'ok' | 'warn' | 'danger' | 'info' | 'none'> = { passed: 'ok', review: 'warn', failed: 'danger', in_progress: 'info', not_started: 'none' }

export function AssignmentsDialog({ version, onClose }: { version: VersionBrief; onClose: () => void }) {
  const [onlyRe, setOnlyRe] = useState(false)
  const q = query({ version_id: version.id, kind: onlyRe ? 'reacquaint' : '', include_inactive: version.status === 'superseded' })
  const { data, error, reload } = useAsync(() => api.get<AssignmentRow[]>(`/api/assignments${q}`), [q])
  const cancel = async (id: string) => {
    try {
      await api.del(`/api/assignments/${id}`)
      reload()
    } catch (e) {
      toast(errorText(e), 'error')
    }
  }
  const re = data?.filter((a) => a.kind === 'reacquaint').length ?? 0
  return (
    <Modal title={`Назначения · редакция ${version.revision}`} size="xwide" onClose={onClose}>
      <div className="stack">
        <div className="spread">
          <p><b>{version.title}</b></p>
          <label className="check" style={{ minHeight: '2.4rem' }}>
            <input type="checkbox" checked={onlyRe} onChange={(e) => setOnlyRe(e.target.checked)} /> Только повторное ознакомление{!onlyRe && re ? ` (${re})` : ''}
          </label>
        </div>
        {error && <Notice tone="danger">{error}</Notice>}
        {!data && !error && <Loading />}
        {data?.length === 0 && <Empty title={onlyRe ? 'Повторное ознакомление никому не назначено' : 'Эта редакция никому не назначена'} />}
        {data && data.length > 0 && (
          <div className="table-wrap" style={{ boxShadow: 'none' }}>
            <table className="grid">
              <thead><tr><th>Сотрудник</th><th>Вид</th><th>Срок</th><th>Состояние</th><th>Изучено</th><th>Попытки</th><th></th></tr></thead>
              <tbody>{data.map((a) => (
                <tr key={a.id}>
                  <td><div className="cell-main">{a.user.full_name}</div><div className="cell-sub">{a.user.department}</div></td>
                  <td>{a.kind === 'reacquaint' ? <Pill tone="brand">Повторное ознакомление</Pill> : 'Первичное'}
                    {a.status !== 'active' && <div className="cell-sub">{a.status === 'superseded' ? 'заменено новой редакцией' : 'отменено'}</div>}</td>
                  <td className="nowrap"><span className="row-tight"><CalendarClock aria-hidden width={16} />{fmtDate(a.due_at)}</span>{a.overdue && <div className="cell-sub" style={{ color: 'var(--danger)' }}>просрочено</div>}</td>
                  <td><Pill tone={STATE_TONE[a.state]}>{a.state_label}</Pill>{a.last_attempt && <div className="cell-sub">{fmtDateTime(a.last_attempt.submitted_at)}</div>}</td>
                  <td className="nowrap">{a.blocks_total ? `${a.blocks_done ?? 0} из ${a.blocks_total}` : '—'}</td>
                  <td>{a.attempts_used} из {a.max_attempts}</td>
                  <td className="actions">{a.status === 'active' && a.attempts_used === 0 && <button className="btn btn-ghost btn-sm" onClick={() => void cancel(a.id)}>Отменить</button>}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </div>
    </Modal>
  )
}

export { formatBytes }
