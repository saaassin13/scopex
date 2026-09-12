<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { api, ApiError } from '../api'
import type { EvidenceItem, ProgressEvent, ResultResponse, TaskSnapshot } from '../types'

const route = useRoute()
const taskId = computed(() => String(route.params.id))
const task = ref<TaskSnapshot | null>(null)
const events = ref<ProgressEvent[]>([])
const evidence = ref<EvidenceItem[]>([])
const result = ref<ResultResponse | null>(null)
const cursor = ref(0)
const instruction = ref('')
const error = ref('')
const actionBusy = ref(false)
let timer: number | undefined

const isRunning = computed(() => task.value?.state === 'RUNNING')
const isPaused = computed(() => task.value?.state === 'PAUSED')
const isTerminal = computed(() => ['COMPLETED', 'FAILED', 'CANCELLED'].includes(task.value?.state ?? ''))

function eventSummary(event: ProgressEvent): string {
  const data = event.data ?? {}
  if (event.type === 'TOOL_CALL') return `${String(data.tool ?? 'tool')} ${String(data.target ?? '')}`.trim()
  if (event.type === 'TOOL_RESULT') return `${String(data.tool ?? 'tool')} 完成`
  if (event.type === 'EVIDENCE_ADDED') return `${String(data.ref ?? '')} · ${String(data.source ?? '')}`
  if (event.type === 'MODEL_REQUEST') return `模型请求 #${String(data.request_index ?? '')}`
  if (event.type === 'TASK_FAILED') return String(data.reason ?? '任务失败')
  return event.type.replaceAll('_', ' ').toLowerCase()
}

async function refresh() {
  try {
    task.value = await api.getTask(taskId.value)
    const eventResponse = await api.getEvents(taskId.value, cursor.value)
    if (eventResponse.events.length) {
      events.value.push(...eventResponse.events)
      cursor.value = eventResponse.next_after
    }
    evidence.value = (await api.getEvidence(taskId.value)).items
    result.value = await api.getResult(taskId.value)
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc)
  }
}

async function perform(kind: 'stop' | 'resume' | 'steer') {
  if (actionBusy.value) return
  actionBusy.value = true
  error.value = ''
  try {
    if (kind === 'stop') {
      task.value = await api.stop(taskId.value, instruction.value.trim())
    } else {
      const value = instruction.value.trim()
      if (!value) throw new Error('请输入指令')
      task.value = kind === 'resume'
        ? await api.resume(taskId.value, value)
        : await api.steer(taskId.value, value)
    }
    instruction.value = ''
    await refresh()
  } catch (exc) {
    error.value = exc instanceof ApiError ? `${exc.code}: ${exc.message}` : String(exc)
  } finally {
    actionBusy.value = false
  }
}

onMounted(() => {
  void refresh()
  timer = window.setInterval(refresh, 1500)
})
onBeforeUnmount(() => timer && window.clearInterval(timer))
</script>

<template>
  <section class="task-page">
    <div class="task-header panel">
      <div>
        <RouterLink class="back-link" to="/">← 返回任务列表</RouterLink>
        <div class="eyebrow">TASK · {{ taskId }}</div>
        <h1>{{ task?.user_request || '加载任务…' }}</h1>
      </div>
      <span class="state-pill large" :data-state="task?.state">{{ task?.state || 'LOADING' }}</span>
    </div>

    <p v-if="error" class="error-banner">{{ error }}</p>

    <div class="task-layout">
      <div class="main-column">
        <section class="panel">
          <div class="section-heading">
            <div>
              <div class="eyebrow">PROGRESS</div>
              <h2>调查进度</h2>
            </div>
            <span class="muted">{{ events.length }} events</span>
          </div>
          <div class="timeline">
            <div v-if="!events.length" class="empty-state">等待 Runtime 事件…</div>
            <div v-for="event in events" :key="event.seq" class="timeline-row">
              <span class="timeline-index">{{ event.seq }}</span>
              <div>
                <strong>{{ event.type }}</strong>
                <p>{{ eventSummary(event) }}</p>
              </div>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="section-heading">
            <div>
              <div class="eyebrow">RESULT</div>
              <h2>诊断结果</h2>
            </div>
          </div>
          <pre v-if="result?.available && result.rendered" class="result-text">{{ result.rendered }}</pre>
          <div v-else class="empty-state">
            {{ isTerminal ? '任务已结束，但当前没有可展示结果。' : '调查完成并通过证据校准后显示最终结果。' }}
          </div>
        </section>
      </div>

      <aside class="side-column">
        <section class="panel control-panel">
          <div class="eyebrow">CONTROL</div>
          <h2>任务控制</h2>
          <textarea
            v-model="instruction"
            rows="4"
            placeholder="追加方向，例如：优先检查 system.log，不要继续排查机器人。"
          ></textarea>
          <div class="control-actions">
            <button v-if="isRunning" class="secondary-button" :disabled="actionBusy" @click="perform('steer')">Steer</button>
            <button v-if="isRunning" class="danger-button" :disabled="actionBusy" @click="perform('stop')">Stop</button>
            <button v-if="isPaused" class="primary-button" :disabled="actionBusy" @click="perform('resume')">Resume</button>
          </div>
          <p class="muted">Stop 在安全模型请求边界生效；已完成工具结果会保留。</p>
        </section>

        <section class="panel evidence-panel">
          <div class="section-heading">
            <div>
              <div class="eyebrow">EVIDENCE</div>
              <h2>证据</h2>
            </div>
            <span class="muted">{{ evidence.length }}</span>
          </div>
          <div v-if="!evidence.length" class="empty-state">暂未形成 Evidence。</div>
          <article v-for="item in evidence" :key="item.ref" class="evidence-card">
            <div class="evidence-meta">
              <strong>{{ item.ref }}</strong>
              <span>{{ item.source }}<template v-if="item.metadata?.line_number">:L{{ item.metadata.line_number }}</template></span>
            </div>
            <code>{{ item.raw }}</code>
          </article>
        </section>
      </aside>
    </div>
  </section>
</template>
