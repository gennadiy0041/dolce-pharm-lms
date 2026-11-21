// Урок (один блок на экран) и прохождение теста. Всё работает из локального пакета — сеть не нужна.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useLiveQuery } from '../../local/live'
import { ChevronLeft, ChevronRight, CircleAlert, CircleCheck, HardDrive, Send, Smartphone } from 'lucide-react'
import { BLOCK_LABEL, BlockView, type FileSource } from '../../components/Viewers'
import { QUESTION_HINT, QUESTION_LABEL, QuestionView, isAnswered } from '../../components/QuestionView'
import type { TrainerOutcome } from '../../components/TrainerView'
import { uuid } from '../../local/db'
import { fileBlob } from '../../local/download'
import {
  markBlockDone, rememberPosition, saveAnswer, saveIndex, startOrResumeAttempt, submitAttempt, useAssignmentView,
  type AssignmentView, type Phase,
} from '../../local/learner'
import { useNet } from '../../local/net'
import { currentDb, deviceKind, useSession } from '../../local/session'
import { enqueue } from '../../local/sync'
import { beginMaterial, endMaterial } from '../../local/tracker'
import type { AnswerValue, FileRef, LocalAttempt } from '../../types'
import { Empty, LocalLoading, Modal, Notice, Pill, ProgressBar, fmtNum, plural, toast } from '../../ui/kit'
import { DEVICE_WORD } from './Shell'
import { MainAction } from './Tasks'

const localSource: FileSource = (ref: FileRef) => fileBlob(currentDb(), ref.sha256)

/** Состояния, в которых тест начинать или продолжать нельзя. */
const NO_TEST_PHASES: Phase[] = ['passed', 'review', 'saved', 'exhausted', 'replaced', 'rejected']

/** К какому курсу относится экран теста: название, код СОП и редакция — чтобы результат не висел без подписи. */
function TestHeader({ v, heading }: { v: AssignmentView; heading?: boolean }) {
  const style = { marginTop: '0.2rem', fontSize: '1.2rem', fontWeight: 700, lineHeight: 1.25 }
  return (
    <div>
      <p className="eyebrow">Тест по курсу</p>
      {/* Обычно это не заголовок: главным заголовком экрана остаётся статус («Сдал», «Ответы сохранены…»).
          На экране перед началом теста статуса нет — там название курса и есть заголовок. */}
      {heading ? <h1 style={style}>{v.a.version.title}</h1> : <p style={style}>{v.a.version.title}</p>}
      <p className="muted" style={{ marginTop: '0.25rem' }}>{v.a.version.sop_code ? `${v.a.version.sop_code} · ` : ''}редакция {v.a.version.revision}</p>
    </div>
  )
}

/** Назначения больше нет в списке: его отменили или редакцию заменили до того, как сотрудник начал работу. */
function GoneNotice() {
  return (
    <Empty title="Задание больше не активно" action={<Link className="btn btn-primary" to="/">К заданиям</Link>}>
      Отдел обучения отменил его или выпустил новую редакцию курса. Действующее задание — в списке заданий.
    </Empty>
  )
}

