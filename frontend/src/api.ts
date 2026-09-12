import type {
  EvidenceSnapshot,
  EventsResponse,
  ResultResponse,
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
  listTasks: () => request<{ tasks: TaskSnapshot[] }>('/tasks'),
  createTask: (message: string) =>
    request<TaskSnapshot>('/tasks', { method: 'POST', body: JSON.stringify({ message }) }),
  getTask: (id: string) => request<TaskSnapshot>(`/tasks/${encodeURIComponent(id)}`),
  getEvents: (id: string, after = 0) =>
    request<EventsResponse>(`/tasks/${encodeURIComponent(id)}/events?after=${after}`),
  getEvidence: (id: string) =>
    request<EvidenceSnapshot>(`/tasks/${encodeURIComponent(id)}/evidence`),
  getResult: (id: string) =>
    request<ResultResponse>(`/tasks/${encodeURIComponent(id)}/result`),
  stop: (id: string, message = '') =>
    request<TaskSnapshot>(`/tasks/${encodeURIComponent(id)}/stop`, {
      method: 'POST',
      body: JSON.stringify({ message }),
    }),
  resume: (id: string, message: string) =>
    request<TaskSnapshot>(`/tasks/${encodeURIComponent(id)}/resume`, {
      method: 'POST',
      body: JSON.stringify({ message }),
    }),
  steer: (id: string, message: string) =>
    request<TaskSnapshot>(`/tasks/${encodeURIComponent(id)}/steer`, {
      method: 'POST',
      body: JSON.stringify({ message }),
    }),
}
