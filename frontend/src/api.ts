import type {
  Assessment,
  ActivitySnapshot,
  Evaluation,
  EvidenceSnapshot,
  EventsResponse,
  ResultResponse,
  ScheduleSnapshot,
  TaskCalendarResponse,
  TaskSnapshot,
  DataPackageMode,
  DataPackageSnapshot,
  CalibrationCamera,
  CalibrationSaveResult,
  CalibrationVersion,
  MonitorEvent,
  MonitorStatus,
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
  activity: () => request<ActivitySnapshot>('/activity'),
  cancelQueued: (id: string) => request<TaskSnapshot>(`/tasks/${encodeURIComponent(id)}/cancel-queued`, { method: 'POST', body: '{}' }),
  health: () => request<{ status: string; active_task_id: string | null }>('/health'),
  createRun: (message: string, assessment_enabled = false) =>
    request<TaskSnapshot>('/runs', { method: 'POST', body: JSON.stringify({ message, assessment_enabled }) }),
  listTasks: (options?: { mode?: 'task' | 'conversation' | 'auto'; day?: string; schedule_id?: string; limit?: number; offset?: number; state?: string; assessment_status?: string; push_decision?: string; query?: string; trigger_type?: 'manual' | 'schedule'; attention?: boolean }) => {
    const params = new URLSearchParams()
    params.set('include_preview', 'true')
    if (options?.query) params.set('query', options.query)
    if (options?.trigger_type) params.set('trigger_type', options.trigger_type)
    if (options?.attention) params.set('attention', 'true')
    if (options?.state) params.set('state', options.state)
    if (options?.assessment_status) params.set('assessment_status', options.assessment_status)
    if (options?.push_decision) params.set('push_decision', options.push_decision)
    if (options?.mode) params.set('mode', options.mode)
    if (options?.day) params.set('day', options.day)
    if (options?.schedule_id) params.set('schedule_id', options.schedule_id)
    if (options?.limit) params.set('limit', String(options.limit))
    if (options?.offset) params.set('offset', String(options.offset))
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
    request<{ task_id: string; deleted: boolean; collected_business_data_deleted: boolean; external_business_data_deleted: boolean }>(`/tasks/${encodeURIComponent(id)}`, { method: 'DELETE' }),
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
  assessTask: (id: string, allow_model = false, retry = false) =>
    request<{ task_id: string; assessment: Assessment; requires_model?: boolean }>(`/tasks/${encodeURIComponent(id)}/assessment`, {
      method: 'POST', body: JSON.stringify({ allow_model, retry }),
    }),
  getEvaluation: (id: string) =>
    request<{ task_id: string; evaluation: Evaluation | null }>(`/tasks/${encodeURIComponent(id)}/evaluation`),
  setEvaluation: (id: string, rating: 'up' | 'down', tags: string[], note: string) =>
    request<Evaluation>(`/tasks/${encodeURIComponent(id)}/evaluation`, {
      method: 'POST', body: JSON.stringify({ rating, tags, note }),
    }),
  exportUrl: (id: string) => `/tasks/${encodeURIComponent(id)}/export`,
  getImages: (id: string) => request<{ images: Array<{ index: number; filename: string; source: string }> }>(`/tasks/${encodeURIComponent(id)}/images`),
  imageUrl: (id: string, index: number) => `/tasks/${encodeURIComponent(id)}/images/${index}`,
  getPlots: (id: string) => request<{ plots: Array<{ index: number; filename: string; candidate_id: string | null; kind: string }> }>(`/tasks/${encodeURIComponent(id)}/plots`),
  plotUrl: (id: string, index: number) => `/tasks/${encodeURIComponent(id)}/plots/${index}`,
  getDataPackage: (id: string) =>
    request<DataPackageSnapshot>(`/tasks/${encodeURIComponent(id)}/data-package`),
  buildDataPackage: (id: string, modes: DataPackageMode[]) =>
    request<DataPackageSnapshot>(`/tasks/${encodeURIComponent(id)}/data-package`, {
      method: 'POST', body: JSON.stringify({ modes }),
    }),
  dataPackageDownloadUrl: (id: string) => `/tasks/${encodeURIComponent(id)}/data-package/download`,
  listSchedules: () => request<{ schedules: ScheduleSnapshot[] }>('/schedules'),
  createSchedule: (payload: {
    name: string
    message: string
    kind: 'interval' | 'daily' | 'once'
    interval_minutes?: number
    daily_time?: string
    run_at?: string
    enabled?: boolean
    assessment_enabled?: boolean
  }) => request<ScheduleSnapshot>('/schedules', { method: 'POST', body: JSON.stringify(payload) }),
  updateSchedule: (id: string, payload: { name: string; message: string; kind: 'interval' | 'daily' | 'once'; interval_minutes?: number; daily_time?: string; run_at?: string; assessment_enabled: boolean }) =>
    request<ScheduleSnapshot>(`/schedules/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  setScheduleAssessment: (id: string, assessment_enabled: boolean) =>
    request<ScheduleSnapshot>(`/schedules/${encodeURIComponent(id)}/assessment`, {
      method: 'PATCH', body: JSON.stringify({ assessment_enabled }),
    }),
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
  listCalibrations: () => request<{ cameras: CalibrationCamera[] }>('/calibrations'),
  getCalibration: (camera: string) =>
    request<CalibrationCamera>(`/calibrations/${encodeURIComponent(camera)}`),
  getCalibrationVersion: (camera: string, version: number) =>
    request<{ camera: string; version: number; config: Record<string, unknown> }>(
      `/calibrations/${encodeURIComponent(camera)}/versions/${version}/config`),
  uploadCalibrationFrame: (camera: string, imageB64: string) =>
    request<{ frame_id: string }>(`/calibrations/${encodeURIComponent(camera)}/frames`, {
      method: 'POST', body: JSON.stringify({ image_b64: imageB64 }),
    }),
  calibrationFrameUrl: (camera: string, frameId: string) =>
    `/calibrations/${encodeURIComponent(camera)}/frames/${encodeURIComponent(frameId)}`,
  saveCalibration: (camera: string, config: Record<string, unknown>, note: string, frameId: string | null) =>
    request<CalibrationSaveResult>(`/calibrations/${encodeURIComponent(camera)}`, {
      method: 'POST', body: JSON.stringify({ config, note, frame_id: frameId }),
    }),
  activateCalibration: (camera: string, version: number) =>
    request<{ camera: string; active_version: number }>(
      `/calibrations/${encodeURIComponent(camera)}/versions/${version}/activate`, {
        method: 'POST', body: '{}',
      }),
  verifyCalibration: (camera: string, version: number, result: 'pending' | 'passed' | 'failed', note: string) =>
    request<{ camera: string; version: number; verify: CalibrationVersion['verify'] }>(
      `/calibrations/${encodeURIComponent(camera)}/versions/${version}/verify`, {
        method: 'POST', body: JSON.stringify({ result, note }),
      }),
  monitorStatus: () => request<MonitorStatus>('/monitor/status'),
  monitorStart: () => request<MonitorStatus>('/monitor/start', { method: 'POST' }),
  monitorStop: () => request<MonitorStatus>('/monitor/stop', { method: 'POST' }),
  monitorEvents: (limit = 100) =>
    request<{ events: MonitorEvent[] }>(`/monitor/events?limit=${limit}`),
  monitorConfirm: (eventId: string) =>
    request<{ ok: boolean; queued: string }>(
      `/monitor/events/${encodeURIComponent(eventId)}/confirm`, { method: 'POST' }),
  monitorFrameUrl: (eventId: string, name: string) =>
    `/monitor/events/${encodeURIComponent(eventId)}/frames/${encodeURIComponent(name)}`,
}
