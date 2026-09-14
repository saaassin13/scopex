export interface TaskSnapshot {
  id: string
  state: string
  user_request?: string
  session_key?: string
  created_at?: string
  updated_at?: string
  started_at?: string | null
  finished_at?: string | null
  duration_ms?: number | null
  mode?: 'task' | 'conversation'
  trigger_type?: 'manual' | 'schedule'
  schedule_id?: string | null
  scheduled_for?: string | null
  metadata?: Record<string, unknown>
  last_reason?: string | null
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

export interface ResultPayload extends Record<string, unknown> {
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
  created_at: string
  updated_at: string
  next_run_at?: string | null
  last_run_at?: string | null
  last_status?: string | null
  last_task_id?: string | null
}

export interface Evaluation {
  task_id: string
  rating: 'up' | 'down'
  tags: string[]
  note: string
  updated_at: string
}
