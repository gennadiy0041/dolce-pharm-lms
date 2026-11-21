// Кабинет сотрудника: всё читается из локальной базы, поэтому экран одинаково работает с сетью и без неё.
import { useLiveQuery } from './live'
import type {
  AnswerValue, AssignmentState, AttemptBrief, LocalAttempt, Manifest, OutboxEvent, PackageRow, RejectedEvent, ServerState,
} from '../types'
import { uuid, type ProgressRow, type UserDb } from './db'
import { currentDb } from './session'
import { enqueue, enqueueTx, kick } from './sync'
import { checkpoint, currentSessionId } from './tracker'

export type Phase =
  | 'download'        // пакета нет
  | 'resume'          // загрузка прервана
  | 'learn'           // изучение материалов
  | 'test'            // можно сдавать тест
  | 'draft'           // тест начат
  | 'saved'           // ответы сохранены на устройстве, ждут отправки
  | 'rejected'        // сервер не принял попытку
  | 'review'          // получено, ждёт проверки методистом
  | 'passed'
  | 'retry'           // не сдал, есть попытки
  | 'exhausted'       // не сдал, попыток нет
  | 'replaced'        // редакция заменена новой, назначение осталось только в истории
  | 'studied'         // курс без теста изучен

export interface AssignmentView {
  a: AssignmentState
  pkg?: PackageRow
  manifest?: Manifest
  phase: Phase
  draft?: LocalAttempt
  savedLocal?: LocalAttempt
  savedQueued: boolean       // true — ответы ещё на устройстве; false — отправлены, ждём ответ сервера
  rejected?: RejectedEvent
  lastAttempt?: AttemptBrief
  done: string[]
  lastBlockId: string | null
  requiredLeft: number
  attemptsUsed: number
  attemptsLeft: number
  updateAvailable: boolean
}

interface LocalData {
  server?: ServerState
  savedAt?: string
  packages: PackageRow[]
  attempts: LocalAttempt[]
  outbox: OutboxEvent[]
  rejected: RejectedEvent[]
  progress: ProgressRow[]
}

export function useLocalData(): LocalData | undefined {
  return useLiveQuery(async () => {
    const db = currentDb()
    const [state, packages, attempts, outbox, rejected, progress] = await Promise.all([
      db.state.get('server'), db.packages.toArray(), db.attempts.toArray(),
      db.outbox.where('type').equals('attempt.submit').toArray(), db.rejected.toArray(), db.progress.toArray(),
    ])
    return { server: state?.value as ServerState | undefined, savedAt: state?.savedAt, packages, attempts, outbox, rejected, progress }
  }, [])
}

export function buildView(a: AssignmentState, d: LocalData): AssignmentView {
  const pkg = d.packages.find((p) => p.versionId === a.version_id)
  const manifest = pkg?.manifest
  const mine = d.attempts.filter((t) => t.assignmentId === a.id)
  const serverIds = new Set(a.attempts.map((t) => t.id))
  const queued = new Set(d.outbox.map((e) => String(e.payload.attempt_id)))
  const rejectedById = new Map(d.rejected.filter((r) => r.type === 'attempt.submit').map((r) => [String(r.payload.attempt_id), r]))
  const draft = mine.find((t) => t.status === 'draft')
  // сданная попытка, которой ещё нет в ответе сервера: либо ждёт в очереди, либо уже отправлена
  // и результат подтянется при ближайшем обновлении
  const savedLocal = mine.find((t) => t.status === 'submitted' && !serverIds.has(t.id) && !rejectedById.has(t.id))
  const savedQueued = !!savedLocal && queued.has(savedLocal.id)
  const rejected = mine.map((t) => rejectedById.get(t.id)).find((r) => r && !serverIds.has(String(r.payload.attempt_id)))
  const local = d.progress.find((p) => p.versionId === a.version_id)
  const done = [...new Set([...(a.completed_block_ids ?? []), ...(local?.done ?? [])])]
  const required = manifest ? manifest.blocks.filter((b) => b.required).map((b) => b.id) : a.version.required_block_ids
  const requiredLeft = required.filter((id) => !done.includes(id)).length
  const lastAttempt = a.attempts.length ? a.attempts[a.attempts.length - 1] : undefined
  const attemptsUsed = a.attempts.length + (savedLocal ? 1 : 0)
  const attemptsLeft = Math.max(0, a.version.max_attempts - attemptsUsed)
  const hasTest = a.version.questions_count > 0
  const ready = pkg?.status === 'ready' && !!manifest

  const studiedAll = !hasTest && requiredLeft === 0 && done.length > 0
  let phase: Phase
  if (savedLocal) phase = 'saved'
  else if (a.state === 'passed') phase = 'passed'
  else if (a.state === 'review') phase = 'review'
  else if (a.state === 'exhausted') phase = 'exhausted'
  // назначение заменено новой редакцией: пересдавать старую редакцию не предлагаем
  else if (a.status === 'superseded') phase = 'replaced'
  else if (rejected && !draft) phase = 'rejected'
  // курс без теста уже изучен — остаётся изученным, даже если его удалили с устройства
  else if (studiedAll) phase = 'studied'
  else if (!ready) phase = pkg && (pkg.status === 'partial' || pkg.status === 'downloading') ? 'resume' : 'download'
  else if (draft) phase = 'draft'
  else if (a.state === 'failed') phase = attemptsLeft > 0 ? 'retry' : 'exhausted'
  else if (requiredLeft > 0) phase = 'learn'
  else phase = hasTest ? 'test' : 'studied'

  return {
    a, pkg, manifest, phase, draft, savedLocal, savedQueued, rejected, lastAttempt, done,
    lastBlockId: local?.lastBlockId ?? a.last_block_id, requiredLeft, attemptsUsed, attemptsLeft,
    updateAvailable: ready && !!a.package?.manifest_hash && manifest!.manifest_hash !== a.package.manifest_hash,
  }
}

