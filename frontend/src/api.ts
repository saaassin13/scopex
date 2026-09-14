import type {
  Evaluation,
  EvidenceSnapshot,
  EventsResponse,
  ResultResponse,
  ScheduleSnapshot,
  TaskCalendarResponse,
  TaskSnapshot,
} from './types'

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = payload?.error ?? {}
    throw new ApiError(response.status, error.code ?? 'request_failed', error.message ?? response.statusText)
  }
  return payload as T
}

export const api = {
  health: () => request<{ status: string; active_task_id: string | null }>('/health'),
  createRun: (message: string) =>
    request<TaskSnapshot>('/runs', { method: 'POST', body: JSON.stringify({ message }) }),
  listTasks: (options?: { mode?: 'task' | 'conversation' | 'auto'; day?: string }) => {
    const params = new URLSearchParams()
    if (options?.mode) params.set('mode', options.mode)
    if (options?.day) params.set('day', options.day)
    const query = params.toString()
    return request<{ tasks: TaskSnapshot[] }>(query ? `/tasks?${query}` : '/tasks')
  },
  getTaskCalendar: (month: string) =>
    request<TaskCalendarResponse>(`/tasks/calendar?month=${encodeURIComponent(month)}`),
  createTask: (message: string) =>
    request<TaskSnapshot>('/tasks', { method: 'POST', body: JSON.stringify({ message }) }),
  createConversation: (message: string) =>
    request<TaskSnapshot>('/conversations', { method: 'POST', body: JSON.stringify({ message }) }),
  continueConversation: (id: string, message: string) =>
    request<TaskSnapshot>(`/conversations/${encodeURIComponent(id)}/messages`, {
      method: 'POST', body: JSON.stringify({ message }),
    }),
  getTask: (id: string) => request<TaskSnapshot>(`/tasks/${encodeURIComponent(id)}`),
  deleteTask: (id: string) =>
    request<{ task_id: string; deleted: boolean; external_business_data_deleted: boolean }>(`/tasks/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  getEvents: (id: string, after = 0) =>
    request<EventsResponse>(`/tasks/${encodeURIComponent(id)}/events?after=${after}`),
  getEvidence: (id: string) =>
    request<EvidenceSnapshot>(`/tasks/${encodeURIComponent(id)}/evidence`),
  getResult: (id: string) =>
    request<ResultResponse>(`/tasks/${encodeURIComponent(id)}/result`),
  stop: (id: string, message = '') =>
    request<TaskSnapshot>(`/tasks/${encodeURIComponent(id)}/stop`, {
      method: 'POST', body: JSON.stringify({ message }),
    }),
  resume: (id: string, message: string) =>
    request<TaskSnapshot>(`/tasks/${encodeURIComponent(id)}/resume`, {
      method: 'POST', body: JSON.stringify({ message }),
    }),
  steer: (id: string, message: string) =>
    request<TaskSnapshot>(`/tasks/${encodeURIComponent(id)}/steer`, {
      method: 'POST', body: JSON.stringify({ message }),
    }),
  getEvaluation: (id: string) =>
    request<{ task_id: string; evaluation: Evaluation | null }>(`/tasks/${encodeURIComponent(id)}/evaluation`),
  setEvaluation: (id: string, rating: 'up' | 'down', tags: string[], note: string) =>
    request<Evaluation>(`/tasks/${encodeURIComponent(id)}/evaluation`, {
      method: 'POST', body: JSON.stringify({ rating, tags, note }),
    }),
  exportUrl: (id: string) => `/tasks/${encodeURIComponent(id)}/export`,
  listSchedules: () => request<{ schedules: ScheduleSnapshot[] }>('/schedules'),
  createSchedule: (payload: {
    name: string
    message: string
    kind: 'interval' | 'daily' | 'once'
    interval_minutes?: number
    daily_time?: string
    run_at?: string
    enabled?: boolean
  }) => request<ScheduleSnapshot>('/schedules', { method: 'POST', body: JSON.stringify(payload) }),
  setScheduleEnabled: (id: string, enabled: boolean) =>
    request<ScheduleSnapshot>(`/schedules/${encodeURIComponent(id)}/enabled`, {
      method: 'PATCH', body: JSON.stringify({ enabled }),
    }),
  runScheduleNow: (id: string) =>
    request<Record<string, unknown>>(`/schedules/${encodeURIComponent(id)}/run`, { method: 'POST', body: '{}' }),
  deleteSchedule: async (id: string) => {
    const response = await fetch(`/schedules/${encodeURIComponent(id)}`, { method: 'DELETE' })
    if (!response.ok) throw new ApiError(response.status, 'request_failed', response.statusText)
  },
}
