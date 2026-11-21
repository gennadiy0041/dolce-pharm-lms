// Раздел руководителя: обучение своего подразделения. Сервер отдаёт только его сотрудников.
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { useNet } from '../../local/net'
import { Empty } from '../../ui/kit'
import { ReportsPage, TimePage, type ReportRow } from '../methodist/Reports'
import { ProtocolPage } from '../methodist/Review'

export function TeamPage() {
  const net = useNet()
  const [tab, setTab] = useState<'report' | 'time' | 'protocol'>('report')
  const [courses, setCourses] = useState<{ id: string; title: string }[]>([])
  useEffect(() => {
    if (tab !== 'protocol') return
    api.get<{ rows: ReportRow[] }>('/api/reports/attempts')
      .then((d) => setCourses([...new Map(d.rows.map((r) => [r.course_id, { id: r.course_id, title: r.course_title }])).values()]))
      .catch(() => undefined)
  }, [tab])
  if (net.reachable === false) return <Empty title="Отчёты доступны при связи с сервером" />
  return (
    <div>
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === 'report'} onClick={() => setTab('report')}>Результаты</button>
        <button role="tab" aria-selected={tab === 'time'} onClick={() => setTab('time')}>Время</button>
        <button role="tab" aria-selected={tab === 'protocol'} onClick={() => setTab('protocol')}>Протокол</button>
      </div>
      {tab === 'report' && <ReportsPage staff />}
      {tab === 'time' && <TimePage staff />}
      {tab === 'protocol' && <ProtocolPage staff courses={courses} />}
    </div>
  )
}
