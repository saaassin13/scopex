export interface TaskSnapshot {
  id: string
  state: string
  user_request?: string
  session_key?: string
  created_at?: string
  updated_at?: string
  metadata?: Record<string, unknown>
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
