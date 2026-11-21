// Библиотека глазами методиста: добавить справочник, категорию и описание, убрать материал.
import { useRef, useState } from 'react'
import { Download, Plus, Trash2, Upload } from 'lucide-react'
import { api, openBlob } from '../../api/client'
import { formatBytes } from '../../local/download'
import type { LibraryItem } from '../../types'
import { Empty, Field, Loading, Modal, Notice, Pill, Spinner, errorText, fmtDate, toast, useAsync } from '../../ui/kit'

interface LibraryData { items: LibraryItem[]; categories: string[] }

export function LibraryAdminPage() {
  const [q, setQ] = useState('')
  const { data, error, reload } = useAsync(() => api.get<LibraryData>(`/api/library${q ? `?q=${encodeURIComponent(q)}` : ''}`), [q])
  const [adding, setAdding] = useState(false)
  const remove = async (item: LibraryItem) => {
    if (!window.confirm(`Убрать «${item.title}» из библиотеки?`)) return
    try {
      await api.del(`/api/library/${item.id}`)
      reload()
    } catch (e) {
      toast(errorText(e), 'error')
    }
  }
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Библиотека</h1>
          <p>Справочные материалы вне курсов. Сотрудник ищет их по названию, коду СОП и категории и может сохранить на телефон.</p>
        </div>
        <button className="btn btn-primary" onClick={() => setAdding(true)}><Plus aria-hidden /> Добавить материал</button>
      </div>
      <div className="card card-flat filters">
        <div className="field">
          <label htmlFor="la-q">Поиск по названию, коду СОП, категории</label>
          <input id="la-q" className="input" type="search" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>
      {error && <Notice tone="danger">{error}</Notice>}
      {!data && !error && <Loading />}
      {data?.items.length === 0 && <Empty title="Материалов нет" />}
      {data && data.items.length > 0 && (
        <div className="table-wrap"><table className="grid">
          <thead><tr><th>Название</th><th>Код СОП</th><th>Категория</th><th>Файл</th><th>Копия для чтения</th><th>Обновлён</th><th></th></tr></thead>
          <tbody>{data.items.map((i) => (
            <tr key={i.id}>
              <td><div className="cell-main">{i.title}</div><div className="cell-sub">{i.description}</div></td>
              <td className="nowrap">{i.sop_code || '—'}</td>
              <td>{i.category ? <Pill tone="brand">{i.category}</Pill> : '—'}</td>
              <td className="nowrap">{i.file.ext.toUpperCase()} · {formatBytes(i.file.size)}</td>
              <td>{i.view
                ? <Pill tone="ok">{i.view.id === i.file.id ? 'не требуется' : `PDF, ${i.view.pages ?? '—'} стр.`}</Pill>
                : i.file.mime.startsWith('video/') ? <Pill tone="ok">не требуется</Pill>
                  : <Pill tone="danger">ошибка: {i.conversion_error}</Pill>}</td>
              <td className="nowrap">{fmtDate(i.updated_at)}</td>
              <td className="actions">
                <button className="btn btn-ghost btn-sm" onClick={() => void openBlob(`/api/files/${(i.view ?? i.file).id}/content`).catch((e) => toast(errorText(e), 'error'))}><Download aria-hidden /> Открыть</button>
                <button className="btn btn-ghost btn-sm" onClick={() => void remove(i)} aria-label={`Убрать ${i.title}`}><Trash2 aria-hidden /></button>
              </td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {adding && <AddItem categories={data?.categories ?? []} onClose={() => { setAdding(false); reload() }} />}
    </div>
  )
}

function AddItem({ categories, onClose }: { categories: string[]; onClose: () => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [form, setForm] = useState({ title: '', sop_code: '', category: '', description: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const save = async () => {
    if (!file || !form.title.trim()) {
      setError('Укажите название и выберите файл')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const body = new FormData()
      body.append('file', file)
      Object.entries(form).forEach(([k, v]) => body.append(k, v))
      const item = await api.upload<LibraryItem>('/api/library', body)
      if (item.conversion_status === 'error') toast('Материал добавлен, но копию для чтения построить не удалось.', 'error', 9000)
      else toast('Материал добавлен в библиотеку', 'ok')
      onClose()
    } catch (e) {
      setError(errorText(e))
      setBusy(false)
    }
  }
  return (
    <Modal title="Новый материал библиотеки" onClose={onClose} actions={<>
      <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
      <button className="btn btn-primary" onClick={() => void save()} disabled={busy}>{busy && <Spinner />} {busy ? 'Загружаем…' : 'Добавить'}</button>
    </>}>
      <div className="stack">
        <div className="dropzone stack-sm">
          <input ref={input} type="file" className="sr-only" tabIndex={-1} aria-hidden accept=".pdf,.mp4,.m4v,.webm,.docx,.xlsx,.pptx" onChange={(e) => {
            const f = e.target.files?.[0]
            e.target.value = ''
            if (f) {
              setFile(f)
              if (!form.title) setForm((s) => ({ ...s, title: f.name.replace(/\.[^.]+$/, '') }))
            }
          }} />
          <button className="btn btn-secondary" onClick={() => input.current?.click()}><Upload aria-hidden /> {file ? 'Выбрать другой файл' : 'Выбрать файл'}</button>
          <p className="muted small">{file ? `${file.name} · ${formatBytes(file.size)}` : 'PDF, MP4, DOCX, XLSX или PPTX'}</p>
        </div>
        <Field label="Название" htmlFor="li-title"><input id="li-title" className="input" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
        <div className="form-grid">
          <Field label="Код СОП" htmlFor="li-code"><input id="li-code" className="input" value={form.sop_code} onChange={(e) => setForm({ ...form, sop_code: e.target.value })} /></Field>
          <Field label="Категория" htmlFor="li-cat">
            <input id="li-cat" className="input" list="li-cats" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} />
            <datalist id="li-cats">{categories.map((c) => <option key={c} value={c} />)}</datalist>
          </Field>
        </div>
        <Field label="Описание" htmlFor="li-desc"><textarea id="li-desc" className="textarea" style={{ minHeight: '3.6rem' }} rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
        {error && <p className="error-text" role="alert">{error}</p>}
      </div>
    </Modal>
  )
}
