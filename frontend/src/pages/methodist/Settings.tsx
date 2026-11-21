// Настройки учёта времени, подключение телефона (QR-коды) и сведения об установке.
import { useEffect, useState, type ChangeEvent } from 'react'
import { api } from '../../api/client'
import type { ClientSettings } from '../../types'
import { Field, Loading, Notice, Pill, Spinner, errorText, toast, useAsync } from '../../ui/kit'

interface SystemInfo {
  env: string; https_port: number; http_port: number
  lan: { ip: string; stable: boolean; app_url: string; setup_url: string; qr_app: string; qr_setup: string }[]
  stable_url: string
  data_dir: string; files_dir: string; backup_dir: string; log_dir: string; database: string; tz: string; converter: string
}

function Qr({ svg, label }: { svg: string; label: string }) {
  // QR-код приходит с сервера готовым SVG — показываем как картинку, без внешних сервисов
  const src = `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`
  return <img className="qr" src={src} alt={label} />
}

export function SettingsPage() {
  const info = useAsync(() => api.get<SystemInfo>('/api/system/info'), [])
  const settings = useAsync(() => api.get<ClientSettings>('/api/settings'), [])
  const [form, setForm] = useState<ClientSettings | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (settings.data) setForm(settings.data)
  }, [settings.data])
  const save = async () => {
    if (!form) return
    setBusy(true)
    try {
      setForm(await api.patch<ClientSettings>('/api/settings', form))
      toast('Настройки сохранены. Устройства получат их при следующем открытии приложения.', 'ok')
    } catch (e) {
      toast(errorText(e), 'error')
    } finally {
      setBusy(false)
    }
  }
  const num = (key: keyof ClientSettings) => ({
    className: 'input', type: 'number', value: form?.[key] ?? 0, style: { maxWidth: '9rem' },
    onChange: (e: ChangeEvent<HTMLInputElement>) => { if (form) setForm({ ...form, [key]: Number(e.target.value) || 0 }) },
  })

  return (
    <div className="stack" style={{ maxWidth: '60rem' }}>
      <div className="page-head">
        <div>
          <h1>Настройки и телефон</h1>
          <p>Как подключить смартфон сотрудника и как считается время.</p>
        </div>
      </div>

      <section className="card stack" aria-labelledby="st-phone">
        <h2 id="st-phone">Подключение телефона</h2>
        {info.error && <Notice tone="danger">{info.error}</Notice>}
        {!info.data && !info.error && <Loading />}
        {info.data && info.data.lan.length === 0 && (
          <Notice tone="warn" title="Ноутбук не подключён к сети">Подключитесь к Wi-Fi или включите «Мобильный хот-спот» Windows и обновите страницу.</Notice>
        )}
        {info.data && info.data.lan.length > 0 && !info.data.lan.some((l) => l.stable) && (
          <Notice tone="info" title="Адрес может измениться">
            Адреса ниже выдала сеть Wi-Fi: после перезагрузки ноутбука или в другом помещении они бывают другими, и ярлык на телефоне
            перестаёт открываться. Постоянный адрес — <b style={{ wordBreak: 'break-all' }}>{info.data.stable_url}</b> — появляется, когда на ноутбуке
            включён «Мобильный хот-спот» (Параметры Windows → Сеть и Интернет), а телефон подключён к его сети.
          </Notice>
        )}
        {info.data?.lan.map((l) => (
          <div key={l.ip} className="card card-flat card-tint stack-sm">
            <h3 className="row">Сеть {l.ip} {l.stable && <Pill tone="ok">Постоянный адрес</Pill>}</h3>
            {l.stable && <p className="small">Сеть «Мобильного хот-спота» этого ноутбука. Адрес не меняется после перезагрузки и не зависит от Wi-Fi в помещении — ставьте приложение на главный экран именно с него.</p>}
            <div className="row" style={{ alignItems: 'flex-start', gap: '1.5rem' }}>
              <div className="stack-sm center">
                <Qr svg={l.qr_setup} label={`QR-код: ${l.setup_url}`} />
                <b>1. Первый раз</b>
                <span className="small" style={{ wordBreak: 'break-all' }}>{l.setup_url}</span>
              </div>
              <div className="stack-sm center">
                <Qr svg={l.qr_app} label={`QR-код: ${l.app_url}`} />
                <b>2. Платформа</b>
                <span className="small" style={{ wordBreak: 'break-all' }}>{l.app_url}</span>
              </div>
              <ol className="grow" style={{ margin: 0, paddingLeft: '1.2rem', minWidth: '16rem' }}>
                <li>Телефон и ноутбук — в одной сети Wi-Fi.</li>
                <li>Наведите камеру на первый QR-код: откроется страница с сертификатом и пошаговой инструкцией.</li>
                <li>Установите сертификат (один раз), затем откройте платформу по второму QR-коду.</li>
                <li>В меню браузера выберите «Добавить на главный экран».</li>
                <li>В приложении: «Профиль» → «Проверка автономной работы».</li>
              </ol>
            </div>
          </div>
        ))}
        <p className="muted small">Сертификат нужен, чтобы телефон доверял адресу https:// в локальной сети — без этого браузер не разрешит работу без интернета. Сертификат создаётся на этом ноутбуке и никуда не передаётся.</p>
      </section>

      <section className="card stack" aria-labelledby="st-time">
        <h2 id="st-time">Учёт времени</h2>
        {!form ? <Loading /> : (
          <>
            <div className="form-grid">
              <Field label="Шаг записи, секунд" hint="Как часто интервал сохраняется на устройстве. Допуск измерения."><input {...num('heartbeat_seconds')} min={5} max={60} /></Field>
              <Field label="Пауза при бездействии, секунд" hint="Нет касаний и нажатий — учёт времени на платформе останавливается."><input {...num('idle_timeout_seconds')} min={30} max={1800} /></Field>
              <Field label="Чтение без действий, секунд" hint="Сколько можно читать материал, не касаясь экрана."><input {...num('reading_idle_seconds')} min={30} max={900} /></Field>
              <Field label="Очистка телефона, дней" hint="Через сколько дней после сдачи рекомендовать удалить данные с личного устройства."><input {...num('local_retention_days')} min={1} max={365} /></Field>
            </div>
            <div><button className="btn btn-primary" onClick={() => void save()} disabled={busy}>{busy && <Spinner />} Сохранить</button></div>
          </>
        )}
      </section>

      <section className="card stack-sm" aria-labelledby="st-sys">
        <h2 id="st-sys">Установка</h2>
        {info.data && (
          <div className="table-wrap" style={{ boxShadow: 'none' }}><table className="grid"><tbody>
            <tr><td>Контур</td><td>{info.data.env === 'demo' ? 'демонстрационный' : info.data.env}</td></tr>
            <tr><td>База данных</td><td>{info.data.database}</td></tr>
            <tr><td>Файлы материалов</td><td style={{ wordBreak: 'break-all' }}>{info.data.files_dir}</td></tr>
            <tr><td>Журналы</td><td style={{ wordBreak: 'break-all' }}>{info.data.log_dir}</td></tr>
            <tr><td>Резервные копии</td><td style={{ wordBreak: 'break-all' }}>{info.data.backup_dir} <span className="muted small">(команда: python run.py backup)</span></td></tr>
            <tr><td>Конвертер Office → PDF</td><td>{info.data.converter}</td></tr>
            <tr><td>Часовой пояс документов</td><td>{info.data.tz}</td></tr>
            <tr><td>Описание API</td><td><a href="/api/docs" target="_blank" rel="noopener">/api/docs</a></td></tr>
          </tbody></table></div>
        )}
        <p className="muted small">Все данные хранятся на этом сервере. Внешние сервисы, телеметрия и облачные хранилища не используются.</p>
      </section>
    </div>
  )
}