export function LessonPage() {
  const { id, blockId } = useParams()
  const { view: v, loaded } = useAssignmentView(id)
  const navigate = useNavigate()
  const blocks = v?.manifest?.blocks ?? []
  const index = blocks.findIndex((b) => b.id === blockId)
  const block = index >= 0 ? blocks[index] : undefined
  const versionId = v?.a.version_id

  useEffect(() => {
    if (!block || !versionId) return
    // видео — это и блок «Видео», и ссылка, у которой локальная копия — видеофайл
    const isVideo = block.type === 'video' || (block.type === 'link' && !!block.view?.mime.startsWith('video/'))
    beginMaterial({ versionId, blockId: block.id, mode: isVideo ? 'video' : 'reading' })
    void rememberPosition(currentDb(), versionId, block.id)
    return () => endMaterial()
  }, [block?.id, versionId]) // eslint-disable-line react-hooks/exhaustive-deps

  const onTrainerFinish = useCallback((r: TrainerOutcome) => {
    if (!versionId || !blockId) return
    const db = currentDb()
    const result = { id: uuid(), versionId, blockId, endingId: r.endingId, title: r.title, outcome: r.outcome, finishedAt: new Date().toISOString() }
    void db.trainer.add(result)
    void enqueue(db, 'trainer.result', {
      result_id: result.id, version_id: versionId, block_id: blockId, ending_id: r.endingId, ending_title: r.title,
      outcome: r.outcome, path: r.path, finished_at: result.finishedAt,
    }, result.finishedAt)
    void markBlockDone(db, versionId, blockId)
  }, [versionId, blockId])

  if (!loaded) return <LocalLoading />
  if (!v) return <GoneNotice />
  if (!v.manifest) {
    return <Empty title="Курс не скачан" action={<Link className="btn btn-primary" to={id ? `/a/${id}` : '/'}>К курсу</Link>}>Скачайте курс, чтобы открыть материалы.</Empty>
  }
  if (!block) return <Empty title="Материал не найден" action={<Link className="btn btn-primary" to={`/a/${v.a.id}`}>К курсу</Link>} />

  const prev = index > 0 ? blocks[index - 1] : undefined
  const next = index < blocks.length - 1 ? blocks[index + 1] : undefined
  const done = v.done.includes(block.id)
  // тест предлагается, только если его действительно можно сдавать
  const toTest = v.manifest.questions.length > 0 && !NO_TEST_PHASES.includes(v.phase)
  const forward = async () => {
    await markBlockDone(currentDb(), v.a.version_id, block.id)
    if (next) navigate(`/a/${v.a.id}/b/${next.id}`)
    else if (toTest && !(v.manifest!.gate_test && v.requiredLeft > (done || !block.required ? 0 : 1))) {
      navigate(`/a/${v.a.id}/test`)
    } else navigate(`/a/${v.a.id}`)
  }

  return (
    <div className="stack">
      <div>
        <Link to={`/a/${v.a.id}`} className="btn btn-ghost btn-sm backlink">← К курсу</Link>
        <div className="spread" style={{ marginTop: '0.4rem' }}>
          <span className="q-count">Шаг {index + 1} из {blocks.length}</span>
          <span className="row-tight">
            <Pill>{BLOCK_LABEL[block.type]}</Pill>
            {done && <Pill tone="ok" icon={<CircleCheck aria-hidden />}>Изучено</Pill>}
          </span>
        </div>
        <ProgressBar value={index + 1} max={blocks.length} label="Шаг курса" />
        <h1 style={{ marginTop: '0.8rem' }}>{block.title || 'Материал'}</h1>
      </div>

      <BlockView block={block} source={localSource} onTrainerFinish={onTrainerFinish} />

      <div className="navrow">
        <button className="btn btn-secondary" disabled={!prev} onClick={() => prev && navigate(`/a/${v.a.id}/b/${prev.id}`)}>
          <ChevronLeft aria-hidden /> Назад
        </button>
        <button className="btn btn-primary" onClick={() => void forward()}>
          {next ? 'Дальше' : toTest ? 'К тесту' : 'Готово'} <ChevronRight aria-hidden />
        </button>
      </div>
    </div>
  )
}

