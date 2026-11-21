import { Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { useEffect } from 'react'
import { useSession } from './local/session'
import { LoginPage } from './pages/Login'
import { EmployeeShell } from './pages/employee/Shell'
import { CoursePage, HomePage } from './pages/employee/Tasks'
import { LessonPage, TestPage } from './pages/employee/Learn'
import { LibraryItemPage, LibraryPage } from './pages/employee/Library'
import { AssessmentsPage, HistoryPage, ProfilePage } from './pages/employee/Other'
import { AdminShell } from './pages/methodist/Shell'
import { OverviewPage } from './pages/methodist/Overview'
import { ReportsPage, TimePage } from './pages/methodist/Reports'
import { AttemptPage, ProtocolPage, ReviewPage } from './pages/methodist/Review'
import { CoursesPage } from './pages/methodist/Courses'
import { EditorPage } from './pages/methodist/Editor'
import { PreviewPage } from './pages/methodist/Preview'
import { UsersPage } from './pages/methodist/Users'
import { LibraryAdminPage } from './pages/methodist/LibraryAdmin'
import { Assessments360Page } from './pages/methodist/Assessments'
import { SettingsPage } from './pages/methodist/Settings'
import { TeamPage } from './pages/employee/Team'
import { Loading, Toasts } from './ui/kit'

function Relogin() {
  const { account, needsLogin } = useSession()
  const navigate = useNavigate()
  useEffect(() => {
    if (!needsLogin) navigate('/', { replace: true })
  }, [needsLogin, navigate])
  // без сети пароль проверяется по сохранённой копии, но сессию это не восстанавливает —
  // возвращаем человека к работе, а не оставляем на экране входа
  const back = () => navigate('/', { replace: true })
  return <LoginPage relogin={account?.login} onDone={back} onBack={back} />
}

export function App() {
  const { status, account, needsLogin } = useSession()
  if (status === 'loading') {
    return <div className="login"><Loading label="Открываем приложение…" /></div>
  }
  if (status === 'anon' || !account) {
    return (
      <>
        <LoginPage />
        <Toasts />
      </>
    )
  }
  const methodist = account.user.role === 'methodist'
  // кабинет методиста работает только с сервером: если сессия завершена, сразу просим войти заново
  if (methodist && needsLogin) {
    return (
      <>
        <LoginPage relogin={account.login} />
        <Toasts />
      </>
    )
  }
  return (
    <>
      <Routes>
        <Route path="/relogin" element={<Relogin />} />
        {methodist ? (
          <Route element={<AdminShell />}>
            <Route path="/" element={<Navigate to="/m/overview" replace />} />
            <Route path="/m/overview" element={<OverviewPage />} />
            <Route path="/m/reports" element={<ReportsPage />} />
            <Route path="/m/time" element={<TimePage />} />
            <Route path="/m/review" element={<ReviewPage />} />
            <Route path="/m/attempts/:id" element={<AttemptPage />} />
            <Route path="/m/protocol" element={<ProtocolPage />} />
            <Route path="/m/courses" element={<CoursesPage />} />
            <Route path="/m/courses/:versionId" element={<EditorPage />} />
            <Route path="/m/preview/:versionId" element={<PreviewPage />} />
            <Route path="/m/users" element={<UsersPage />} />
            <Route path="/m/library" element={<LibraryAdminPage />} />
            <Route path="/m/assessments" element={<Assessments360Page />} />
            <Route path="/m/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/m/overview" replace />} />
          </Route>
        ) : (
          <Route element={<EmployeeShell />}>
            <Route path="/" element={<HomePage />} />
            <Route path="/a/:id" element={<CoursePage />} />
            <Route path="/a/:id/b/:blockId" element={<LessonPage />} />
            <Route path="/a/:id/test" element={<TestPage />} />
            <Route path="/library" element={<LibraryPage />} />
            <Route path="/library/:itemId" element={<LibraryItemPage />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/assessments" element={<AssessmentsPage />} />
            <Route path="/profile" element={<ProfilePage />} />
            <Route path="/team" element={<TeamPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        )}
      </Routes>
      <Toasts />
    </>
  )
}
