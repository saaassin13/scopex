<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { api, ApiError } from '../api'
import type { MonitorEvent, MonitorStatus } from '../types'

const status = ref<MonitorStatus | null>(null)
const events = ref<MonitorEvent[]>([])
const error = ref('')
const unavailable = ref(false)
const busy = ref(false)
const expanded = ref<string | null>(null)
const showAllFrames = ref<Record<string, boolean>>({})
let timer: number | undefined

const STATE_TEXT: Record<string, string> = {
  running: '运行中', stopped: '已停止', backoff: '退避重试中',
  starting: '启动中', error: '异常',
}
const RULE_TEXT: Record<string, string> = {
  zone_foreground: '禁区内持续出现物体',
  cow_fully_outside: '检测到牛在转盘外',
  cow_mostly_off: '牛大部分离开转盘',
  below_platform: '物体位于平台线下方',
}

const stateText = computed(() => STATE_TEXT[status.value?.state ?? 'stopped'] ?? status.value?.state ?? '')
const stale = computed(() => {
  const age = status.value?.frame_age_s
  return typeof age === 'number' && age > 30 ? age : null
})
const calibrationMismatch = computed(() => {
  const s = status.value
  if (!s) return false
  const running = s.running_calibration?.version ?? null
  const active = s.active_calibration?.version ?? null
  return s.state !== 'stopped' && running !== active
})

function fmtTime(epoch: number | null | undefined): string {
  if (!epoch) return '—'
  return new Date(epoch * 1000).toLocaleString('zh-CN', { hour12: false })
}

function fmtAge(seconds: number | null | undefined): string {
  if (seconds == null) return '—'
  if (seconds < 90) return `${Math.round(seconds)} 秒`
  return `${Math.round(seconds / 60)} 分钟`
}

async function refresh() {
  try {
    const [statusResult, eventsResult] = await Promise.all([
      api.monitorStatus(), api.monitorEvents(),
    ])
    status.value = statusResult
    events.value = eventsResult.events
    unavailable.value = false
  } catch (exc) {
    if (exc instanceof ApiError && exc.status === 503) {
      unavailable.value = true
      status.value = null
    } else {
      error.value = exc instanceof Error ? exc.message : String(exc)
    }
  }
}

async function start() { await api.monitorStart(); await refresh() }
async function stop() { await api.monitorStop(); await refresh() }
async function restartWithNewCalibration() {
  busy.value = true
  try {
    await api.monitorStop()
    await api.monitorStart()
    await refresh()
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc)
  } finally {
    busy.value = false
  }
}

async function sendConfirm(eventId: string) {
  try {
    await api.monitorConfirm(eventId)
    await refresh()
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc)
  }
}

function toggleDetail(eventId: string) {
  expanded.value = expanded.value === eventId ? null : eventId
  if (expanded.value === eventId) showAllFrames.value[eventId] = false
}

function frameNames(event: MonitorEvent): string[] {
  const pre = Array.from({ length: event.pre_count }, (_, i) => `pre-${String(i).padStart(5, '0')}.jpg`)
  const post = Array.from({ length: event.post_count }, (_, i) => `post-${String(i).padStart(5, '0')}.jpg`)
  return pre.concat(post)
}

// Events are immutable; one frame every ~5 (fps 2 → ~2.5s per kept frame).
const THIN_STEP = 5

function visibleFrames(event: MonitorEvent): string[] {
  const names = frameNames(event)
  if (showAllFrames.value[event.event_id]) return names
  return names.filter((_, index) => index % THIN_STEP === 0 || index === names.length - 1)
}

