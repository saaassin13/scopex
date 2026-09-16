import type { Assessment } from './types'

export const assessmentLabels: Record<string, string> = {
  not_assessed: '未评估', pending: '待判定', normal: '未见异常',
  abnormal: '异常', needs_review: '需复核',
}
export const pushLabels: Record<string, string> = {
  suggested: '建议推送', none: '不推送', undecided: '未判定推送',
}
export const executionLabels: Record<string, string> = {
  CREATED: '准备中', QUEUED: '排队中', RUNNING: '执行中', PAUSING: '暂停中',
  PAUSED: '已暂停', FINALIZING: '保存结果中', COMPLETED: '已完成', FAILED: '失败', CANCELLED: '已取消',
}
export function assessmentLabel(value?: Assessment | null): string {
  return assessmentLabels[value?.status || 'not_assessed'] || '需复核'
}
export interface TaskFilters {
  state?: string
  assessment_status?: string
  push_decision?: string
}
