// Отчёт по обучению (обновляется сам) и отчёт по времени. Те же экраны видит руководитель —
// сервер отдаёт ему только его подразделение.
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Award, CircleAlert, CircleCheck, Download, Hourglass, Monitor, Smartphone } from 'lucide-react'
import { api, openBlob, query } from '../../api/client'
import { useNet } from '../../local/net'
import { Empty, Notice, Pill, errorText, fmtDateTime, fmtDuration, fmtNum, fmtTime, toast, useAsync } from '../../ui/kit'

export interface ReportRow {
  attempt_id: string; attempt_no: number; user_id: string; full_name: string; personnel_no: string; department: string
  course_id: string; course_title: string; sop_code: string; version_id: string; revision: number
  login_at: string | null; logout_at: string | null; logout_estimated: boolean
  platform_seconds: number; material_seconds: number; submitted_at: string; received_at: string
  delay_seconds: number | null; sync_trigger: string; sync_trigger_label: string; sync_app_state_label: string
  score: number; max_score: number; percent: number | null; pass_percent: number
  status: 'review' | 'passed' | 'failed'; status_label: string; source: string; source_label: string
  open_answers: { question: string; answer: string; points: number | null; max_points: number }[]
  certificate_id: string | null; certificate_number: string | null; certificate_file_id: string | null
}

export interface Filters {
  department_id: string; user_id: string; course_id: string; version_id: string; date_from: string; date_to: string; result: string
}
export const EMPTY_FILTERS: Filters = { department_id: '', user_id: '', course_id: '', version_id: '', date_from: '', date_to: '', result: '' }

interface Options {
  departments: { id: string; name: string }[]
  users: { id: string; full_name: string; department_id: string | null }[]
  courses: { id: string; title: string; versions: { id: string; revision: number; status: string }[] }[]
}

export function useOptions(staffOnly = false): Options {
  const [o, setO] = useState<Options>({ departments: [], users: [], courses: [] })
  useEffect(() => {
    let alive = true
    ;(async () => {
      const departments = await api.get<Options['departments']>('/api/departments').catch(() => [])
      // руководителю список курсов и сотрудников недоступен — фильтры строятся по строкам отчёта
      const users = staffOnly ? [] : await api.get<Options['users']>('/api/users').catch(() => [])
      const courses = staffOnly ? [] : await api.get<Options['courses']>('/api/courses').catch(() => [])
      if (alive) setO({ departments, users, courses })
    })()
    return () => {
      alive = false
    }
  }, [staffOnly])
  return o
}