onMounted(() => {
  refresh()
  timer = window.setInterval(refresh, 5000)
})
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <section class="page">
    <h2>常驻监控</h2>
    <p class="hint">牛掉落监控持续检测转盘外禁区；事件自动留存前后帧，可发起模型确认。</p>
    <p v-if="error" class="error">{{ error }}</p>

    <div v-if="unavailable" class="empty">
      <p>监控服务未运行。本页面需要 scopex-monitor 容器（edge.env 中 SCOPEX_MONITOR_ENABLED=1）。</p>
    </div>

    <template v-else-if="status">
      <div class="status-card" :data-state="status.state">
        <div class="status-main">
          <span class="state-badge">{{ stateText }}</span>
          <span class="hint">相机 {{ status.camera }} · 信号源 {{ status.source ?? '未配置' }}</span>
          <span v-if="stale != null" class="warn">画面已 {{ fmtAge(stale) }} 无新帧</span>
          <span v-if="status.state === 'backoff' && status.backoff_wait_s" class="hint">
            {{ Math.ceil(status.backoff_wait_s) }} 秒后自动重试
          </span>
        </div>
        <dl class="status-facts">
          <div><dt>当前画面延迟</dt><dd>{{ fmtAge(status.frame_age_s) }}</dd></div>
          <div><dt>本次运行启动于</dt><dd>{{ fmtTime(status.session_started_at) }}</dd></div>
          <div><dt>累计自动重启</dt><dd>{{ status.restart_count }} 次</dd></div>
          <div><dt>运行中标定</dt><dd>v{{ status.running_calibration?.version ?? '—' }}</dd></div>
          <div><dt>最新启用标定</dt><dd>v{{ status.active_calibration?.version ?? '—' }}</dd></div>
          <div><dt>自动确认</dt><dd>{{ status.auto_confirm ? '开' : '关' }}</dd></div>
        </dl>
        <p v-if="status.last_error" class="warn">{{ status.last_error }}</p>
        <div class="nav-buttons">
          <button :disabled="busy || status.state === 'running'" @click="start">启动</button>
          <button :disabled="busy || status.state === 'stopped'" @click="stop">停止</button>
          <button v-if="calibrationMismatch" :disabled="busy" @click="restartWithNewCalibration">
            重启以应用新标定 v{{ status.active_calibration?.version }}
          </button>
        </div>
        <p v-if="calibrationMismatch" class="warn">
          监控正在使用 v{{ status.running_calibration?.version }} 标定，最新启用的是
          v{{ status.active_calibration?.version }}；检测参数在重启后才生效。
        </p>
      </div>

      <h3>事件列表</h3>
      <p v-if="!events.length" class="hint">还没有事件。正常情况下转盘外走道持续无物体，不会产生事件。</p>
      <table v-else>
        <thead>
          <tr><th>触发时间</th><th>状态</th><th>判定依据</th><th>帧数</th><th>确认</th><th></th></tr>
        </thead>
        <tbody>
          <template v-for="event in events" :key="event.event_id">
            <tr :class="{ open: event.status === 'open' }">
              <td>{{ fmtTime(event.opened_at) }}</td>
              <td>{{ event.status === 'open' ? '进行中' : '已结束' }}</td>
              <td>{{ event.rules.map((r) => RULE_TEXT[r] ?? r).join('；') }}</td>
              <td>{{ event.frame_count }}</td>
              <td>
                <RouterLink v-if="event.confirm_task_id" :to="`/tasks/${event.confirm_task_id}`">
                  查看确认任务
                </RouterLink>
                <span v-else-if="event.confirm_error" class="warn">{{ event.confirm_error }}</span>
                <span v-else class="hint">未确认</span>
              </td>
              <td>
                <button v-if="!event.confirm_task_id" class="link" @click="sendConfirm(event.event_id)">发确认</button>
                <button class="link" @click="toggleDetail(event.event_id)">
                  {{ expanded === event.event_id ? '收起帧图' : '查看帧图' }}
                </button>
              </td>
            </tr>
            <tr v-if="expanded === event.event_id" class="frames-row">
              <td colspan="6">
                <div class="frame-grid">
                  <figure v-for="name in visibleFrames(event)" :key="name">
                    <img :src="api.monitorFrameUrl(event.event_id, name)" loading="lazy" :alt="name" />
                    <figcaption>{{ name.startsWith('pre') ? '事发前' : '事发后' }} {{ name.slice(4, 9) }}</figcaption>
                  </figure>
                </div>
                <button v-if="event.frame_count > visibleFrames(event).length"
                        class="link" @click="showAllFrames[event.event_id] = true">
                  加载全部 {{ event.frame_count }} 帧
                </button>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </template>
  </section>
</template>

<style scoped>
.status-card { border: 1px solid var(--panel-border); border-radius: 8px; padding: 16px; margin-bottom: 24px; background: var(--panel); }
.status-card[data-state='running'] { border-color: rgba(46, 158, 91, 0.6); }
.status-card[data-state='error'] { border-color: rgba(255, 90, 104, 0.55); }
.status-main { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
.state-badge { font-weight: 600; padding: 2px 12px; border-radius: 12px; background: rgba(255, 255, 255, 0.07); color: #b6bfce; }
.status-card[data-state='running'] .state-badge { background: rgba(46, 158, 91, 0.16); color: #7de0a5; }
.status-facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; margin: 0 0 12px; }
.status-facts dt { font-size: 12px; color: var(--muted); }
.status-facts dd { margin: 0; font-size: 14px; }
.nav-buttons { display: flex; gap: 8px; }
table { width: 100%; font-size: 13px; border-collapse: collapse; }
th { color: var(--muted); font-weight: 600; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--panel-border); vertical-align: top; }
tr.open td { background: rgba(255, 196, 107, 0.07); }
.frames-row td { background: rgba(255, 255, 255, 0.02); }
.frame-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 8px; margin-bottom: 8px; }
.frame-grid figure { margin: 0; }
.frame-grid img { width: 100%; border-radius: 4px; display: block; }
.frame-grid figcaption { font-size: 11px; color: var(--muted); margin-top: 2px; }
.warn { color: #ffc46b; }
.empty { border: 1px dashed rgba(255, 255, 255, 0.2); border-radius: 6px; padding: 32px; text-align: center; color: var(--muted); }
</style>
