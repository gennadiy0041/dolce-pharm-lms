import { useState, type FormEvent } from 'react'
import { useLiveQuery } from '../local/live'
import { LogIn, WifiOff } from 'lucide-react'
import { deviceDb } from '../local/db'
import { useNet } from '../local/net'
import { login } from '../local/session'
import { Notice, Spinner, toast } from '../ui/kit'

export function LoginPage({ relogin, onDone, onBack }: { relogin?: string; onDone?: () => void; onBack?: () => void }) {
  const net = useNet()
  const [name, setName] = useState(relogin ?? '')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const known = useLiveQuery(() => deviceDb.accounts.toArray(), [], [])
  const offline = net.reachable === false

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!name.trim() || !password) {
      setError('Введите логин и пароль')
      return
    }
    setBusy(true)
    setError(null)
    const res = await login(name, password)
    setBusy(false)
    if (!res.ok) {
      setError(res.message ?? 'Не удалось войти')
      return
    }
    if (res.offline && relogin) toast('Сервер недоступен. Результаты уйдут после входа, когда связь появится.', 'info', 7000)
    onDone?.()
  }

  return (
    <div className="login">
      <main className="login-card">
        <div className="login-logo">
          <img src="/brand/dolce-pharm.png" alt="Dolce-Pharm" width={398} height={134} />
          <div>
            <h1>Обучение и аттестация</h1>
            <p className="muted">Инструкции и тесты — на телефоне и без сети</p>
          </div>
        </div>
        <form className="card stack" onSubmit={submit} noValidate>
          <h2>Вход</h2>
          {relogin && !offline && <Notice tone="warn" title="Сессия завершена">Войдите заново, чтобы продолжить.</Notice>}
          {offline && (
            <Notice tone="warn" title="Нет связи с сервером">
              {known.length ? 'Можно войти под учётной записью, которая уже входила на этом устройстве.' : 'Первый вход на этом устройстве возможен только при доступном сервере.'}
            </Notice>
          )}
          <div className="field">
            <label htmlFor="login">Логин</label>
            <input id="login" className="input" value={name} onChange={(e) => setName(e.target.value)} autoComplete="username"
              autoCapitalize="none" autoCorrect="off" spellCheck={false} inputMode="text" required />
          </div>
          <div className="field">
            <label htmlFor="password">Пароль</label>
            <input id="password" className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password" required />
          </div>
          {error && <p className="error-text" role="alert">{error}</p>}
          <button className="btn btn-primary btn-lg btn-block" type="submit" disabled={busy}>
            {busy ? <Spinner /> : offline ? <WifiOff aria-hidden /> : <LogIn aria-hidden />} Войти
          </button>
          {onBack && <button type="button" className="btn btn-ghost btn-block" onClick={onBack}>Вернуться без входа</button>}
          <p className="muted small">Логин и пароль выдаёт отдел обучения. Демонстрационные доступы — в файле README.</p>
        </form>
      </main>
    </div>
  )
}
