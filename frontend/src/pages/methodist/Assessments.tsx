// Оценка 180/360: запуск опроса и сводка. Сводка пересчитывается при каждом ответе участника.
import { useEffect, useMemo, useState } from 'react'
import { CircleCheck, Hourglass, Lock, Plus } from 'lucide-react'
import { api } from '../../api/client'
import { Empty, Field, Loading, Modal, Notice, Pill, Spinner, errorText, fmtDateTime, fmtNum, toast, useAsync } from '../../ui/kit'

interface Summary {
  id: string; title: string; mode: '180' | '360'; status: string; created_at: string
  subject: { id: string; full_name: string; position: string; department: string | null }
  raters: { id: string; role: string; role_label: string; full_name: string; submitted_at: string | null }[]
  expected: number; received: number
  rows: {
    competency_id: string; name: string; description: string; self: number | null; manager: number | null
    peers_avg: number | null; peers_count: number; others_avg: number | null; answers_count: number; gap: number | null
  }[]
  explanation: string
  comments?: { role: string; role_label: string; text: string; submitted_at: string }[]
}
interface UserRow { id: string; full_name: string; department_id: string | null; department: string | null; role: string; position: string; is_active: boolean }
interface Dept { id: string; name: string; manager_id: string | null }
interface Competency { id: string; name: string; description: string }

function Score({ value }: { value: number | null }) {
  return <td className="score num">{value === null ? <span className="muted" title="Оценок нет — это не ноль">—</span> : fmtNum(value)}</td>
}

