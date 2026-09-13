<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { api, ApiError } from '../api'
import type { EvidenceItem, ProgressEvent, ResultResponse, TaskSnapshot } from '../types'

interface ProgressStep {
  step: string
  status: 'pending' | 'in_progress' | 'completed'
}

interface EvidenceGroup {
  key: string
  title: string
  source: string
  items: EvidenceItem[]
}

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
const evidenceGroups = computed<EvidenceGroup[]>(() => {
  const groups = new Map<string, EvidenceGroup>()

  for (const item of evidence.value) {
    const kind = metadataString(item, 'evidence_type')
    // Text/command Evidence keeps its claim-grade line identity internally, but
    // the product UI groups rows from the same OpenClaw tool call. Images stay
    // separate because one view_image call may contain several distinct files.
    const key = kind === 'image'
      ? `image:${item.ref}`
      : `${item.tool_call_id || 'source'}:${item.source}`

    let group = groups.get(key)
    if (!group) {
      group = {
        key,
        title: evidenceGroupTitle(item),
        source: item.source,
        items: [],
      }
      groups.set(key, group)
    }
    group.items.push(item)
  }

  return [...groups.values()]
})
const lastTaskFailed = computed(() => {
  for (let index = events.value.length - 1; index >= 0; index -= 1) {
    if (events.value[index]?.type === 'TASK_FAILED') return events.value[index]
  }
  return null
})
const resultProblem = computed(() => {
  if (!isTerminal.value || result.value?.rendered) return ''

  const details: string[] = []
  const payload = result.value?.result
  if (payload) {
    const parseError = payload.parse_error
    if (typeof parseError === 'string' && parseError) {
      details.push(`parse_error: ${parseError}`)
    }
    const errors = payload.errors
    if (Array.isArray(errors)) {
      for (const item of errors) {
        if (typeof item === 'string' && item) details.push(item)
      }
    }
  }

  const failedReason = lastTaskFailed.value?.data?.reason
  if (typeof failedReason === 'string' && failedReason) {
    details.push(`runtime: ${failedReason}`)
  }

  if (details.length) return [...new Set(details)].join('\n')
  if (result.value?.available) {
    return '结果记录已经生成，但没有可渲染的最终文本。请检查 result.json / claims.json。'
  }
  return '当前终态没有 result.json。请检查 TASK_FAILED 事件、worker-error.json 或 investigation-error.json。'
})

function metadataString(item: EvidenceItem, key: string): string {
  const value = item.metadata?.[key]
  return typeof value === 'string' ? value : ''
}

function sourceName(source: string): string {
  const normalized = source.replaceAll('\\', '/')
  const parts = normalized.split('/').filter(Boolean)
  return parts.at(-1) || source
}

function evidenceGroupTitle(item: EvidenceItem): string {
  const title = metadataString(item, 'title')
  if (title) return title

  const kind = metadataString(item, 'evidence_type')
  const tool = metadataString(item, 'tool')
  if (kind === 'image') return `图片 · ${sourceName(item.source)}`
  if (tool === 'read') return `文件 · ${sourceName(item.source)}`
  if (tool === 'exec') {
    const command = metadataString(item, 'command')
    return command ? `命令 · ${command}` : '命令输出'
  }
  return sourceName(item.source)
}

function evidenceRefsLabel(items: EvidenceItem[]): string {
  if (!items.length) return ''
  if (items.length === 1) return items[0].ref
  return `${items[0].ref}–${items[items.length - 1].ref}`
}

function evidenceLineRange(items: EvidenceItem[]): string {
  const lines = items
    .map((item) => item.metadata?.line_number)
    .filter((value): value is number => typeof value === 'number' && value > 0)
  if (!lines.length) return ''
  const first = Math.min(...lines)
  const last = Math.max(...lines)
  return first === last ? `L${first}` : `L${first}–L${last}`
}

function evidenceRaw(group: EvidenceGroup): string {
  return group.items.map((item) => item.raw).join('\n')
}

function dataString(event: ProgressEvent, key: string): string {
  const value = event.data?.[key]
  return typeof value === 'string' ? value : ''
}

function dataStrings(event: ProgressEvent, key: string): string[] {
  const value = event.data?.[key]
  if (!Array.isArray(value)) return []
  return value.filter((item): item is string => typeof item === 'string' && Boolean(item))
}

function progressSteps(event: ProgressEvent): ProgressStep[] {
  const value = event.data?.plan
  if (!Array.isArray(value)) return []
  const steps: ProgressStep[] = []
  for (const item of value) {
    if (!item || typeof item !== 'object') continue
    const row = item as Record<string, unknown>
    if (typeof row.step !== 'string') continue
    if (!['pending', 'in_progress', 'completed'].includes(String(row.status))) continue
    steps.push({ step: row.step, status: row.status as ProgressStep['status'] })
  }
  return steps
}

function progressSymbol(status: ProgressStep['status']): string {
  if (status === 'completed') return '✓'
  if (status === 'in_progress') return '→'
  return '○'
}

