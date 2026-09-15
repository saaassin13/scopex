<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError } from '../api'
import type { TaskCalendarDay, TaskSnapshot } from '../types'

const router = useRouter()
const tasks = ref<TaskSnapshot[]>([])
const calendarDays = ref<TaskCalendarDay[]>([])
const message = ref('')
const loading = ref(false)
const deletingId = ref('')
const error = ref('')
const now = new Date()
const selectedMonth = ref(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`)
const selectedDay = ref(`${selectedMonth.value}-${String(now.getDate()).padStart(2, '0')}`)
let timer: number | undefined

function fmt(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function duration(task: TaskSnapshot) {
  if (typeof task.duration_ms !== 'number') return ''
  const sec = Math.max(0, Math.round(task.duration_ms / 1000))
  if (sec < 60) return `${sec}s`
  return `${Math.floor(sec / 60)}m ${sec % 60}s`
}

function canDelete(task: TaskSnapshot) {
  return ['COMPLETED', 'FAILED', 'CANCELLED'].includes(task.state)
}

const monthDate = computed(() => {
  const [year, month] = selectedMonth.value.split('-').map(Number)
  return new Date(year, month - 1, 1)
})
const monthTitle = computed(() => `${monthDate.value.getFullYear()}年${monthDate.value.getMonth() + 1}月`)
const dayStats = computed(() => new Map(calendarDays.value.map(row => [row.date, row])))
const calendarCells = computed(() => {
  const base = monthDate.value
  const year = base.getFullYear()
  const month = base.getMonth()
  const days = new Date(year, month + 1, 0).getDate()
  const first = (new Date(year, month, 1).getDay() + 6) % 7
  const cells: Array<{ day: number | null; date?: string; stats?: TaskCalendarDay }> = []
  for (let i = 0; i < first; i += 1) cells.push({ day: null })
  for (let day = 1; day <= days; day += 1) {
    const date = `${selectedMonth.value}-${String(day).padStart(2, '0')}`
    cells.push({ day, date, stats: dayStats.value.get(date) })
  }
  return cells
})

function shiftMonth(delta: number) {
  const base = monthDate.value
  const next = new Date(base.getFullYear(), base.getMonth() + delta, 1)
  selectedMonth.value = `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, '0')}`
  selectedDay.value = `${selectedMonth.value}-01`
  void refresh()
}

async function refresh() {
  try {
    const [calendar, rows] = await Promise.all([
      api.getTaskCalendar(selectedMonth.value),
      api.listTasks({ day: selectedDay.value }),
    ])
    calendarDays.value = calendar.days
    tasks.value = rows.tasks
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc)
  }
}

async function selectDay(date?: string) {
  if (!date) return
  selectedDay.value = date
  try {
    tasks.value = (await api.listTasks({ day: date })).tasks
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
    const task = await api.createRun(value)
    message.value = ''
    await router.push(`/tasks/${task.id}`)
  } catch (exc) {
    error.value = exc instanceof ApiError ? `${exc.code}: ${exc.message}` : String(exc)
  } finally {
    loading.value = false
  }
}

async function deleteEntry(task: TaskSnapshot) {
  if (!canDelete(task) || deletingId.value) return
  const ok = window.confirm('删除后会移除此 Run 的结果、事实依据、技术记录、临时工作目录和已导出的复盘包。原始日志/图片/JSON/点云不会删除。继续吗？')
  if (!ok) return
  deletingId.value = task.id
  error.value = ''
  try {
    await api.deleteTask(task.id)
    await refresh()
  } catch (exc) {
    error.value = exc instanceof ApiError ? `${exc.code}: ${exc.message}` : String(exc)
  } finally {
    deletingId.value = ''
  }
}

onMounted(() => {
  void refresh()
  timer = window.setInterval(refresh, 10000)
})
onBeforeUnmount(() => timer && window.clearInterval(timer))
</script>

<template>
  <section class="dashboard-page">
    <div class="hero-panel panel unified-agent-panel">
      <div class="eyebrow">LOCAL · BUSINESS AGENT</div>
      <h1>告诉 Agent 你要了解或处理什么</h1>
      <p>直接输入问题或分析目标。Agent 会使用可用的业务能力完成调查，并保留结果、来源和执行记录。</p>

      <form class="task-compose" @submit.prevent="createEntry">
        <textarea
          v-model="message"
          rows="4"
          placeholder="例如：检查3点的编码器数据是否存在异常；分析7点乳头识别率；当前支持哪些能力？"
        ></textarea>
        <div class="compose-footer">
          <span>统一入口 · 用户无需选择任务类型</span>
          <button class="primary-button" :disabled="loading || !message.trim()">
            {{ loading ? '处理中…' : '发送' }}
          </button>
        </div>
      </form>
      <p v-if="error" class="error-banner">{{ error }}</p>
    </div>

    <div class="history-calendar-layout">
      <section class="panel calendar-panel">
        <div class="section-heading">
          <div>
            <div class="eyebrow">RUN CALENDAR</div>
            <h2>执行日历</h2>
          </div>
          <div class="calendar-nav">
            <button class="ghost-button" @click="shiftMonth(-1)">←</button>
            <strong>{{ monthTitle }}</strong>
            <button class="ghost-button" @click="shiftMonth(1)">→</button>
          </div>
        </div>
        <div class="calendar-weekdays">
          <span v-for="name in ['一','二','三','四','五','六','日']" :key="name">周{{ name }}</span>
        </div>
        <div class="calendar-grid">
          <button
            v-for="(cell, index) in calendarCells"
            :key="cell.date || `blank-${index}`"
            class="calendar-day"
            :class="{ blank: !cell.date, selected: cell.date === selectedDay, failed: (cell.stats?.failed || 0) > 0 }"
            :disabled="!cell.date"
            @click="selectDay(cell.date)"
          >
            <span class="calendar-day-number">{{ cell.day || '' }}</span>
            <template v-if="cell.stats">
              <strong>{{ cell.stats.count }} 次</strong>
              <small>
                <template v-if="cell.stats.failed">{{ cell.stats.failed }} 失败</template>
                <template v-else-if="cell.stats.running">{{ cell.stats.running }} 运行中</template>
                <template v-else>{{ cell.stats.completed }} 完成</template>
              </small>
            </template>
          </button>
        </div>
      </section>

      <aside class="panel day-history">
        <div class="section-heading">
          <div>
            <div class="eyebrow">RUNS · {{ selectedDay }}</div>
            <h2>当天执行记录</h2>
          </div>
          <button class="ghost-button" @click="refresh">刷新</button>
        </div>
        <div v-if="!tasks.length" class="empty-state">当天没有执行记录。</div>
        <div v-for="task in tasks" :key="task.id" class="task-row task-row-with-action" @click="router.push(`/tasks/${task.id}`)">
          <div class="task-row-top">
            <span class="state-pill" :data-state="task.state">{{ task.state }}</span>
            <span class="mode-badge">{{ task.trigger_type === 'schedule' ? '定时' : '手动' }}</span>
            <button
              v-if="canDelete(task)"
              class="task-delete-button"
              :disabled="deletingId === task.id"
              title="删除执行记录"
              @click.stop="deleteEntry(task)"
            >{{ deletingId === task.id ? '…' : '删除' }}</button>
          </div>
          <strong>{{ task.user_request || task.id }}</strong>
          <small>{{ fmt(task.started_at || task.created_at) }}<template v-if="duration(task)"> · {{ duration(task) }}</template></small>
        </div>
      </aside>
    </div>
  </section>
</template>
