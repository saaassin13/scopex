import type { TaskSnapshot } from './types'

export function taskTitle(task: TaskSnapshot): string {
  const text = (task.user_request || task.id).trim().split(/[\n。！？]/)[0] || task.id
  return text.length > 64 ? `${text.slice(0, 64)}…` : text
}

export function taskResult(task: TaskSnapshot): { label: string; text: string } {
  if (!['COMPLETED', 'FAILED', 'CANCELLED'].includes(task.state)) {
    return { label: '进度', text: task.latest_activity?.title || '等待任务结果' }
  }
  const assessment = task.assessment
  if (assessment?.summary && ['native', 'manual_text'].includes(assessment.source)) {
    return { label: task.state === 'COMPLETED' ? '结果摘要' : '未完成结果', text: assessment.summary }
  }
  if (task.result_preview) return {
    label: task.state === 'COMPLETED' ? '正文摘录' : '未完成草稿', text: task.result_preview,
  }
  return { label: '结果', text: task.state === 'FAILED' ? '任务未完成，打开查看原因和已有记录'
    : task.state === 'CANCELLED' ? '任务已取消' : '打开查看完整结果' }
}
