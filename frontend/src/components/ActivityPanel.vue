<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '../api'
import type { ActivitySnapshot, TaskSnapshot } from '../types'

const data = ref<ActivitySnapshot | null>(null)
const open = ref(false)
const error = ref('')
const actionError = ref('')
const now = ref(Date.now())
let disposed = false
let busy = false
let timer: number | undefined

async function refresh() {
  if (busy) return
  busy = true
  try {
    const response = await api.activity()
    if (!disposed) { data.value = response; error.value = '' }
  } catch {
    if (!disposed) error.value = '状态暂不可确认'
  } finally { busy = false }
}
function phase(task: TaskSnapshot) {
  const states: Record<string, string> = {
    QUEUED: '等待执行', CREATED: '准备运行环境', PAUSED: '已暂停（保留名额）',
    PAUSING: '正在安全暂停', FINALIZING: '生成报告',
  }
  if (states[task.state]) return states[task.state]
  if (task.latest_activity?.type === 'MODEL_REQUEST') return '模型处理中（含模型侧等待）'
  if (task.latest_activity?.type === 'TOOL_CALL') return `执行工具 · ${task.latest_activity.tool}`
  return '调查中'
}
function elapsed(task: TaskSnapshot) {
  const start = task.state === 'QUEUED' ? task.created_at : task.started_at || task.created_at
  const seconds = start ? Math.max(0, Math.floor((now.value - Date.parse(start)) / 1000)) : 0
  return `${Math.floor(seconds / 60)}分${seconds % 60}秒`
}
async function act(task: TaskSnapshot, kind: 'pause' | 'cancel') {
  actionError.value = ''
  try {
    if (kind === 'cancel') await api.cancelQueued(task.id)
    else await api.stop(task.id)
    await refresh()
  } catch (exc) { actionError.value = exc instanceof Error ? exc.message : String(exc) }
}
function escape(event: KeyboardEvent) { if (event.key === 'Escape') open.value = false }
onMounted(() => {
  void refresh()
  timer = window.setInterval(() => { now.value = Date.now(); void refresh() }, 2000)
  window.addEventListener('keydown', escape)
})
onBeforeUnmount(() => {
  disposed = true
  window.clearInterval(timer)
  window.removeEventListener('keydown', escape)
})
</script>

<template>
  <button class="activity-trigger" :aria-expanded="open" aria-controls="activity-drawer" @click="open = !open">
    {{ error || (data ? `运行中 ${data.running_count} · 等待 ${data.queued_count} · 暂停 ${data.paused_count}` : '连接 Runtime…') }}
  </button>
  <Teleport to="body">
    <div v-if="open" class="activity-backdrop" @click="open = false"></div>
    <aside v-if="open" id="activity-drawer" class="activity-drawer" role="dialog" aria-modal="true" aria-label="当前活动任务">
      <div class="activity-heading"><h2>当前活动任务</h2><button class="ghost-button" @click="open = false">关闭</button></div>
      <p class="muted">所有日期 · 独立任务 · 不扫描业务目录</p>
      <p v-if="error" class="error-banner">{{ error }}；下方可能是旧状态，不能据此判断任务仍在运行。</p>
      <p v-if="actionError" class="error-banner">{{ actionError }}</p>
      <p v-if="data" class="muted">执行名额 {{ data.occupied_slots }}/{{ data.max_active_tasks }} · 等待队列 {{ data.queued_count }}/{{ data.max_queued_tasks }}</p>
      <p v-if="data && !data.tasks.length && !error" class="empty-state">当前没有活动任务。</p>
      <article v-for="task in data?.tasks || []" :key="task.id" class="activity-card">
        <RouterLink :to="`/tasks/${task.id}`" @click="open = false">{{ task.user_request || task.id }}</RouterLink>
        <p>{{ phase(task) }} · {{ task.state === 'QUEUED' ? '等待' : '已开始' }} {{ elapsed(task) }}</p>
        <p v-if="task.queue_position">队列位置 {{ task.queue_position }}</p>
        <p class="muted">{{ task.trigger_type === 'schedule' ? '定时触发' : '手动任务' }} · {{ task.id }}</p>
        <p v-if="task.latest_activity?.title" class="muted">{{ task.latest_activity.title }}</p>
        <div class="activity-actions">
          <button v-if="task.state === 'QUEUED'" class="ghost-button" :disabled="!!error" @click="act(task, 'cancel')">取消排队</button>
          <button v-if="task.state === 'RUNNING'" class="ghost-button" :disabled="!!error" @click="act(task, 'pause')">暂停此任务</button>
        </div>
      </article>
    </aside>
  </Teleport>
</template>
