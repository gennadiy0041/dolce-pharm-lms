// Сотрудники: список, добавление, экспорт и импорт CSV с предпросмотром.
import { useRef, useState } from 'react'
import { CircleAlert, CircleCheck, Download, KeyRound, Pencil, Plus, Upload } from 'lucide-react'
import { api, presentBlob, query } from '../../api/client'
import { Empty, Field, Loading, Modal, Notice, Pill, Spinner, errorText, toast, useAsync } from '../../ui/kit'
import { downloadCsv } from './Reports'

interface UserRow {
  id: string; personnel_no: string; full_name: string; login: string; role: string; role_label: string
  department_id: string | null; department: string | null; position: string; is_active: boolean
}
interface Dept { id: string; name: string; manager: string | null }

interface PreviewRow {
  line: number; personnel_no: string; full_name: string; department: string; position: string; role: string | null; login: string
  status: 'new' | 'exists' | 'error'; problems: string[]; notes: string[]
}
interface Preview { encoding: string; delimiter: string; rows: PreviewRow[]; summary: { new: number; exists: number; error: number } }
interface Created { personnel_no: string; full_name: string; login: string; password: string; department: string; note?: string | null }

export function UsersPage() {
  const [q, setQ] = useState('')
  const [dept, setDept] = useState('')
  const users = useAsync(() => api.get<UserRow[]>(`/api/users${query({ q, department_id: dept })}`), [q, dept])
  const depts = useAsync(() => api.get<Dept[]>('/api/departments'), [])
  const [adding, setAdding] = useState(false)
  const [importing, setImporting] = useState(false)
  const [editing, setEditing] = useState<UserRow | null>(null)

  const toggleActive = async (u: UserRow) => {
    try {
      const res = await api.patch<{ note?: string | null }>(`/api/users/${u.id}`, { is_active: !u.is_active })
      if (res.note) toast(res.note, 'info', 9000)
      users.reload()
    } catch (e) {
      toast(errorText(e), 'error')
    }
  }

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Сотрудники</h1>
          <p>Все данные вымышлены. Повторный импорт не создаёт дублей: сотрудник определяется по табельному номеру.</p>
        </div>
        <div className="row">
          <button className="btn btn-secondary" onClick={() => void downloadCsv('/api/users/export.csv', 'Сотрудники.csv')}><Download aria-hidden /> Экспорт в Excel (CSV)</button>
          <button className="btn btn-secondary" onClick={() => setImporting(true)}><Upload aria-hidden /> Импорт из CSV</button>
          <button className="btn btn-primary" onClick={() => setAdding(true)}><Plus aria-hidden /> Добавить</button>
        </div>
      </div>
      <div className="card card-flat filters">
        <div className="field">
          <label htmlFor="u-q">Поиск по ФИО, табельному номеру, логину</label>
          <input id="u-q" className="input" type="search" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="u-dept">Подразделение</label>
          <select id="u-dept" className="select" value={dept} onChange={(e) => setDept(e.target.value)}>
            <option value="">Все</option>
            {(depts.data ?? []).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        </div>
      </div>
      {users.error && <Notice tone="danger">{users.error}</Notice>}
      {!users.data && !users.error && <Loading />}
      {users.data?.length === 0 && <Empty title="Никого не найдено" />}
      {users.data && users.data.length > 0 && (
        <div className="table-wrap"><table className="grid">
          <thead><tr><th>Таб. №</th><th>ФИО</th><th>Подразделение</th><th>Должность</th><th>Роль</th><th>Логин</th><th>Доступ</th><th></th></tr></thead>
          <tbody>{users.data.map((u) => (
            <tr key={u.id}>
              <td className="num">{u.personnel_no}</td><td className="cell-main">{u.full_name}</td><td>{u.department ?? '—'}</td><td>{u.position}</td>
              <td><Pill tone={u.role === 'methodist' ? 'brand' : u.role === 'manager' ? 'info' : 'none'}>{u.role_label}</Pill></td>
              <td>{u.login}</td>
              <td><button className="btn btn-ghost btn-sm" onClick={() => void toggleActive(u)}>{u.is_active ? 'Активен · отключить' : 'Отключён · включить'}</button></td>
              <td className="actions"><button className="btn btn-secondary btn-sm" onClick={() => setEditing(u)}><Pencil aria-hidden /> Изменить</button></td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {editing && <EditUser user={editing} depts={depts.data ?? []} onClose={() => { setEditing(null); users.reload(); depts.reload() }} />}
      {adding && <AddUser depts={depts.data ?? []} onClose={() => { setAdding(false); users.reload() }} />}
      {importing && <ImportUsers onClose={() => { setImporting(false); users.reload(); depts.reload() }} />}
    </div>
  )
}

function AddUser({ depts, onClose }: { depts: Dept[]; onClose: () => void }) {
  const [form, setForm] = useState({ personnel_no: '', full_name: '', department_id: '', position: '', role: 'employee' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [created, setCreated] = useState<{ login: string; initial_password: string; full_name: string; note?: string | null } | null>(null)
  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      setCreated(await api.post('/api/users', { ...form, department_id: form.department_id || null }))
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  if (created) {
    return (
      <Modal title="Сотрудник добавлен" onClose={onClose} actions={<button className="btn btn-primary" onClick={onClose}>Готово</button>}>
        <Notice tone="ok" title={created.full_name}>
          Логин: <b>{created.login}</b><br />Временный пароль: <b>{created.initial_password}</b><br />
          Пароль показан один раз — передайте его сотруднику.
        </Notice>
        {created.note && <div style={{ marginTop: '0.8rem' }}><Notice tone="warn" title="Обратите внимание">{created.note}</Notice></div>}
      </Modal>
    )
  }
  return (
    <Modal title="Новый сотрудник" onClose={onClose} actions={<>
      <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
      <button className="btn btn-primary" onClick={() => void save()} disabled={busy}>{busy && <Spinner />} Добавить</button>
    </>}>
      <div className="stack">
        <div className="form-grid">
          <Field label="Табельный номер" htmlFor="nu-pn"><input id="nu-pn" className="input" value={form.personnel_no} onChange={(e) => setForm({ ...form, personnel_no: e.target.value })} /></Field>
          <Field label="Роль" htmlFor="nu-role">
            <select id="nu-role" className="select" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              <option value="employee">Сотрудник</option><option value="manager">Руководитель</option><option value="methodist">Методист</option>
            </select>
          </Field>
        </div>
        <Field label="ФИО" htmlFor="nu-name"><input id="nu-name" className="input" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></Field>
        <div className="form-grid">
          <Field label="Подразделение" htmlFor="nu-dept">
            <select id="nu-dept" className="select" value={form.department_id} onChange={(e) => setForm({ ...form, department_id: e.target.value })}>
              <option value="">—</option>{depts.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </Field>
          <Field label="Должность" htmlFor="nu-pos"><input id="nu-pos" className="input" value={form.position} onChange={(e) => setForm({ ...form, position: e.target.value })} /></Field>
        </div>
        <p className="muted small">Логин и временный пароль будут созданы автоматически.</p>
        {error && <p className="error-text" role="alert">{error}</p>}
      </div>
    </Modal>
  )
}

/** Изменение ФИО, роли, подразделения и должности; сброс пароля. Руководитель подразделения назначается
 *  по роли и подразделению — как при создании и импорте (сервер сообщит, если место уже занято). */
function EditUser({ user, depts, onClose }: { user: UserRow; depts: Dept[]; onClose: () => void }) {
  const [form, setForm] = useState({ full_name: user.full_name, role: user.role, department_id: user.department_id ?? '', position: user.position })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [password, setPassword] = useState<string | null>(null)
  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      const res = await api.patch<{ note?: string | null }>(`/api/users/${user.id}`, form)
      toast(res.note ?? 'Изменения сохранены', res.note ? 'info' : 'ok', res.note ? 9000 : undefined)
      onClose()
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  const resetPassword = async () => {
    // временный пароль без похожих символов (0/O, 1/l): его диктуют сотруднику
    const alphabet = 'abcdefghjkmnpqrstuvwxyz23456789'
    const bytes = crypto.getRandomValues(new Uint8Array(8))
    const next = Array.from(bytes, (b) => alphabet[b % alphabet.length]).join('')
    setBusy(true)
    setError(null)
    try {
      await api.patch(`/api/users/${user.id}`, { password: next })
      setPassword(next)
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal title={`Сотрудник · таб. № ${user.personnel_no}`} onClose={onClose} actions={<>
      <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
      <button className="btn btn-primary" onClick={() => void save()} disabled={busy || !form.full_name.trim()}>{busy && <Spinner />} Сохранить</button>
    </>}>
      <div className="stack">
        <Field label="ФИО" htmlFor="eu-name"><input id="eu-name" className="input" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></Field>
        <div className="form-grid">
          <Field label="Роль" htmlFor="eu-role">
            <select id="eu-role" className="select" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              <option value="employee">Сотрудник</option><option value="manager">Руководитель</option><option value="methodist">Методист</option>
            </select>
          </Field>
          <Field label="Подразделение" htmlFor="eu-dept">
            <select id="eu-dept" className="select" value={form.department_id} onChange={(e) => setForm({ ...form, department_id: e.target.value })}>
              <option value="">—</option>{depts.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </Field>
        </div>
        <Field label="Должность" htmlFor="eu-pos"><input id="eu-pos" className="input" value={form.position} onChange={(e) => setForm({ ...form, position: e.target.value })} /></Field>
        <p className="muted small">Руководитель видит отчёты своего подразделения; методист — всё и управляет платформой. Логин: <b>{user.login}</b>.</p>
        <div className="card card-flat stack-sm">
          <b>Пароль</b>
          {password
            ? <Notice tone="ok" title="Новый временный пароль">Пароль: <b>{password}</b>. Показан один раз — передайте его сотруднику. Сеансы на всех его устройствах завершены.</Notice>
            : <div><button className="btn btn-secondary btn-sm" onClick={() => void resetPassword()} disabled={busy}><KeyRound aria-hidden /> Сбросить пароль</button>
              <span className="muted small"> — сотрудник войдёт с новым временным паролем; данные на телефоне сохранятся.</span></div>}
        </div>
        {error && <p className="error-text" role="alert">{error}</p>}
      </div>
    </Modal>
  )
}

const STATUS = {
  new: { label: 'Будет создан', tone: 'ok' as const }, exists: { label: 'Уже есть — пропуск', tone: 'none' as const }, error: { label: 'Ошибка', tone: 'danger' as const },
}

function ImportUsers({ onClose }: { onClose: () => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<{ created: Created[]; skipped_existing: number; errors: number } | null>(null)

  const send = async (path: string, f: File) => {
    const form = new FormData()
    form.append('file', f)
    return api.upload(path, form)
  }
  const pick = async (f: File) => {
    setFile(f)
    setPreview(null)
    setError(null)
    setBusy(true)
    try {
      setPreview(await send('/api/users/import/preview', f) as Preview)
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  const commit = async () => {
    if (!file) return
    setBusy(true)
    try {
      setResult(await send('/api/users/import/commit', file) as typeof result)
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  const savePasswords = () => {
    if (!result) return
    const lines = ['Табельный номер;ФИО;Подразделение;Логин;Временный пароль', ...result.created.map((c) => [c.personnel_no, c.full_name, c.department, c.login, c.password].join(';'))]
    presentBlob(new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' }), 'Новые сотрудники — логины.csv', true)
  }

  if (result) {
    return (
      <Modal title="Импорт завершён" size="wide" onClose={onClose} actions={<>
        {result.created.length > 0 && <button className="btn btn-secondary" onClick={savePasswords}><Download aria-hidden /> Скачать логины и пароли</button>}
        <button className="btn btn-primary" onClick={onClose}>Готово</button>
      </>}>
        <div className="stack">
          <Notice tone="ok" title={`Создано сотрудников: ${result.created.length}`}>
            Пропущено уже существующих: {result.skipped_existing}. Строк с ошибками: {result.errors}.
          </Notice>
          {result.created.length > 0 && (
            <div className="table-wrap" style={{ boxShadow: 'none' }}><table className="grid">
              <thead><tr><th>Таб. №</th><th>ФИО</th><th>Логин</th><th>Временный пароль</th></tr></thead>
              <tbody>{result.created.map((c) => <tr key={c.personnel_no}><td>{c.personnel_no}</td><td>{c.full_name}</td><td>{c.login}</td><td><code>{c.password}</code></td></tr>)}</tbody>
            </table></div>
          )}
          {result.created.some((c) => c.note) && (
            <Notice tone="warn" title="Обратите внимание">
              <ul style={{ margin: '0.3rem 0 0', paddingLeft: '1.2rem' }}>
                {result.created.filter((c) => c.note).map((c) => <li key={c.personnel_no}>{c.full_name}: {c.note}</li>)}
              </ul>
            </Notice>
          )}
          <p className="muted small">Пароли показаны один раз. Сохраните файл и передайте сотрудникам.</p>
        </div>
      </Modal>
    )
  }

  return (
    <Modal title="Импорт сотрудников из CSV" size="xwide" onClose={onClose} actions={<>
      <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
      <button className="btn btn-primary" disabled={busy || !preview || preview.summary.new === 0} onClick={() => void commit()}>
        {busy && <Spinner />} Создать {preview ? preview.summary.new : ''}
      </button>
    </>}>
      <div className="stack">
        <div className="dropzone stack-sm">
          <p>Столбцы: <b>Табельный номер</b>, <b>ФИО</b>, Подразделение, Должность, Роль. Разделитель «;» или «,», кодировка UTF-8 или Windows-1251.</p>
          <div className="row" style={{ justifyContent: 'center' }}>
            <input ref={input} type="file" accept=".csv,text/csv" className="sr-only" tabIndex={-1} aria-hidden onChange={(e) => {
              const f = e.target.files?.[0]
              e.target.value = ''
              if (f) void pick(f)
            }} />
            <button className="btn btn-primary" onClick={() => input.current?.click()}><Upload aria-hidden /> Выбрать файл</button>
            <button className="btn btn-ghost" onClick={() => void downloadCsv('/api/users/import/sample.csv', 'Образец импорта сотрудников.csv')}><Download aria-hidden /> Скачать образец</button>
          </div>
          {file && <p className="muted small">Файл: {file.name}</p>}
        </div>
        {busy && !preview && <Spinner label="Проверяем строки…" />}
        {error && <Notice tone="danger" title="Файл не принят">{error}</Notice>}
        {preview && (
          <>
            <div className="row">
              <Pill tone="ok" icon={<CircleCheck aria-hidden />}>Новых: {preview.summary.new}</Pill>
              <Pill>Уже есть: {preview.summary.exists}</Pill>
              <Pill tone={preview.summary.error ? 'danger' : 'none'} icon={<CircleAlert aria-hidden />}>С ошибками: {preview.summary.error}</Pill>
              <span className="muted small">Кодировка {preview.encoding}, разделитель «{preview.delimiter === '\t' ? 'табуляция' : preview.delimiter}»</span>
            </div>
            <div className="table-wrap" style={{ boxShadow: 'none', maxHeight: '22rem', overflowY: 'auto' }}><table className="grid">
              <thead><tr><th>Строка</th><th>Таб. №</th><th>ФИО</th><th>Подразделение</th><th>Должность</th><th>Логин</th><th>Что произойдёт</th></tr></thead>
              <tbody>{preview.rows.map((r) => (
                <tr key={r.line}>
                  <td>{r.line}</td><td>{r.personnel_no || '—'}</td><td className="cell-main">{r.full_name || '—'}</td><td>{r.department}</td><td>{r.position}</td>
                  <td>{r.status === 'new' ? r.login : ''}</td>
                  <td>
                    <Pill tone={STATUS[r.status].tone}>{STATUS[r.status].label}</Pill>
                    {[...r.problems, ...r.notes].map((t, i) => <div key={i} className="cell-sub" style={r.problems.includes(t) ? { color: 'var(--danger)' } : undefined}>{t}</div>)}
                  </td>
                </tr>
              ))}</tbody>
            </table></div>
            <p className="muted small">Строки с ошибками и уже существующие сотрудники не изменяются.</p>
          </>
        )}
      </div>
    </Modal>
  )
}
