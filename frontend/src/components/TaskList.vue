<script setup lang="ts">
import { RouterLink } from 'vue-router'
import type { TaskSnapshot } from '../types'
import { executionLabels } from '../assessment'
import { taskResult, taskTitle } from '../taskPresentation'
import ResultBadge from './ResultBadge.vue'
defineProps<{ tasks: TaskSnapshot[]; scheduleNames?: Record<string, string>; deletingId?: string; allowDelete?: boolean }>()
defineEmits<{ delete: [task: TaskSnapshot] }>()
function fmt(value?: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'
}
</script>

<template>
  <div class="task-list">
    <div class="task-list-labels" aria-hidden="true"><span>任务</span><span>结果</span><span>执行时间 / 状态</span></div>
    <article v-for="task in tasks" :key="task.id" class="task-list-row">
      <RouterLink :to="`/tasks/${task.id}`" class="task-list-link">
        <div class="task-list-title">
          <span class="task-origin">{{ task.trigger_type === 'schedule' ? '定时检查' : '手动任务' }}</span>
          <h3>{{ (task.schedule_id && scheduleNames?.[task.schedule_id]) || taskTitle(task) }}</h3>
          <span class="task-open">查看详情 →</span>
        </div>
        <div class="task-list-result">
          <ResultBadge v-if="task.assessment && task.assessment.status !== 'not_assessed'" :value="task.assessment" />
          <span v-else class="muted result-kind">{{ taskResult(task).label }}</span>
          <p>{{ taskResult(task).text }}</p>
        </div>
        <div class="task-list-time">
          <time>{{ fmt(task.started_at || task.created_at) }}</time>
          <span class="state-pill" :data-state="task.state">{{ executionLabels[task.state] || task.state }}</span>
        </div>
      </RouterLink>
      <button v-if="allowDelete && ['COMPLETED', 'FAILED', 'CANCELLED'].includes(task.state)"
        class="task-list-delete" :disabled="!!deletingId" :aria-label="`删除任务：${taskTitle(task)}`"
        @click="$emit('delete', task)">{{ deletingId === task.id ? '删除中' : '删除' }}</button>
    </article>
  </div>
</template>

<style scoped>
.task-list-labels, .task-list-link { display: grid; grid-template-columns: minmax(180px, 1fr) minmax(260px, 1.6fr) 150px; gap: 28px; }
.task-list-labels { padding: 0 64px 12px 16px; color: var(--muted); font-size: 13px; border-bottom: 1px solid var(--panel-border); }
.task-list-row { position: relative; border-bottom: 1px solid var(--panel-border); }
.task-list-link { padding: 24px 64px 24px 16px; align-items: start; border-radius: 8px; }
.task-list-link:hover { background: rgba(157,240,200,.035); }
.task-list-title h3 { font-size: 16px; font-weight: 650; margin: 8px 0; line-height: 1.6; overflow-wrap: anywhere; }
.task-origin, .task-open, .result-kind { font-size: 12px; color: var(--muted); }
.task-open { color: var(--accent); }
.task-list-result p { margin: 9px 0 0; line-height: 1.7; font-size: 14px; color: #d3dbe8; overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.task-list-time { display: grid; gap: 12px; font-size: 13px; color: var(--muted); }
.task-list-delete { position: absolute; right: 0; top: 19px; padding: 12px 8px; color: var(--muted); background: none; border: none; font-size: 12px; }
.task-list-delete:hover { color: var(--danger); }
@media (max-width: 900px) {
  .task-list-labels { display: none; }
  .task-list-link { grid-template-columns: minmax(0, 1fr) 120px; gap: 16px; padding: 20px 48px 20px 0; }
  .task-list-result { grid-row: 2; grid-column: 1 / -1; }
}
@media (max-width: 500px) {
  .task-list-link { grid-template-columns: minmax(0, 1fr); }
  .task-list-time { grid-row: 3; display: flex; align-items: center; flex-wrap: wrap; }
}
</style>