export function Assessments360Page() {
  const { data, error, reload } = useAsync(() => api.get<Summary[]>('/api/assessments'), [])
  const [creating, setCreating] = useState(false)
  // сводка обновляется сама: изменение одной оценки участника сразу видно на экране
  useEffect(() => {
    const t = setInterval(() => document.visibilityState === 'visible' && reload(), 4000)
    return () => clearInterval(t)
  }, [reload])
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Оценка 180/360</h1>
          <p>180° — самооценка и руководитель. 360° — к ним добавляются коллеги. Каждый участник входит под своей учётной записью и отвечает в разделе «Опросы».</p>
        </div>
        <button className="btn btn-primary" onClick={() => setCreating(true)}><Plus aria-hidden /> Запустить оценку</button>
      </div>
      {error && <Notice tone="danger">{error}</Notice>}
      {!data && !error && <Loading />}
      {data?.length === 0 && <Empty title="Оценок пока нет" />}
      {data?.map((a) => (
        <section key={a.id} className="card stack">
          <div className="spread">
            <div>
              <h2>{a.subject.full_name}</h2>
              <p className="muted small">{a.subject.position}{a.subject.department ? ` · ${a.subject.department}` : ''} · запущена {fmtDateTime(a.created_at)}</p>
            </div>
            <div className="row">
              <Pill tone="brand">{a.mode}°</Pill>
              <Pill tone={a.received === a.expected ? 'ok' : 'warn'}>Ответов: {a.received} из {a.expected}</Pill>
              {a.status === 'closed' ? <Pill tone="none" icon={<Lock aria-hidden />}>Сбор завершён</Pill> : <CloseCollection a={a} onDone={reload} />}
            </div>
          </div>
          <div className="row">
            {a.raters.map((r) => (
              <Pill key={r.id} tone={r.submitted_at ? 'ok' : 'none'} icon={r.submitted_at ? <CircleCheck aria-hidden /> : <Hourglass aria-hidden />}>
                {r.role_label}: {r.full_name}
              </Pill>
            ))}
          </div>
          <div className="table-wrap" style={{ boxShadow: 'none' }}>
            <table className="grid matrix">
              <thead>
                <tr>
                  <th>Компетенция</th><th>Самооценка</th><th>Руководитель</th>
                  {a.mode === '360' && <th>Среднее коллег</th>}
                  <th>Оценка окружения</th><th>Расхождение</th><th>Ответов</th>
                </tr>
              </thead>
              <tbody>
                {a.rows.map((r) => (
                  <tr key={r.competency_id}>
                    <td><div className="cell-main">{r.name}</div><div className="cell-sub">{r.description}</div></td>
                    <Score value={r.self} /><Score value={r.manager} />
                    {a.mode === '360' && <td className="score num">{r.peers_avg === null ? <span className="muted">—</span> : <>{fmtNum(r.peers_avg)}<div className="cell-sub">из {r.peers_count}</div></>}</td>}
                    <Score value={r.others_avg} />
                    <td className="score num" style={{ color: r.gap === null ? undefined : Math.abs(r.gap) >= 1 ? 'var(--danger)' : 'var(--ok)' }}>
                      {r.gap === null ? <span className="muted">—</span> : `${r.gap > 0 ? '+' : ''}${fmtNum(r.gap)}`}
                    </td>
                    <td className="score num">{r.answers_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!!a.comments?.length && (
            <div className="stack-sm">
              <h3>Комментарии участников</h3>
              {a.comments.map((c, i) => (
                <p key={i} className="quote"><b>{c.role_label}:</b> {c.text}</p>
              ))}
              {a.mode === '360' && <p className="muted small">Комментарии коллег показываются без имён.</p>}
            </div>
          )}
          <p className="muted small"><b>Как считается.</b> {a.explanation}</p>
        </section>
      ))}
      {creating && <CreateAssessment onClose={() => { setCreating(false); reload() }} />}
    </div>
  )
}

/** Новая компетенция в справочнике: добавляется и сразу отмечается для этой оценки. */
function NewCompetency({ onAdded }: { onAdded: (c: Competency) => void }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (!open) {
    return <div><button type="button" className="btn btn-ghost btn-sm" onClick={() => setOpen(true)}><Plus aria-hidden /> Добавить компетенцию</button></div>
  }
  const add = async () => {
    setBusy(true)
    setError(null)
    try {
      const c = await api.post<Competency>('/api/competencies', { name, description })
      onAdded(c)
      setName('')
      setDescription('')
      setOpen(false)
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="card stack-sm" style={{ boxShadow: 'none', border: '1px solid var(--line)' }}>
      <Field label="Название компетенции" htmlFor="comp-name">
        <input id="comp-name" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Например: Работа с оборудованием" />
      </Field>
      <Field label="Что значит хорошо (по желанию)" htmlFor="comp-desc">
        <input id="comp-desc" className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      {error && <p className="error-text" role="alert">{error}</p>}
      <div className="row">
        <button type="button" className="btn btn-primary btn-sm" onClick={() => void add()} disabled={busy || !name.trim()}>{busy && <Spinner />} Добавить</button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setOpen(false)}>Отмена</button>
      </div>
    </div>
  )
}

/** «Завершить сбор»: после этого участники не могут ни отправить, ни изменить оценки; сводка фиксируется. */
function CloseCollection({ a, onDone }: { a: Summary; onDone: () => void }) {
  const [asking, setAsking] = useState(false)
  const [busy, setBusy] = useState(false)
  const close = async () => {
    setBusy(true)
    try {
      await api.post(`/api/assessments/${a.id}/close`, {})
      toast('Сбор ответов завершён. Сводка зафиксирована.', 'ok')
      onDone()
    } catch (e) {
      toast(errorText(e), 'error')
    } finally {
      setBusy(false)
      setAsking(false)
    }
  }
  if (!asking) return <button className="btn btn-secondary btn-sm" onClick={() => setAsking(true)}><Lock aria-hidden /> Завершить сбор</button>
  const left = a.expected - a.received
  return (
    <span className="row-tight" role="group" aria-label="Подтверждение">
      <span className="small">{left > 0 ? `Не ответили: ${left}. ` : ''}Завершить?</span>
      <button className="btn btn-primary btn-sm" onClick={() => void close()} disabled={busy}>{busy && <Spinner />} Да, завершить</button>
      <button className="btn btn-ghost btn-sm" onClick={() => setAsking(false)}>Отмена</button>
    </span>
  )
}

function CreateAssessment({ onClose }: { onClose: () => void }) {
  const users = useAsync(() => api.get<UserRow[]>('/api/users'), [])
  const depts = useAsync(() => api.get<Dept[]>('/api/departments'), [])
  const comps = useAsync(() => api.get<Competency[]>('/api/competencies'), [])
  const [mode, setMode] = useState<'180' | '360'>('360')
  const [subjectId, setSubjectId] = useState('')
  const [managerId, setManagerId] = useState('')
  const [peerIds, setPeerIds] = useState<string[]>([])
  const [compIds, setCompIds] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const people = useMemo(() => (users.data ?? []).filter((u) => u.is_active && u.role !== 'methodist'), [users.data])
  const subject = people.find((u) => u.id === subjectId)

  // руководитель подставляется по подразделению, коллеги — из того же подразделения
  useEffect(() => {
    if (!subject) return
    const dept = (depts.data ?? []).find((d) => d.id === subject.department_id)
    setManagerId(dept?.manager_id && dept.manager_id !== subject.id ? dept.manager_id : '')
    setPeerIds([])
  }, [subjectId]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (comps.data && compIds.length === 0) setCompIds(comps.data.slice(0, 3).map((c) => c.id))
  }, [comps.data]) // eslint-disable-line react-hooks/exhaustive-deps

  const colleagues = people.filter((u) => u.id !== subjectId && u.id !== managerId && (!subject?.department_id || u.department_id === subject.department_id))
  const toggle = (list: string[], id: string) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id])
  const create = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.post('/api/assessments', { subject_id: subjectId, mode, competency_ids: compIds, manager_id: managerId || null, peer_ids: mode === '360' ? peerIds : [] })
      toast('Оценка запущена. Участники увидят опрос в своём кабинете.', 'ok')
      onClose()
    } catch (e) {
      setError(errorText(e))
      setBusy(false)
    }
  }

  return (
    <Modal title="Запустить оценку" size="wide" onClose={onClose} actions={<>
      <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
      <button className="btn btn-primary" onClick={() => void create()} disabled={busy || !subjectId}>{busy && <Spinner />} Запустить</button>
    </>}>
      <div className="stack">
        <div className="row" role="group" aria-label="Режим">
          <button className={`btn ${mode === '180' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setMode('180')}>180° — сам и руководитель</button>
          <button className={`btn ${mode === '360' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setMode('360')}>360° — плюс коллеги</button>
        </div>
        <div className="form-grid">
          <Field label="Кого оцениваем" htmlFor="as-subject">
            <select id="as-subject" className="select" value={subjectId} onChange={(e) => setSubjectId(e.target.value)}>
              <option value="">Выберите сотрудника</option>
              {people.map((u) => <option key={u.id} value={u.id}>{u.full_name} — {u.department ?? 'без подразделения'}</option>)}
            </select>
          </Field>
          <Field label="Руководитель" htmlFor="as-manager">
            <select id="as-manager" className="select" value={managerId} onChange={(e) => setManagerId(e.target.value)}>
              <option value="">Выберите руководителя</option>
              {people.filter((u) => u.id !== subjectId).map((u) => <option key={u.id} value={u.id}>{u.full_name}{u.role === 'manager' ? ' (руководитель)' : ''}</option>)}
            </select>
          </Field>
        </div>
        <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="label">Компетенции</legend>
          {(comps.data ?? []).map((c) => (
            <label key={c.id} className="check" style={{ minHeight: '2.4rem' }}>
              <input type="checkbox" checked={compIds.includes(c.id)} onChange={() => setCompIds((l) => toggle(l, c.id))} />
              <span><b>{c.name}</b><span className="muted small" style={{ display: 'block' }}>{c.description}</span></span>
            </label>
          ))}
          <NewCompetency onAdded={(c) => { comps.setData([...(comps.data ?? []), c]); setCompIds((l) => [...l, c.id]) }} />
        </fieldset>
        {mode === '360' && (
          <fieldset className="stack-sm" style={{ border: 0, padding: 0, margin: 0 }}>
            <legend className="label">Коллеги</legend>
            {!subject && <p className="muted">Сначала выберите сотрудника.</p>}
            {subject && colleagues.length === 0 && <p className="muted">В подразделении нет других сотрудников.</p>}
            {colleagues.map((u) => (
              <label key={u.id} className="check" style={{ minHeight: '2.4rem' }}>
                <input type="checkbox" checked={peerIds.includes(u.id)} onChange={() => setPeerIds((l) => toggle(l, u.id))} />
                <span>{u.full_name} <span className="muted small">· {u.position}</span></span>
              </label>
            ))}
          </fieldset>
        )}
        {error && <p className="error-text" role="alert">{error}</p>}
      </div>
    </Modal>
  )
}