export function FilterBar({ value, onChange, options, show }: {
  value: Filters; onChange: (f: Filters) => void; options: Options
  show: (keyof Filters)[]
}) {
  const set = (patch: Partial<Filters>) => onChange({ ...value, ...patch })
  const course = options.courses.find((c) => c.id === value.course_id)
  const users = options.users.filter((u) => !value.department_id || u.department_id === value.department_id)
  return (
    <div className="card card-flat filters no-print">
      {show.includes('department_id') && (
        <div className="field">
          <label htmlFor="f-dept">Подразделение</label>
          <select id="f-dept" className="select" value={value.department_id} onChange={(e) => set({ department_id: e.target.value, user_id: '' })}>
            <option value="">Все</option>
            {options.departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        </div>
      )}
      {show.includes('user_id') && options.users.length > 0 && (
        <div className="field">
          <label htmlFor="f-user">Сотрудник</label>
          <select id="f-user" className="select" value={value.user_id} onChange={(e) => set({ user_id: e.target.value })}>
            <option value="">Все</option>
            {users.map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
          </select>
        </div>
      )}
      {show.includes('course_id') && options.courses.length > 0 && (
        <div className="field">
          <label htmlFor="f-course">Курс</label>
          <select id="f-course" className="select" value={value.course_id} onChange={(e) => set({ course_id: e.target.value, version_id: '' })}>
            <option value="">Все</option>
            {options.courses.map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
          </select>
        </div>
      )}
      {show.includes('version_id') && course && (
        <div className="field">
          <label htmlFor="f-ver">Редакция</label>
          <select id="f-ver" className="select" value={value.version_id} onChange={(e) => set({ version_id: e.target.value })}>
            <option value="">Все</option>
            {course.versions.filter((v) => v.status !== 'draft').map((v) => <option key={v.id} value={v.id}>Редакция {v.revision}</option>)}
          </select>
        </div>
      )}
      {show.includes('date_from') && (
        <div className="field">
          <label htmlFor="f-from">Период с</label>
          <input id="f-from" type="date" className="input" value={value.date_from} onChange={(e) => set({ date_from: e.target.value })} />
        </div>
      )}
      {show.includes('date_to') && (
        <div className="field">
          <label htmlFor="f-to">по</label>
          <input id="f-to" type="date" className="input" value={value.date_to} onChange={(e) => set({ date_to: e.target.value })} />
        </div>
      )}
      {show.includes('result') && (
        <div className="field">
          <label htmlFor="f-res">Результат</label>
          <select id="f-res" className="select" value={value.result} onChange={(e) => set({ result: e.target.value })}>
            <option value="">Любой</option>
            <option value="passed">Сдал</option>
            <option value="failed">Не сдал</option>
            <option value="review">На проверке</option>
          </select>
        </div>
      )}
      <div className="field">
        <label>&nbsp;</label>
        <button className="btn btn-ghost btn-sm" onClick={() => onChange(EMPTY_FILTERS)}>Сбросить</button>
      </div>
    </div>
  )
}

export function ResultPill({ status, label }: { status: string; label: string }) {
  if (status === 'passed') return <Pill tone="ok" icon={<CircleCheck aria-hidden />}>{label}</Pill>
  if (status === 'failed') return <Pill tone="danger" icon={<CircleAlert aria-hidden />}>{label}</Pill>
  return <Pill tone="warn" icon={<Hourglass aria-hidden />}>{label}</Pill>
}

export function SourcePill({ source, label }: { source: string; label: string }) {
  return <span className="row-tight nowrap">{source === 'phone' ? <Smartphone aria-hidden width={18} /> : <Monitor aria-hidden width={18} />}{label}</span>
}

export async function downloadCsv(path: string, name: string): Promise<void> {
  try {
    await openBlob(path, name, true)
  } catch (e) {
    toast(errorText(e), 'error')
  }
}

export function ReportsPage({ staff = false }: { staff?: boolean }) {
  const net = useNet()
  const options = useOptions(staff)
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)
  const [rows, setRows] = useState<ReportRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<string | null>(null)
  const [fresh, setFresh] = useState<Set<string>>(new Set())
  const seen = useRef<Set<string> | null>(null)
  const status = useRef<Map<string, string>>(new Map())
  const q = query({ ...filters })

  useEffect(() => {
    let alive = true
    seen.current = null
    const load = async () => {
      if (document.visibilityState !== 'visible') return
      try {
        const data = await api.get<{ rows: ReportRow[]; server_time: string }>(`/api/reports/attempts${q}`)
        if (!alive) return
        // новая строка подсвечивается: запись пришла без каких-либо действий на этом экране
        if (seen.current) {
          const added = data.rows.filter((r) => !seen.current!.has(r.attempt_id))
          if (added.length) {
            setFresh((f) => new Set([...f, ...added.map((r) => r.attempt_id)]))
            added.forEach((r) => toast(`Новая попытка: ${r.full_name} — ${r.status_label}`, 'ok', 8000))
          }
          data.rows.forEach((r) => {
            const prev = status.current.get(r.attempt_id)
            if (prev && prev !== r.status) setFresh((f) => new Set([...f, r.attempt_id]))
          })
        }
        seen.current = new Set(data.rows.map((r) => r.attempt_id))
        status.current = new Map(data.rows.map((r) => [r.attempt_id, r.status]))
        setRows(data.rows)
        setUpdatedAt(data.server_time)
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
  }, [q])

  const kpi = useMemo(() => ({
    total: rows?.length ?? 0,
    passed: rows?.filter((r) => r.status === 'passed').length ?? 0,
    failed: rows?.filter((r) => r.status === 'failed').length ?? 0,
    review: rows?.filter((r) => r.status === 'review').length ?? 0,
  }), [rows])

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>{staff ? 'Обучение моего подразделения' : 'Отчёт по обучению'}</h1>
          <p>Строка появляется сама, как только устройство сотрудника доставит ответы. Обновлять страницу не нужно.</p>
        </div>
        <div className="row no-print">
          {net.reachable !== false && <span className="live" aria-live="polite"><i />обновлено в {fmtTime(updatedAt)}</span>}
          <button className="btn btn-secondary" onClick={() => void downloadCsv(`/api/reports/attempts.csv${q}`, 'Отчёт по обучению.csv')}>
            <Download aria-hidden /> Экспорт в Excel (CSV)
          </button>
        </div>
      </div>
      <FilterBar value={filters} onChange={setFilters} options={options}
        show={['department_id', 'user_id', 'course_id', 'version_id', 'date_from', 'date_to', 'result']} />
      <div className="kpis">
        <div className="kpi"><b className="num">{kpi.total}</b><span>попыток в выборке</span></div>
        <div className="kpi"><b className="num">{kpi.passed}</b><span>сдали</span></div>
        <div className="kpi"><b className="num">{kpi.failed}</b><span>не сдали</span></div>
        <div className="kpi"><b className="num">{kpi.review}</b><span>на проверке</span></div>
      </div>
      {error && <Notice tone="danger" title="Отчёт не обновился">{error}</Notice>}
      {rows && rows.length === 0 && (
        <Empty title="Попыток пока нет">Когда сотрудник сдаст тест и его устройство выйдет на связь, строка появится здесь автоматически.</Empty>
      )}
      {rows && rows.length > 0 && (
        <div className="table-wrap">
          <table className="grid report">
            <thead>
              <tr>
                <th>Сотрудник</th><th>Курс и редакция</th><th>Вход / выход</th><th>Время</th>
                <th>Попытка и доставка</th><th>Итог и баллы</th><th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.attempt_id} className={fresh.has(r.attempt_id) ? 'fresh' : ''}>
                  <td>
                    <div className="cell-main">{r.full_name}</div>
                    <div className="cell-sub">{r.department} · таб. {r.personnel_no}</div>
                  </td>
                  <td>
                    <div>{r.course_title}</div>
                    <div className="cell-sub">{r.sop_code ? `${r.sop_code} · ` : ''}<b>редакция {r.revision}</b> · попытка {r.attempt_no}</div>
                  </td>
                  <td className="nowrap">
                    <div>{fmtDateTime(r.login_at)}</div>
                    <div className="cell-sub">{r.logout_at ? `${fmtDateTime(r.logout_at)}${r.logout_estimated ? ' (оценка)' : ''}` : '—'}</div>
                  </td>
                  <td className="nowrap">
                    <div title="Активное время на платформе">платформа: <b>{fmtDuration(r.platform_seconds)}</b></div>
                    <div className="cell-sub" title="Время изучения материалов этого курса">материал: <b>{fmtDuration(r.material_seconds)}</b></div>
                  </td>
                  <td>
                    <div className="nowrap">сдано: {fmtDateTime(r.submitted_at)}</div>
                    <div className="cell-sub nowrap">получено: {fmtDateTime(r.received_at)}</div>
                    <div className="cell-sub"><SourcePill source={r.source} label={r.source_label} /></div>
                    {r.delay_seconds !== null
                      ? <div className="cell-sub">отправлено через <b>{r.delay_seconds < 1 ? 'меньше секунды' : fmtDuration(r.delay_seconds)}</b> после обнаружения связи ({r.sync_trigger_label}{r.sync_app_state_label ? `, ${r.sync_app_state_label}` : ''})</div>
                      : r.sync_trigger && <div className="cell-sub">{r.sync_trigger_label}{r.sync_app_state_label ? `, ${r.sync_app_state_label}` : ''}</div>}
                  </td>
                  <td>
                    <ResultPill status={r.status} label={r.status_label} />
                    <div style={{ marginTop: '0.3rem' }}><b>{fmtNum(r.score)} из {fmtNum(r.max_score)}</b>
                      <span className="cell-sub"> · {r.percent !== null ? `${fmtNum(r.percent)} % при пороге ${r.pass_percent} %` : 'итог после проверки'}</span></div>
                    {r.open_answers.map((a, i) => (
                      <div key={i} className="quote" title={a.question}>«{a.answer || '— нет ответа —'}»{a.points !== null ? ` · ${fmtNum(a.points)} из ${fmtNum(a.max_points)}` : ''}</div>
                    ))}
                  </td>
                  <td className="actions">
                    {!staff && <Link className={`btn btn-sm ${r.status === 'review' ? 'btn-primary' : 'btn-secondary'}`} to={`/m/attempts/${r.attempt_id}`}>
                      {r.status === 'review' ? 'Проверить' : 'Открыть'}
                    </Link>}
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
    </div>
  )
}

interface SessionsData {
  sessions: {
    id: string; full_name: string; department: string; source: string; source_label: string; login_at: string
    logout_at: string | null; exit_at: string | null; last_activity_at: string | null; end_kind: 'logout' | 'open' | 'estimated'
    end_label: string; active_seconds: number; material_seconds: number; estimated_intervals: number
  }[]
  materials: { user_id: string; full_name: string; department: string; course_title: string; revision: number; block_title: string; seconds: number; sources: string[] }[]
  totals: {
    user_id: string; full_name: string; department: string; sessions: number; open_sessions: number
    first_login_at: string | null; last_exit_at: string | null; platform_seconds: number; material_seconds: number
  }[]
  trainer: TrainerRow[]
  sessions_total: number
}

interface TrainerRow {
  id: string; full_name: string; department: string; course_title: string; revision: number | null
  ending_title: string; outcome: string; steps: number; finished_at: string; source_label: string
}

const TIME_TABLES = { totals: 'итого по сотрудникам', sessions: 'входы и выходы', materials: 'по материалам', trainer: 'тренажёр' } as const

export function TimePage({ staff = false }: { staff?: boolean }) {
  const options = useOptions(staff)
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)
  const [tab, setTab] = useState<'totals' | 'sessions' | 'materials' | 'trainer'>('totals')
  const q = query({ department_id: filters.department_id, user_id: filters.user_id, date_from: filters.date_from, date_to: filters.date_to })
  // экран и выгрузка — один и тот же расчёт на сервере с теми же фильтрами и правами
  const { data, error, reload } = useAsync(() => api.get<SessionsData>(`/api/reports/sessions${q}`), [q])
  useEffect(() => {
    const t = setInterval(() => document.visibilityState === 'visible' && reload(), 10000)
    return () => clearInterval(t)
  }, [reload])
  const fileTail = filters.date_from || filters.date_to ? ` ${filters.date_from || 'начало'}_${filters.date_to || 'сегодня'}` : ''
  const trainerRows = data?.trainer ?? []
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Время и входы</h1>
          <p>Учитывается только активное время: экран виден и сотрудник что-то делает. Пересекающиеся интервалы
            (две вкладки, ПК и телефон) объединяются. Время изучения материала входит в общее время на платформе.
            В отчёт попадает каждый, кто входил за период, — в том числе тот, кто только читал материалы.</p>
        </div>
        <div className="row no-print">
          <button className="btn btn-primary" onClick={() => void downloadCsv(`/api/reports/sessions.xlsx${q}`, `Время и входы${fileTail}.xlsx`)}>
            <Download aria-hidden /> Скачать Excel (все таблицы)
          </button>
          <button className="btn btn-secondary" onClick={() => void downloadCsv(`/api/reports/sessions.csv${query({ table: tab })}${q ? '&' + q.slice(1) : ''}`, `Время и входы — ${TIME_TABLES[tab]}${fileTail}.csv`)}>
            <Download aria-hidden /> CSV этой таблицы
          </button>
        </div>
      </div>
      <FilterBar value={filters} onChange={setFilters} options={options} show={['department_id', 'user_id', 'date_from', 'date_to']} />
      {error && <Notice tone="danger" title="Не удалось загрузить">{error}</Notice>}
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === 'totals'} onClick={() => setTab('totals')}>Итого по сотрудникам</button>
        <button role="tab" aria-selected={tab === 'sessions'} onClick={() => setTab('sessions')}>Входы и выходы</button>
        <button role="tab" aria-selected={tab === 'materials'} onClick={() => setTab('materials')}>По материалам</button>
        <button role="tab" aria-selected={tab === 'trainer'} onClick={() => setTab('trainer')}>Тренажёр</button>
      </div>
      {data && tab === 'trainer' && (trainerRows.length === 0 ? <Empty title="За этот период тренажёр никто не проходил" /> : (
        <div className="table-wrap"><table className="grid">
          <thead><tr><th>Сотрудник</th><th>Курс</th><th>Развязка</th><th>Исход</th><th>Шагов</th><th>Пройден</th><th>Источник</th></tr></thead>
          <tbody>{trainerRows.map((t) => (
            <tr key={t.id}>
              <td><div className="cell-main">{t.full_name}</div><div className="cell-sub">{t.department}</div></td>
              <td>{t.course_title}<div className="cell-sub">редакция {t.revision}</div></td>
              <td>{t.ending_title}</td>
              <td>{t.outcome === 'good' ? <Pill tone="ok">Хороший</Pill> : t.outcome === 'bad' ? <Pill tone="danger">Плохой</Pill> : <Pill tone="warn">С потерями</Pill>}</td>
              <td className="num">{t.steps}</td><td className="nowrap">{fmtDateTime(t.finished_at)}</td><td>{t.source_label}</td>
            </tr>
          ))}</tbody>
        </table></div>
      ))}
      {data && tab === 'totals' && (data.totals.length === 0 ? <Empty title="Нет данных о времени" /> : (
        <div className="table-wrap"><table className="grid">
          <thead><tr><th>Сотрудник</th><th>Подразделение</th><th>Входов</th><th>Первый вход</th><th>Последний выход</th><th>Активное время на платформе</th><th>Из него — изучение материалов</th></tr></thead>
          <tbody>{data.totals.map((t) => (
            <tr key={t.user_id}><td className="cell-main">{t.full_name}</td><td>{t.department}</td>
              <td className="num">{t.sessions}{t.open_sessions ? <div className="cell-sub">идёт: {t.open_sessions}</div> : null}</td>
              <td className="nowrap">{fmtDateTime(t.first_login_at)}</td><td className="nowrap">{fmtDateTime(t.last_exit_at)}</td>
              <td className="num">{fmtDuration(t.platform_seconds)}</td><td className="num">{fmtDuration(t.material_seconds)}</td></tr>
          ))}</tbody>
        </table></div>
      ))}
      {data && tab === 'sessions' && data.sessions_total > data.sessions.length && (
        <Notice tone="info">На экране — последние {data.sessions.length} входов из {data.sessions_total}. Полный список — в выгрузке Excel или CSV; итоги считаются по всем.</Notice>
      )}
      {data && tab === 'sessions' && (data.sessions.length === 0 ? <Empty title="Входов не было" /> : (
        <div className="table-wrap"><table className="grid">
          <thead><tr><th>Сотрудник</th><th>Вход</th><th>Выход</th><th>Последняя активность</th><th>Активное время</th><th>Изучение</th><th>Источник</th></tr></thead>
          <tbody>{data.sessions.map((s) => (
            <tr key={s.id}>
              <td><div className="cell-main">{s.full_name}</div><div className="cell-sub">{s.department}</div></td>
              <td className="nowrap">{fmtDateTime(s.login_at)}</td>
              <td className="nowrap">{s.end_kind === 'logout'
                ? <>{fmtDateTime(s.logout_at)} <Pill tone="ok">явный выход</Pill></>
                : s.end_kind === 'open'
                  ? <Pill tone="info">сеанс идёт</Pill>
                  : <>{fmtDateTime(s.exit_at ?? s.last_activity_at)} <Pill tone="warn">оценка по последней записи</Pill></>}</td>
              <td className="nowrap">{fmtDateTime(s.last_activity_at)}</td>
              <td className="num">{fmtDuration(s.active_seconds)}</td>
              <td className="num">{fmtDuration(s.material_seconds)}</td>
              <td><SourcePill source={s.source} label={s.source_label} /></td>
            </tr>
          ))}</tbody>
        </table></div>
      ))}
      {data && tab === 'materials' && (data.materials.length === 0 ? <Empty title="Материалы ещё не изучались" /> : (
        <div className="table-wrap"><table className="grid">
          <thead><tr><th>Сотрудник</th><th>Курс</th><th>Материал</th><th>Время изучения</th><th>Источник</th></tr></thead>
          <tbody>{data.materials.map((m, i) => (
            <tr key={i}>
              <td><div className="cell-main">{m.full_name}</div><div className="cell-sub">{m.department}</div></td>
              <td>{m.course_title}<div className="cell-sub">{m.revision ? `редакция ${m.revision}` : 'справочный материал'}</div></td>
              <td>{m.block_title}</td><td className="num">{fmtDuration(m.seconds)}</td><td>{m.sources.join(', ')}</td>
            </tr>
          ))}</tbody>
        </table></div>
      ))}
    </div>
  )
}
