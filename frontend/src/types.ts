export interface Assessment {
  version: number
  assessment_id?: string
  revision?: number
  status: 'not_assessed' | 'pending' | 'normal' | 'abnormal' | 'needs_review'
  summary: string
  source: string
  reason?: string
  push_decision: 'suggested' | 'none' | 'undecided'
  delivery_status: 'not_connected'
  model_calls: number
  manual_model_calls_total?: number
  updated_at?: string | null
}

export interface TaskSnapshot {
  assessment?: Assessment
  id: string
  state: string
  user_request?: string
  session_key?: string
  created_at?: string
  updated_at?: string
  started_at?: string | null
  finished_at?: string | null
  duration_ms?: number | null
  queue_wait_ms?: number | null
  total_duration_ms?: number | null
  queue_position?: number | null
  latest_activity?: { type: string; at: string; tool: string; title: string } | null
  mode?: 'task' | 'conversation' | 'auto'
  trigger_type?: 'manual' | 'schedule'
  schedule_id?: string | null
  scheduled_for?: string | null
  metadata?: Record<string, unknown>
  last_reason?: string | null
}

export interface TaskCalendarDay {
  date: string
  count: number
  completed: number
  failed: number
  running: number
  scheduled: number
  manual: number
}

export interface TaskCalendarResponse {
  month: string
  days: TaskCalendarDay[]
}

export interface ProgressEvent {
  seq: number
  task_id: string
  type: string
  created_at?: string
  data?: Record<string, unknown>
}

export interface EvidenceItem {
  ref: string
  source: string
  raw: string
  tool_call_id?: string | null
  observed_at?: string
  metadata?: Record<string, unknown>
}

export interface EvidenceSnapshot {
  task_id: string
  session_key?: string
  items: EvidenceItem[]
}

export interface AnswerItem {
  text: string
  claim_ids: string[]
  kind: string
}

export interface ProductAnswer {
  version: number
  conclusion: AnswerItem[]
  explanation: AnswerItem[]
  execution: AnswerItem[]
  recommendations: AnswerItem[]
}

export interface ReportItem {
  text: string
  claim_ids: string[]
  evidence_refs: string[]
}

export interface ProductReport {
  version: 1
  conclusion: ReportItem
  facts: ReportItem[]
  possibilities: ReportItem[]
  next_steps: ReportItem[]
  limitations: ReportItem[]
}

export interface ResultPayload extends Record<string, unknown> {
  report?: ProductReport
  answer?: ProductAnswer
  answer_text?: string
  mode?: string
}

export interface ResultResponse {
  task_id: string
  state: string
  available: boolean
  result?: ResultPayload
  rendered?: string | null
}

export interface EventsResponse {
  task_id: string
  after: number
  next_after: number
  events: ProgressEvent[]
}

export interface ScheduleSnapshot {
  id: string
  name: string
  message: string
  kind: 'interval' | 'daily' | 'once'
  interval_minutes?: number | null
  daily_time?: string | null
  run_at?: string | null
  enabled: boolean
  assessment_enabled?: boolean
  created_at: string
  updated_at: string
  next_run_at?: string | null
  last_run_at?: string | null
  last_status?: string | null
  last_task_id?: string | null
  missed_count?: number
  last_missed_at?: string | null
}

export interface Evaluation {
  task_id: string
  rating: 'up' | 'down'
  tags: string[]
  note: string
  updated_at: string
}

export type DataPackageMode = 'image_evidence' | 'image_window' | 'encoder_window' | 'encoder_source_files' | 'working_plots'

export type Point = [number, number]

export interface CalibrationVersion {
  version: number
  created_at: string
  note: string
  frame_id: string | null
  warnings: string[]
  verify: { status: 'pending' | 'passed' | 'failed'; note: string; command: string }
}

export interface CalibrationCamera {
  camera: string
  active_version: number | null
  versions: CalibrationVersion[]
}

export interface CalibrationSaveResult {
  camera: string
  version: number
  active_version: number | null
  warnings: string[]
  verify: { status: string; note: string; command: string }
}

export interface MonitorCalibration {
  camera: string
  version: number
  source: string
}

export type MonitorState = 'running' | 'stopped' | 'backoff' | 'starting' | 'error'

export interface MonitorStatus {
  state: MonitorState
  user_stopped: boolean
  frame_age_s: number | null
  restart_count: number
  backoff_wait_s: number | null
  session_started_at: number | null
  source: string | null
  running_calibration: MonitorCalibration | null
  active_calibration: MonitorCalibration | null
  auto_confirm: boolean
  camera: string
  output_root: string
  last_error: string | null
}

export interface MonitorEvent {
  schema: number
  event_id: string
  status: 'open' | 'closed'
  rules: string[]
  opened_at: number
  last_seen_at: number
  center: number[]
  confirm_task_id?: string | null
  confirm_error?: string | null
  frame_count: number
  pre_count: number
  post_count: number
}

export interface DataPackageOption {
  mode: DataPackageMode
  label: string
  count: number | null
  recommended: boolean
}

export interface DataPackageSnapshot {
  task_id: string
  available: boolean
  options: DataPackageOption[]
  package_ready: boolean
  download_url?: string | null
  filename?: string
  bytes?: number
  collected_files?: number
  missing_files?: number
  package_modes?: DataPackageMode[]
}

export interface ActivitySnapshot {
  tasks: TaskSnapshot[]
  max_active_tasks: number
  max_queued_tasks: number
  occupied_slots: number
  running_count: number
  queued_count: number
  paused_count: number
  scopex_commit: string | null
}