const ORDER: Record<Phase, number> = {
  draft: 0, resume: 1, download: 2, learn: 2, test: 2, retry: 2, rejected: 2, saved: 5, review: 6, exhausted: 7, studied: 8, passed: 9,
  replaced: 10,
}

/** Задание больше не требует действий сотрудника. */
export const FINAL_PHASES: Phase[] = ['passed', 'studied', 'exhausted', 'replaced']

export function useAssignmentViews(): { views: AssignmentView[]; loaded: boolean; savedAt?: string } {
  const d = useLocalData()
  if (!d) return { views: [], loaded: false }
  const list = (d.server?.assignments ?? []).map((a) => buildView(a, d))
  list.sort((x, y) => {
    const dx = ORDER[x.phase] - ORDER[y.phase]
    const actionable = (p: Phase) => ORDER[p] <= 2
    if (actionable(x.phase) !== actionable(y.phase)) return dx
    if (actionable(x.phase)) {
      const ax = x.a.due_at ? Date.parse(x.a.due_at) : Infinity
      const ay = y.a.due_at ? Date.parse(y.a.due_at) : Infinity
      if (ax !== ay) return ax - ay
    }
    return dx || Date.parse(y.a.assigned_at) - Date.parse(x.a.assigned_at)
  })
  return { views: list, loaded: true, savedAt: d.savedAt }
}

export function useAssignmentView(assignmentId: string | undefined): { view?: AssignmentView; loaded: boolean } {
  const { views, loaded } = useAssignmentViews()
  return { view: views.find((v) => v.a.id === assignmentId), loaded }
}

// ---------- действия ----------

export async function markBlockDone(db: UserDb, versionId: string, blockId: string): Promise<void> {
  const row = (await db.progress.get(versionId)) ?? { versionId, done: [], lastBlockId: null, updatedAt: '' }
  if (row.done.includes(blockId)) return
  const at = new Date().toISOString()
  await db.transaction('rw', db.progress, db.outbox, async () => {
    await db.progress.put({ ...row, done: [...row.done, blockId], updatedAt: at })
    await enqueueTx(db, 'block.done', { version_id: versionId, block_id: blockId, at }, at)
  })
  kick('enqueue')
}

export async function rememberPosition(db: UserDb, versionId: string, blockId: string): Promise<void> {
  const row = (await db.progress.get(versionId)) ?? { versionId, done: [], lastBlockId: null, updatedAt: '' }
  if (row.lastBlockId === blockId) return
  const at = new Date().toISOString()
  await db.progress.put({ ...row, lastBlockId: blockId, updatedAt: at })
  await enqueue(db, 'position', { version_id: versionId, block_id: blockId, at }, at)
}

export async function startOrResumeAttempt(db: UserDb, view: AssignmentView): Promise<LocalAttempt> {
  // Черновик ищется в базе в одной транзакции с созданием: снимок на экране мог устареть,
  // и двойное нажатие «Начать тест» иначе завело бы две попытки по одному заданию.
  return db.transaction('rw', db.attempts, async () => {
    const existing = await db.attempts.where('assignmentId').equals(view.a.id).filter((t) => t.status === 'draft').first()
    if (existing) return existing
    const attempt: LocalAttempt = {
      id: uuid(), assignmentId: view.a.id, versionId: view.a.version_id, status: 'draft', answers: {}, touched: {},
      index: 0, startedAt: new Date().toISOString(), sessionId: currentSessionId(),
    }
    await db.attempts.add(attempt)
    return attempt
  })
}

/** Каждый ответ сразу пишется в IndexedDB: черновик переживает закрытие приложения. */
export async function saveAnswer(db: UserDb, attemptId: string, questionId: string, value: AnswerValue): Promise<void> {
  await db.attempts.where('id').equals(attemptId).modify((t) => {
    t.answers[questionId] = value
    t.touched[questionId] = true
  })
}

export async function saveIndex(db: UserDb, attemptId: string, index: number): Promise<void> {
  await db.attempts.update(attemptId, { index })
}

/** Завершение попытки и постановка в очередь отправки — одна транзакция. */
export async function submitAttempt(db: UserDb, attemptId: string): Promise<void> {
  await checkpoint()
  const at = new Date().toISOString()
  await db.transaction('rw', db.attempts, db.outbox, async () => {
    const t = await db.attempts.get(attemptId)
    if (!t || t.status !== 'draft') return
    await db.attempts.update(attemptId, { status: 'submitted', submittedAt: at })
    await enqueueTx(db, 'attempt.submit', {
      attempt_id: t.id, assignment_id: t.assignmentId, version_id: t.versionId, session_id: t.sessionId,
      started_at: t.startedAt, submitted_at: at, answers: t.answers,
    }, at)
  })
  kick('enqueue')
}

/**
 * Сотрудник прочитал, что сервер не принял ответы. Вместе с сообщением удаляется и сама локальная
 * попытка: на сервере её нет и не будет, а оставшись «сданной», она навсегда держала бы задание
 * в состоянии «ответы отправлены» и не давала начать новую попытку.
 */
export async function discardRejected(db: UserDb, eventId: string): Promise<void> {
  await db.transaction('rw', db.rejected, db.attempts, async () => {
    const row = await db.rejected.get(eventId)
    if (!row) return
    await db.rejected.delete(eventId)
    if (row.type !== 'attempt.submit' || !row.payload.attempt_id) return
    const id = String(row.payload.attempt_id)
    const t = await db.attempts.get(id)
    if (t?.status === 'submitted') await db.attempts.delete(id)
  })
}