function eventTitle(event: ProgressEvent): string {
  const tool = dataString(event, 'tool')
  if (event.type === 'PROGRESS_UPDATE') return '调查计划'
  if (event.type === 'MODEL_REQUEST') return '模型处理中'
  if (event.type === 'TOOL_CALL') {
    if (tool === 'exec') return '执行命令'
    if (tool === 'read') return '读取文件'
    if (tool === 'view_image') return '查看图片'
    if (tool === 'process') return '进程操作'
    return `执行工具 · ${tool || 'unknown'}`
  }
  if (event.type === 'TOOL_RESULT') return `${tool || '工具'} · 返回结果`
  if (event.type === 'EVIDENCE_ADDED') return '新增证据'
  if (event.type === 'FINALIZATION_STARTED') return '正在整理最终结论'
  if (event.type === 'FINALIZATION_COMPLETED') return '最终结论已生成'
  if (event.type === 'TASK_COMPLETED') return '任务完成'
  if (event.type === 'TASK_FAILED') return '任务失败'
  if (event.type === 'TASK_STARTED') return '任务开始'
  if (event.type === 'USER_STEER') return '用户调整调查方向'
  if (event.type === 'USER_STOP') return '用户请求停止'
  if (event.type === 'USER_RESUME') return '继续调查'
  return event.type.replaceAll('_', ' ').toLowerCase()
}

function eventSummary(event: ProgressEvent): string {
  const data = event.data ?? {}
  if (event.type === 'MODEL_REQUEST') return `第 ${String(data.request_index ?? '')} 次模型请求`
  if (event.type === 'EVIDENCE_ADDED') return `${String(data.ref ?? '')} · ${String(data.source ?? '')}`
  if (event.type === 'TASK_FAILED') return String(data.reason ?? '任务失败')
  if (event.type === 'TOOL_CALL') return dataString(event, 'target')
  if (event.type === 'TOOL_RESULT') return `${String(data.result_chars ?? 0)} chars`
  return ''
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
            <div
              v-for="event in events"
              :key="event.seq"
              class="timeline-row"
              :data-kind="event.type"
            >
              <span class="timeline-index">{{ event.seq }}</span>
              <div class="timeline-content">
                <strong>{{ eventTitle(event) }}</strong>

                <template v-if="event.type === 'PROGRESS_UPDATE'">
                  <p v-if="dataString(event, 'markdown')" class="progress-note">
                    {{ dataString(event, 'markdown') }}
                  </p>
                  <div v-if="progressSteps(event).length" class="progress-plan">
                    <div
                      v-for="(step, index) in progressSteps(event)"
                      :key="`${event.seq}-${index}`"
                      class="progress-step"
                      :data-status="step.status"
                    >
                      <span class="progress-symbol">{{ progressSymbol(step.status) }}</span>
                      <span>{{ step.step }}</span>
                    </div>
                  </div>
                </template>

                <template v-else-if="event.type === 'TOOL_CALL'">
                  <p v-if="dataString(event, 'title')" class="tool-intent">
                    {{ dataString(event, 'title') }}
                  </p>
                  <pre v-if="dataString(event, 'command')" class="timeline-code">{{ dataString(event, 'command') }}</pre>
                  <code v-else-if="dataString(event, 'path') || dataString(event, 'file_path')" class="timeline-path">
                    {{ dataString(event, 'path') || dataString(event, 'file_path') }}
                  </code>
                  <div v-if="dataStrings(event, 'paths').length" class="timeline-path-list">
                    <code v-for="path in dataStrings(event, 'paths')" :key="path">{{ path }}</code>
                  </div>
                  <p v-if="dataString(event, 'prompt')" class="tool-prompt">
                    观察要求：{{ dataString(event, 'prompt') }}
                  </p>
                  <p v-if="!dataString(event, 'command') && !dataString(event, 'path') && !dataString(event, 'file_path') && !dataStrings(event, 'paths').length && eventSummary(event)">
                    {{ eventSummary(event) }}
                  </p>
                </template>

                <template v-else-if="event.type === 'TOOL_RESULT'">
                  <pre v-if="dataString(event, 'preview')" class="timeline-result">{{ dataString(event, 'preview') }}</pre>
                  <p v-else>{{ eventSummary(event) }}</p>
                </template>

                <p v-else-if="eventSummary(event)">{{ eventSummary(event) }}</p>
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
          <div v-else-if="isTerminal" class="empty-state">
            <strong>{{ task?.state === 'FAILED' ? '任务失败，未生成可展示诊断结果。' : '任务已结束，但没有可展示诊断结果。' }}</strong>
            <pre v-if="resultProblem" class="result-text">{{ resultProblem }}</pre>
          </div>
          <div v-else class="empty-state">调查完成并通过证据校准后显示最终结果。</div>
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
            <span class="muted">{{ evidenceGroups.length }} 组 · {{ evidence.length }} 条</span>
          </div>
          <div v-if="!evidence.length" class="empty-state">暂未形成 Evidence。</div>
          <article v-for="group in evidenceGroups" :key="group.key" class="evidence-card">
            <div class="evidence-meta">
              <strong>{{ evidenceRefsLabel(group.items) }}</strong>
              <span>{{ group.title }}</span>
            </div>
            <p class="muted">
              {{ group.source }}<template v-if="evidenceLineRange(group.items)"> · {{ evidenceLineRange(group.items) }}</template>
              · {{ group.items.length }} 条
            </p>
            <code>{{ evidenceRaw(group) }}</code>
          </article>
        </section>
      </aside>
    </div>
  </section>
</template>
