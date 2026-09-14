<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError } from '../api'
import type { TaskSnapshot } from '../types'

const router = useRouter()
const tasks = ref<TaskSnapshot[]>([])
const message = ref('')
const mode = ref<'task' | 'conversation'>('task')
const loading = ref(false)
const error = ref('')
let timer: number | undefined

function fmt(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function duration(task: TaskSnapshot) {
  if (typeof task.duration_ms !== 'number') return ''
  const sec = Math.max(0, Math.round(task.duration_ms / 1000))
  if (sec < 60) return `${sec}s`
  return `${Math.floor(sec / 60)}m ${sec % 60}s`
}

async function refresh() {
  try {
    tasks.value = (await api.listTasks()).tasks
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc)
  }
}

async function createEntry() {
  const value = message.value.trim()
  if (!value || loading.value) return
  loading.value = true
  error.value = ''
  try {
    const task = mode.value === 'conversation'
      ? await api.createConversation(value)
      : await api.createTask(value)
    message.value = ''
    await router.push(`/tasks/${task.id}`)
  } catch (exc) {
    error.value = exc instanceof ApiError ? `${exc.code}: ${exc.message}` : String(exc)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void refresh()
  timer = window.setInterval(refresh, 2500)
})
onBeforeUnmount(() => timer && window.clearInterval(timer))
</script>

<template>
  <section class="dashboard-grid">
    <div class="hero-panel panel">
      <div class="eyebrow">LOCAL · BUSINESS AGENT</div>
      <h1>{{ mode === 'task' ? '创建可审计业务任务' : '和同一个 Agent 正常对话' }}</h1>
      <p v-if="mode === 'task'">任务走 Evidence → Claims → Result，可用于诊断、统计、图片和设备分析。</p>
      <p v-else>对话仍走同一个 OpenClaw / Skill / Tool Runtime，但普通问答不强制必须产生 Evidence。</p>

      <div class="mode-switch">
        <button :class="{ active: mode === 'task' }" @click="mode = 'task'">任务</button>
        <button :class="{ active: mode === 'conversation' }" @click="mode = 'conversation'">对话</button>
      </div>

      <form class="task-compose" @submit.prevent="createEntry">
        <textarea
          v-model="message"
          rows="5"
          :placeholder="mode === 'task'
            ? '例如：检查过去30分钟编码器是否存在丢数、毛刺、回退或不稳定。'
            : '例如：现在支持哪些 Skill？解释一下乳头识别率是怎么计算的。'"
        ></textarea>
        <div class="compose-footer">
          <span>同一 Runtime · 单个主要执行槽位</span>
          <button class="primary-button" :disabled="loading || !message.trim()">
            {{ loading ? '创建中…' : mode === 'task' ? '开始任务' : '发送' }}
          </button>
        </div>
      </form>
      <p v-if="error" class="error-banner">{{ error }}</p>
    </div>

    <aside class="panel task-history">
      <div class="section-heading">
        <div>
          <div class="eyebrow">RUN HISTORY</div>
          <h2>执行记录</h2>
        </div>
        <button class="ghost-button" @click="refresh">刷新</button>
      </div>
      <div v-if="!tasks.length" class="empty-state">还没有执行记录。</div>
      <button
        v-for="task in tasks"
        :key="task.id"
        class="task-row"
        @click="router.push(`/tasks/${task.id}`)"
      >
        <div class="task-row-top">
          <span class="state-pill" :data-state="task.state">{{ task.state }}</span>
          <span class="mode-badge">{{ task.mode === 'conversation' ? '对话' : task.trigger_type === 'schedule' ? '定时任务' : '任务' }}</span>
        </div>
        <strong>{{ task.user_request || task.id }}</strong>
        <small>{{ fmt(task.started_at || task.created_at) }}<template v-if="duration(task)"> · {{ duration(task) }}</template></small>
      </button>
    </aside>
  </section>
</template>
