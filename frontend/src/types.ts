// Общие типы клиента. Совпадают с ответами REST API.

export type Role = 'employee' | 'manager' | 'methodist'

export interface User {
  id: string
  personnel_no: string
  full_name: string
  login: string
  role: Role
  role_label: string
  department_id: string | null
  department: string | null
  position: string
  is_active: boolean
  managed_department_ids?: string[]
}

export interface ClientSettings {
  heartbeat_seconds: number
  idle_timeout_seconds: number
  reading_idle_seconds: number
  local_retention_days: number
}

export interface FileRef {
  id: string
  sha256: string
  size: number
  mime: string
  name: string
  ext: string
  pages?: number | null
}

export type BlockType = 'text' | 'link' | 'pdf' | 'video' | 'docx' | 'xlsx' | 'pptx' | 'trainer'

export interface ManifestBlock {
  id: string
  type: BlockType
  title: string
  required: boolean
  position: number
  text?: string
  url?: string
  mandatory?: boolean
  note?: string
  needs_internet?: boolean
  file?: FileRef
  view?: FileRef
  scenario?: Scenario
}

export type QuestionType = 'single' | 'multiple' | 'order' | 'open'

export interface ManifestQuestion {
  id: string
  type: QuestionType
  text: string
  points: number
  position: number
  options: { id: string; text: string }[]
}

export interface Manifest {
  version_id: string
  course_id: string
  title: string
  sop_code: string
  revision: number
  description: string
  change_note: string
  status: string
  published_at: string | null
  pass_percent: number
  max_attempts: number
  gate_test: boolean
  blocks: ManifestBlock[]
  questions: ManifestQuestion[]
  files: FileRef[]
  total_size: number
  manifest_hash: string
}

export interface AttemptBrief {
  id: string
  assignment_id: string
  version_id: string
  attempt_no: number
  status: 'review' | 'passed' | 'failed'
  status_label: string
  score: number
  max_score: number
  percent: number | null
  submitted_at: string
  received_at: string
  reviewed_at: string | null
  review_comment: string
  source: string
  certificate_id: string | null
  certificate_number: string | null
}

export interface AssignmentState {
  id: string
  course_id: string
  version_id: string
  kind: 'initial' | 'reacquaint'
  status: string
  due_at: string | null
  assigned_at: string
  state: 'todo' | 'review' | 'passed' | 'failed' | 'exhausted'
  version: {
    id: string
    title: string
    sop_code: string
    revision: number
    status: string
    description: string
    change_note: string
    pass_percent: number
    max_attempts: number
    gate_test: boolean
    blocks_count: number
    questions_count: number
    required_block_ids: string[]
  }
  package: { total_size: number; manifest_hash: string; files: number; blocks: number; questions: number }
  attempts: AttemptBrief[]
  completed_block_ids: string[]
  last_block_id: string | null
}

export interface CertificateInfo {
  id: string
  number: string
  issued_at: string
  attempt_id: string
  file_id: string
  title: string
  sop_code: string
  revision: number
}

export interface ServerState {
  server_time: string
  assignments: AssignmentState[]
  certificates: CertificateInfo[]
  pending_assessments: number
  trainer_results: { id: string; version_id: string; block_id: string; ending_id: string; ending_title: string; outcome: string; finished_at: string }[]
}

export interface LibraryItem {
  id: string
  title: string
  sop_code: string
  category: string
  description: string
  updated_at: string
  file: FileRef
  view: FileRef | null
  size: number
  conversion_status?: string
  conversion_error?: string | null
  /** текст документа (до 20 000 символов) — для поиска без сети */
  text?: string
}

// ---- тренажёр ----
export interface ScenarioChoice { id: string; text: string; next: string }
export interface ScenarioNode {
  speaker?: 'narrator' | 'character'
  emotion?: string
  text: string
  choices?: ScenarioChoice[]
  ending?: boolean
  outcome?: 'good' | 'mixed' | 'bad'
  title?: string
  consequences?: string[]
  rule?: string
}
export interface Scenario {
  id: string
  title: string
  summary?: string
  intro: string
  background: string
  character: { name: string; role: string; images: Record<string, string> }
  emotion_labels?: Record<string, string>
  start: string
  nodes: Record<string, ScenarioNode>
}

// ---- локальные записи на устройстве ----
export type AnswerValue = string | string[] | null

export interface LocalAttempt {
  id: string
  assignmentId: string
  versionId: string
  status: 'draft' | 'submitted'
  answers: Record<string, AnswerValue>
  touched: Record<string, boolean>
  index: number
  startedAt: string
  submittedAt?: string
  sessionId: string
}

export interface OutboxEvent {
  id: string
  type: string
  deviceTime: string
  createdAt: number
  payload: Record<string, unknown>
  tries: number
  lastError?: string
}

export interface RejectedEvent {
  id: string
  type: string
  payload: Record<string, unknown>
  code: string
  reason: string
  at: string
}

export interface PackageRow {
  versionId: string
  status: 'downloading' | 'partial' | 'ready' | 'error'
  manifest?: Manifest            // последняя проверенная копия — ею пользуемся без сети
  pending?: Manifest             // манифест загружаемого обновления
  doneBytes: number
  totalBytes: number
  verifiedAt?: string
  error?: string
  errorKind?: 'network' | 'quota' | 'integrity' | 'auth' | 'cancelled' | 'paused' | 'blocked' | 'other'
}
