<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { api, ApiError } from '../api'
import type { ScheduleSnapshot } from '../types'

const schedules = ref<ScheduleSnapshot[]>([])
const name = ref('')
const message = ref('')
const kind = ref<'interval' | 'daily' | 'once'>('interval')
const intervalMinutes = ref(30)
const dailyTime = ref('08:00')
const runAt = ref('')
const busy = ref(false)
const error = ref('')
let timer: number | undefined

function fmt(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function ruleText(row: ScheduleSnapshot) {
  if (row.kind === 'interval') return `每 ${row.interval_minutes} 分钟`
  if (row.kind === 'daily') return `每天 ${row.daily_time}`
  return `一次 · ${fmt(row.run_at)}`
}

async function refresh() {
  try {
    schedules.value = (await api.listSchedules()).schedules
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc)
  }
}

async function createSchedule() {
  if (!name.value.trim() || !message.value.trim() || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const payload: Parameters<typeof api.createSchedule>[0] = {
      name: name.value.trim(), message: message.value.trim(), kind: kind.value,
    }
    if (kind.value === 'interval') payload.interval_minutes = intervalMinutes.value
    if (kind.value === 'daily') payload.daily_time = dailyTime.value
    if (kind.value === 'once') payload.run_at = runAt.value
    await api.createSchedule(payload)
    name.value = ''
    message.value = ''
    await refresh()
  } catch (exc) {
    error.value = exc instanceof ApiError ? `${exc.code}: ${exc.message}` : String(exc)
  } finally {
    busy.value = false
  }
}

async function toggle(row: ScheduleSnapshot) {
  await api.setScheduleEnabled(row.id, !row.enabled)
  await refresh()
}

async function runNow(row: ScheduleSnapshot) {
  try {
    await api.runScheduleNow(row.id)
    await refresh()
  } catch (exc) {
    error.value = exc instanceof ApiError ? `${exc.code}: ${exc.message}` : String(exc)
  }
}

async function remove(row: ScheduleSnapshot) {
  if (!window.confirm(`删除定时任务“${row.name}”？历史 Run 不会因此删除。`)) return
  await api.deleteSchedule(row.id)
  await refresh()
}

onMounted(() => {
  void refresh()
  timer = window.setInterval(refresh, 5000)
})
onBeforeUnmount(() => timer && window.clearInterval(timer))
</script>

<template>
  <section class="schedule-page">
    <div class="panel schedule-compose-panel">
      <div class="eyebrow">SCHEDULE</div>
      <h1>定时任务</h1>
      <p class="muted">到点只触发普通业务 Task；设备断电或 ScopeX 未运行期间错过的历史时间点直接跳过，不补跑。</p>

      <form class="schedule-form" @submit.prevent="createSchedule">
        <label><span>任务名称</span><input v-model="name" placeholder="例如：编码器30分钟检查" /></label>
        <label class="wide-field">
          <span>任务内容</span>
          <textarea v-model="message" rows="4" placeholder="例如：检查过去30分钟编码器是否存在丢数、毛刺、回退或不稳定。"></textarea>
        </label>
        <label>
          <span>触发方式</span>
          <select v-model="kind">
            <option value="interval">每 N 分钟</option>
            <option value="daily">每天固定时间</option>
            <option value="once">一次执行</option>
          </select>
        </label>
        <label v-if="kind === 'interval'"><span>间隔（分钟）</span><input v-model.number="intervalMinutes" type="number" min="1" max="10080" /></label>
        <label v-else-if="kind === 'daily'"><span>每天时间</span><input v-model="dailyTime" type="time" /></label>
        <label v-else><span>执行时间</span><input v-model="runAt" type="datetime-local" /></label>
        <div class="schedule-submit">
          <button class="primary-button" :disabled="busy || !name.trim() || !message.trim()">{{ busy ? '保存中…' : '保存定时任务' }}</button>
        </div>
      </form>
      <p v-if="error" class="error-banner">{{ error }}</p>
    </div>

    <div class="panel">
      <div class="section-heading">
        <div><div class="eyebrow">CONFIGURED</div><h2>已配置任务</h2></div>
        <button class="ghost-button" @click="refresh">刷新</button>
      </div>
      <div v-if="!schedules.length" class="empty-state">还没有定时任务。</div>
      <div v-for="row in schedules" :key="row.id" class="schedule-row">
        <div class="schedule-main">
          <div class="schedule-title-line">
            <RouterLink :to="`/schedules/${row.id}/history`"><strong>{{ row.name }}</strong></RouterLink>
            <span class="state-pill" :data-state="row.enabled ? 'RUNNING' : 'PAUSED'">{{ row.enabled ? '启用' : '停用' }}</span>
          </div>
          <p>{{ row.message }}</p>
          <div class="schedule-meta">
            <span>{{ ruleText(row) }}</span>
            <span>上次：{{ fmt(row.last_run_at) }} · {{ row.last_status || '—' }}</span>
            <span>下次：{{ fmt(row.next_run_at) }}</span>
            <span v-if="row.missed_count">离线跳过：{{ row.missed_count }} 次 · 最近 {{ fmt(row.last_missed_at) }}</span>
          </div>
        </div>
        <div class="schedule-actions">
          <RouterLink class="ghost-button" :to="`/schedules/${row.id}/history`">执行历史</RouterLink>
          <button class="ghost-button" @click="runNow(row)">立即执行</button>
          <button class="ghost-button" @click="toggle(row)">{{ row.enabled ? '停用' : '启用' }}</button>
          <button class="danger-button" @click="remove(row)">删除</button>
        </div>
      </div>
    </div>
  </section>
</template>