export function TestPage() {
  const { id } = useParams()
  const { view: v, loaded } = useAssignmentView(id)
  const navigate = useNavigate()
  const net = useNet()
  const [attemptId, setAttemptId] = useState<string | null>(null)
  const [reviewing, setReviewing] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [sending, setSending] = useState(false)
  // Ответ сразу показывается на экране, а запись в IndexedDB идёт следом. Если бы поле ввода ждало
  // подтверждения из базы, при наборе текста прыгал бы курсор и терялись символы.
  const [typed, setTyped] = useState<Record<string, AnswerValue>>({})
  const lastSave = useRef<Promise<void>>(Promise.resolve())
  const { needsLogin } = useSession()
  const attempt = useLiveQuery<LocalAttempt | undefined>(
    () => (attemptId ? currentDb().attempts.get(attemptId) : undefined), [attemptId])

  const [starting, setStarting] = useState(false)

  // новая попытка начинается с чистого экрана: без набранных ответов прошлой и не с экрана «Проверьте ответы»
  useEffect(() => {
    setTyped({})
    setReviewing(false)
    setConfirm(false)
  }, [attemptId])

  useEffect(() => {
    if (v?.draft && !attemptId) setAttemptId(v.draft.id)
  }, [v?.draft?.id, attemptId]) // eslint-disable-line react-hooks/exhaustive-deps

  const questions = useMemo(() => v?.manifest?.questions ?? [], [v?.manifest])

  if (!loaded) return <LocalLoading />
  if (!v) return <GoneNotice />
  const back = <Link className="btn btn-primary" to={`/a/${v.a.id}`}>К курсу</Link>
  // там, где на экране уже есть главное действие (или итог), ссылка на курс — второстепенная
  const backQuiet = <Link className="btn btn-secondary btn-block" to={`/a/${v.a.id}`}>К курсу</Link>
  // редакция заменена новой: тест по ней не начинается и не продолжается, даже если остался черновик
  if (v.phase === 'replaced') {
    return (
      <div className="stack">
        <TestHeader v={v} />
        <div className="card stack">
          <MainAction v={v} />
          <Link className="btn btn-primary" to="/">К заданиям</Link>
        </div>
      </div>
    )
  }
  // Итог и подтверждение сдачи не зависят от того, скачан ли курс на это устройство,
  // поэтому показываются раньше проверки пакета.
  const submittedHere = attempt?.status === 'submitted'
  // Итог уже известен (тест проверен автоматически или методистом) либо сервер не принял ответы:
  // показываем это прямо здесь, без перехода на страницу курса. «Пройти ещё раз» и «Понятно»
  // возвращают экран к началу новой попытки.
  if (['passed', 'exhausted', 'rejected'].includes(v.phase) || (v.phase === 'retry' && submittedHere)) {
    return (
      <div className="stack">
        <TestHeader v={v} />
        <div className="card stack" aria-live="polite">
          <MainAction v={v} onAgain={() => setAttemptId(null)} />
        </div>
        {backQuiet}
      </div>
    )
  }

  // попытка сдана — показываем подтверждение, а не вопросы
  if (submittedHere || ['saved', 'review'].includes(v.phase)) {
    const queued = v.phase === 'saved' && v.savedQueued
    return (
      <div className="stack">
        <TestHeader v={v} />
        <div className="card center stack" style={{ padding: '2rem 1.2rem' }} aria-live="polite">
          <span className={`state-ico ${queued ? 'info' : 'ok'}`} style={{ margin: '0 auto' }} aria-hidden>
            {queued ? (deviceKind() === 'phone' ? <Smartphone /> : <HardDrive />) : <CircleCheck />}
          </span>
          <h1>{queued ? `Ответы сохранены ${DEVICE_WORD}` : v.phase === 'review' ? 'Результат получен отделом обучения' : 'Ответы отправлены'}</h1>
          <p className="muted">
            {queued
              ? needsLogin
                ? 'Сессия завершена. Войдите заново — ответы сохранены и уйдут сразу после входа.'
                : net.reachable === false
                  ? 'Связи с сервером нет. Отправим автоматически, как только она появится. Приложение можно закрыть — ответы не потеряются.'
                  : 'Отправляем в отдел обучения…'
              : v.phase === 'review'
                ? 'В тесте есть открытый ответ — его читает методист. Итог появится здесь сразу после проверки.'
                : 'Получаем подтверждение от сервера…'}
          </p>
          {back}
        </div>
      </div>
    )
  }
  if (!v.manifest) return <Empty title="Курс не скачан" action={back}>Скачайте курс, чтобы пройти тест.</Empty>
  if (!questions.length) return <Empty title="В этом курсе нет теста" action={back} />
  if (!attemptId && v.manifest.gate_test && v.requiredLeft > 0) {
    return (
      <Empty title="Сначала изучите материалы" action={back}>
        До теста осталось изучить: {v.requiredLeft} {plural(v.requiredLeft, 'материал', 'материала', 'материалов')}.
      </Empty>
    )
  }
  if (!attemptId && !['learn', 'test', 'retry', 'draft'].includes(v.phase)) {
    return <Empty title="Тест сейчас недоступен" action={back} />
  }

  // вступление перед началом
  if (!attemptId || !attempt) {
    return (
      <div className="stack">
        <Link to={`/a/${v.a.id}`} className="btn btn-ghost btn-sm backlink" style={{ alignSelf: 'flex-start' }}>← К курсу</Link>
        <TestHeader v={v} heading />
        {/* экран открыли заново после неудачной попытки: её итог не должен пропасть молча */}
        {v.phase === 'retry' && v.lastAttempt && (
          <Notice tone="danger" title="Прошлая попытка не сдана">
            Результат: {fmtNum(v.lastAttempt.percent)} %, нужно {v.manifest.pass_percent} %.
          </Notice>
        )}
        <div className="card">
          <ul className="prose" style={{ margin: 0, paddingLeft: '1.3rem' }}>
            <li>{questions.length} {plural(questions.length, 'вопрос', 'вопроса', 'вопросов')}, время не ограничено.</li>
            <li>Чтобы сдать, нужно набрать {v.manifest.pass_percent} %.</li>
            <li>Попытка {v.attemptsUsed + 1} из {v.manifest.max_attempts}.</li>
            <li>Ответы сохраняются {DEVICE_WORD}: тест можно прервать и продолжить, сеть не нужна.</li>
          </ul>
        </div>
        {/* Кнопка закреплена над нижним меню: она видна сразу, сколько бы ни занимал текст выше. */}
        <div className="navrow">
          <button className="btn btn-primary btn-lg" disabled={starting} onClick={async () => {
            if (starting) return
            setStarting(true)
            try {
              const a = await startOrResumeAttempt(currentDb(), v)
              setAttemptId(a.id)
            } finally {
              setStarting(false)
            }
          }}>Начать тест</button>
        </div>
      </div>
    )
  }

  const index = Math.min(attempt.index, questions.length - 1)
  const q = questions[index]
  const answers = { ...attempt.answers, ...typed }
  const unanswered = questions.filter((x) => !isAnswered(x, answers[x.id]))
  const go = (i: number) => {
    setReviewing(false)
    void saveIndex(currentDb(), attempt.id, Math.max(0, Math.min(questions.length - 1, i)))
    window.scrollTo(0, 0)
  }
  const onChange = (value: AnswerValue) => {
    setTyped((prev) => ({ ...prev, [q.id]: value }))
    lastSave.current = saveAnswer(currentDb(), attempt.id, q.id, value).catch(() => undefined)
  }
  const submit = async () => {
    if (sending) return
    setSending(true)
    try {
      // записи в базу идут по очереди: дождавшись последней, знаем, что сохранены все ответы
      await lastSave.current
      await submitAttempt(currentDb(), attempt.id)
      setConfirm(false)
      toast(`Ответы сохранены ${DEVICE_WORD}`, 'ok')
    } finally {
      setSending(false)
    }
  }

  if (reviewing) {
    return (
      <div className="stack">
        <h1>Проверьте ответы</h1>
        {unanswered.length > 0
          ? <Notice tone="warn" title={`Без ответа: ${unanswered.length}`}>Вопрос без ответа получит 0 баллов. Можно вернуться и ответить.</Notice>
          : <Notice tone="ok" title="Вы ответили на все вопросы" />}
        <div className="card listcard">
          {questions.map((x, i) => {
            const ok = isAnswered(x, answers[x.id])
            return (
              <button key={x.id} className="step" onClick={() => go(i)}>
                <span className={`step-ico ${ok ? 'done' : ''}`} aria-hidden>{ok ? <CircleCheck /> : <CircleAlert />}</span>
                <span className="grow">
                  <span className="step-title">Вопрос {i + 1}</span>
                  <span className="muted small" style={{ display: 'block' }}>{ok ? 'Ответ есть' : 'Нет ответа'} · {x.text.slice(0, 70)}{x.text.length > 70 ? '…' : ''}</span>
                </span>
                <span className="muted small">Изменить</span>
              </button>
            )
          })}
        </div>
        <div className="navrow">
          <button className="btn btn-secondary" onClick={() => go(questions.length - 1)}><ChevronLeft aria-hidden /> К вопросам</button>
          <button className="btn btn-primary" onClick={() => setConfirm(true)}><Send aria-hidden /> Сдать тест</button>
        </div>
        {confirm && (
          <Modal title="Сдать тест?" onClose={() => setConfirm(false)} actions={<>
            <button className="btn btn-secondary" onClick={() => setConfirm(false)} disabled={sending}>Вернуться</button>
            <button className="btn btn-primary" onClick={() => void submit()} disabled={sending}>Да, сдать</button>
          </>}>
            <p>После сдачи изменить ответы нельзя.{unanswered.length > 0 ? ` Без ответа осталось вопросов: ${unanswered.length}.` : ''}</p>
            <p className="muted" style={{ marginTop: '0.5rem' }}>
              {net.reachable === false
                ? `Связи нет — ответы сохранятся ${DEVICE_WORD} и уйдут в отдел обучения автоматически.`
                : 'Ответы сразу уйдут в отдел обучения.'}
            </p>
          </Modal>
        )}
      </div>
    )
  }

  return (
    <div className="stack">
      <div>
        <div className="spread">
          <span className="q-count">Вопрос {index + 1} из {questions.length}</span>
          <Pill>{QUESTION_LABEL[q.type]} · {q.points} {plural(q.points, 'балл', 'балла', 'баллов')}</Pill>
        </div>
        <ProgressBar value={index + 1} max={questions.length} label="Номер вопроса" />
      </div>
      <div className="card">
        <p className="q-text">{q.text}</p>
        <p className="muted small" style={{ marginBottom: '0.9rem' }}>{QUESTION_HINT[q.type]}</p>
        <QuestionView key={q.id} q={q} value={answers[q.id]} onChange={onChange} />
      </div>
      <div className="dots" aria-label="Переход к вопросу">
        {questions.map((x, i) => (
          <button key={x.id} className={`dotbtn ${isAnswered(x, answers[x.id]) ? 'answered' : ''} ${i === index ? 'current' : ''}`}
            onClick={() => go(i)} aria-label={`Вопрос ${i + 1}${isAnswered(x, answers[x.id]) ? ', ответ есть' : ', нет ответа'}`}
            aria-current={i === index ? 'step' : undefined}>{i + 1}</button>
        ))}
      </div>
      <div className="navrow">
        <button className="btn btn-secondary" disabled={index === 0} onClick={() => go(index - 1)}><ChevronLeft aria-hidden /> Назад</button>
        {index < questions.length - 1
          ? <button className="btn btn-primary" onClick={() => go(index + 1)}>Дальше <ChevronRight aria-hidden /></button>
          : <button className="btn btn-primary" onClick={() => { setReviewing(true); window.scrollTo(0, 0) }}>Проверить и сдать <ChevronRight aria-hidden /></button>}
      </div>
      <p className="muted small center">Ответ сохранён {DEVICE_WORD}. Тест можно закрыть и продолжить позже.</p>
    </div>
  )
}
