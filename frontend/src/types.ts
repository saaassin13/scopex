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

export interface ProductAnswerItem {
  claim_id: string
  text: string
  kind: string
  relation: string
  confidence: string
  evidence_refs: string[]
}

export interface ProductAnswer {
  schema_version: number
  conclusion: ProductAnswerItem[]
  explanation: ProductAnswerItem[]
  execution: ProductAnswerItem[]
  recommendation: ProductAnswerItem[]
}

export interface AnswerComposerStatus {
  valid?: boolean
  errors?: string[]
  parse_error?: string | null
  finish_reasons?: string[]
  elapsed_s?: number
  usage?: Record<string, unknown> | null
}

export interface ResultResponse {
  task_id: string
  state: string
  available: boolean
  result?: Record<string, unknown>
  product_answer?: ProductAnswer | null
  answer_composer?: AnswerComposerStatus | null
  rendered?: string | null
}

export interface EventsResponse {
  task_id: string
  after: number
  next_after: number
  events: ProgressEvent[]
}
