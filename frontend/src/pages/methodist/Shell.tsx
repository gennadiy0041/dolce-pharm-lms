// Каркас кабинета методиста: боковое меню на ноутбуке, горизонтальное — на узком экране.
import { useEffect, useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import {
  BookOpen, ChartColumn, ClipboardCheck, GraduationCap, LayoutDashboard, LogOut, ScrollText, Settings, Target, Timer, Users, WifiOff,
} from 'lucide-react'
import { api } from '../../api/client'
import { useNet } from '../../local/net'
import { logout, useSession } from '../../local/session'
import { Notice } from '../../ui/kit'

export function useReviewCount(): number {
  const [count, setCount] = useState(0)
  useEffect(() => {
    let alive = true
    const load = () => {
      if (document.visibilityState !== 'visible') return
      api.get<unknown[]>('/api/review/queue').then((rows) => alive && setCount(rows.length)).catch(() => undefined)
    }
    load()
    const t = setInterval(load, 5000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [])
  return count
}

export function AdminShell() {
  const { account } = useSession()
  const net = useNet()
  const navigate = useNavigate()
  const review = useReviewCount()
  const items = [
    { to: '/m/overview', label: 'Обучение смены', icon: LayoutDashboard },
    { to: '/m/reports', label: 'Отчёт по обучению', icon: ChartColumn },
    { to: '/m/review', label: 'Проверка ответов', icon: ClipboardCheck, badge: review },
    { to: '/m/protocol', label: 'Протокол аттестации', icon: ScrollText },
    { to: '/m/time', label: 'Время и входы', icon: Timer },
    { to: '/m/courses', label: 'Курсы и назначения', icon: GraduationCap },
    { to: '/m/library', label: 'Библиотека', icon: BookOpen },
    { to: '/m/users', label: 'Сотрудники', icon: Users },
    { to: '/m/assessments', label: 'Оценка 180/360', icon: Target },
    { to: '/m/settings', label: 'Настройки и телефон', icon: Settings },
  ]
  return (
    <div className="admin">
      <aside className="side">
        <div className="side-head">
          <NavLink to="/m/overview" className="brand" aria-label="Dolce-Pharm, обучение и аттестация — на главную">
            <img src="/brand/dolce-pharm.png" alt="" width={398} height={134} />
            <span>Обучение и аттестация</span>
          </NavLink>
        </div>
        <nav aria-label="Разделы">
          {items.map((i) => (
            <NavLink key={i.to} to={i.to} className={({ isActive }) => `navlink ${isActive ? 'active' : ''}`}>
              <i.icon aria-hidden />
              <span>{i.label}</span>
              {!!i.badge && <span className="badge" aria-label={`Ждут проверки: ${i.badge}`}>{i.badge}</span>}
            </NavLink>
          ))}
        </nav>
        <div className="side-foot">
          <p className="side-user">{account?.user.full_name}<br />{account?.user.role_label}</p>
          <button className="btn btn-ghost btn-sm" style={{ marginTop: '0.4rem', paddingLeft: 0 }} onClick={async () => {
            await logout()
            navigate('/', { replace: true })
          }}><LogOut aria-hidden /> Выйти</button>
        </div>
      </aside>
      <main className="content" id="main">
        {net.reachable === false && (
          <div style={{ marginBottom: '1rem' }}>
            <Notice tone="warn" title="Нет связи с сервером">
              <span className="row-tight"><WifiOff aria-hidden /> Данные на экране могут устареть. Подключение восстановится автоматически.</span>
            </Notice>
          </div>
        )}
        <Outlet />
      </main>
    </div>
  )
}
