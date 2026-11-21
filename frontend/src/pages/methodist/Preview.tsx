// «Глазами сотрудника»: те же компоненты урока и теста, что на телефоне, но данные идут с сервера.
// Работает и для черновика — до публикации.
import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { api } from '../../api/client'
import { QUESTION_HINT, QUESTION_LABEL, QuestionView } from '../../components/QuestionView'
import { BLOCK_LABEL, BlockView, type FileSource } from '../../components/Viewers'
import { formatBytes } from '../../local/download'
import type { AnswerValue, FileRef, Manifest } from '../../types'
import { Loading, Notice, Pill, plural, useAsync } from '../../ui/kit'

export function PreviewPage() {
  const { versionId } = useParams()
  const { data: m, error } = useAsync(() => api.get<Manifest>(`/api/versions/${versionId}/preview`), [versionId])
  const [step, setStep] = useState(0)
  const [answers, setAnswers] = useState<Record<string, AnswerValue>>({})
  const cache = useMemo(() => new Map<string, Promise<Blob>>(), [versionId]) // eslint-disable-line react-hooks/exhaustive-deps
  const source: FileSource = useMemo(() => (ref: FileRef) => {
    let p = cache.get(ref.id)
    if (!p) {
      p = api.blob(`/api/files/${ref.id}/content`)
      cache.set(ref.id, p)
    }
    return p
  }, [cache])

  if (error) return <Notice tone="danger" title="Предпросмотр не открылся">{error}</Notice>
  if (!m) return <Loading />
  const total = m.blocks.length + m.questions.length
  const block = step < m.blocks.length ? m.blocks[step] : undefined
  const question = step >= m.blocks.length ? m.questions[step - m.blocks.length] : undefined

  return (
    <div className="stack">
      <div>
        <Link to={`/m/courses/${m.version_id}`} className="btn btn-ghost btn-sm" style={{ marginLeft: '-0.6rem' }}>← К редактору</Link>
        <div className="page-head" style={{ marginTop: '0.3rem' }}>
          <div>
            <h1>Глазами сотрудника</h1>
            <p>{m.title} · редакция {m.revision}{m.status === 'draft' ? ' · черновик' : ''}. Объём загрузки на телефон — {formatBytes(m.total_size)}.</p>
          </div>
        </div>
      </div>
      {total === 0 && <Notice tone="warn" title="Курс пуст">Добавьте материалы в редакторе.</Notice>}
      {total > 0 && (
        <div className="editor">
          <div className="preview-frame">
            <div className="inner stack" key={step}>
              {block && (
                <>
                  <div className="spread"><span className="q-count">Шаг {step + 1} из {m.blocks.length}</span><Pill>{BLOCK_LABEL[block.type]}</Pill></div>
                  <h2>{block.title || 'Материал'}</h2>
                  <BlockView block={block} source={source} />
                </>
              )}
              {question && (
                <>
                  <div className="spread">
                    <span className="q-count">Вопрос {step - m.blocks.length + 1} из {m.questions.length}</span>
                    <Pill>{QUESTION_LABEL[question.type]} · {question.points} {plural(question.points, 'балл', 'балла', 'баллов')}</Pill>
                  </div>
                  <p className="q-text">{question.text || 'Текст вопроса не заполнен'}</p>
                  <p className="muted small">{QUESTION_HINT[question.type]}</p>
                  <QuestionView q={question} value={answers[question.id]} onChange={(v) => setAnswers((a) => ({ ...a, [question.id]: v }))} />
                </>
              )}
              <div className="row" style={{ marginTop: 'auto' }}>
                <button className="btn btn-secondary grow" disabled={step === 0} onClick={() => setStep(step - 1)}><ChevronLeft aria-hidden /> Назад</button>
                <button className="btn btn-primary grow" disabled={step >= total - 1} onClick={() => setStep(step + 1)}>Дальше <ChevronRight aria-hidden /></button>
              </div>
            </div>
          </div>
          <aside className="card stack-sm editor-side">
            <h2>Состав курса</h2>
            <ol style={{ margin: 0, paddingLeft: '1.2rem' }} className="stack-sm">
              {m.blocks.map((b, i) => (
                <li key={b.id}>
                  <button className="btn btn-ghost btn-sm" style={{ textAlign: 'left', justifyContent: 'flex-start', fontWeight: step === i ? 700 : 500 }} onClick={() => setStep(i)}>
                    {b.title || BLOCK_LABEL[b.type]} <span className="muted small">· {BLOCK_LABEL[b.type]}{b.required ? '' : ' · по желанию'}{b.needs_internet ? ' · нужен интернет' : ''}</span>
                  </button>
                </li>
              ))}
              {m.questions.map((q, i) => (
                <li key={q.id}>
                  <button className="btn btn-ghost btn-sm" style={{ textAlign: 'left', justifyContent: 'flex-start', fontWeight: step === m.blocks.length + i ? 700 : 500 }} onClick={() => setStep(m.blocks.length + i)}>
                    Вопрос {i + 1} <span className="muted small">· {QUESTION_LABEL[q.type]}</span>
                  </button>
                </li>
              ))}
            </ol>
            <p className="muted small">Правильные ответы в пакет для телефона не входят: проверку выполняет сервер.</p>
          </aside>
        </div>
      )}
    </div>
  )
}
