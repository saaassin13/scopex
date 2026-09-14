<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { api, ApiError } from '../api'
import type { EvidenceItem, ProductAnswer, ProgressEvent, ResultResponse, TaskSnapshot } from '../types'

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
const answer = computed<ProductAnswer | null>(() => {
  const value = result.value?.result?.answer
  return value && typeof value === 'object' ? value as ProductAnswer : null
})

const lastTaskFailed = computed(() => {
  for (let index = events.value.length - 1; index >= 0; index -= 1) {
    if (events.value[index]?.type === 'TASK_FAILED') return events.value[index]
  }
  return null
})

const resultProblem = computed(() => {
  if (!isTerminal.value || answer.value || result.value?.rendered) return ''
  const details: string[] = []
  const payload = result.value?.result
  if (payload) {
    const parseError = payload.parse_error
    if (typeof parseError === 'string' && parseError) details.push(`parse_error: ${parseError}`)
    const errors = payload.errors
    if (Array.isArray(errors)) {
      for (const item of errors) if (typeof item === 'string' && item) details.push(item)
    }
  }
  const failedReason = lastTaskFailed.value?.data?.reason
  if (typeof failedReason === 'string' && failedReason) details.push(`runtime: ${failedReason}`)
  if (details.length) return [...new Set(details)].join('\n')
  return result.value?.available
    ? '结果记录已经生成，但没有可展示的产品结果。请检查 answer.json / claims.json。'
    : '当前终态没有 result.json。请检查 TASK_FAILED、worker-error.json 或 investigation-error.json。'
})

function dataString(event: ProgressEvent, key: string): string {
  const value = event.data?.[key]
  return typeof value === 'string' ? value : ''
}

function eventTitle(event: ProgressEvent): string {
  const tool = dataString(event, 'tool')
  if (event.type === 'PROGRESS_UPDATE') return '调查计划'
  if (event.type === 'MODEL_REQUEST') return '模型处理中'
  if (event.type === 'TOOL_CALL') {
    if (tool === 'exec') return '执行命令'
    if (tool === 'read') return '读取文件'
    if (tool === 'view_image') return '查看图片'
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
  if (event.type === 'TOOL_CALL') return dataString(event, 'title') || dataString(event, 'target')
  if (event.type === 'TOOL_RESULT') return dataString(event, 'preview') || `${String(data.result_chars ?? 0)} chars`
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
        <section class="panel result-first-panel">
          <div class="section-heading">
            <div>
              <div class="eyebrow">RESULT</div>
              <h2>诊断结果</h2>
            </div>
            <span v-if="answer" class="trust-badge">Validated Claims</span>
          </div>

          <template v-if="answer">
            <div class="result-section result-conclusion">
              <h3>结论</h3>
              <p v-if="!answer.conclusion.length" class="muted">暂无可发布结论。</p>
              <article v-for="item in answer.conclusion" :key="item.claim_ids.join('-')" class="answer-item primary-answer">
                <p>{{ item.text }}</p>
                <span>{{ item.claim_ids.join(' · ') }}</span>
              </article>
            </div>

            <div class="result-section">
              <h3>说明</h3>
              <p v-if="!answer.explanation.length" class="muted">没有额外说明。</p>
              <article v-for="item in answer.explanation" :key="`ex-${item.claim_ids.join('-')}`" class="answer-item">
                <p>{{ item.text }}</p>
                <span>{{ item.claim_ids.join(' · ') }}</span>
              </article>
            </div>

            <div class="result-grid">
              <div class="result-section">
                <h3>执行情况</h3>
                <p v-if="!answer.execution.length" class="muted">当前 Validated Claims 未形成可验证的执行/状态结论。</p>
                <article v-for="item in answer.execution" :key="`run-${item.claim_ids.join('-')}`" class="answer-item">
                  <p>{{ item.text }}</p>
                  <span>{{ item.claim_ids.join(' · ') }}</span>
                </article>
              </div>

              <div class="result-section">
                <h3>建议</h3>
                <p v-if="!answer.recommendations.length" class="muted">当前没有基于未证实项生成的额外建议。</p>
                <article v-for="item in answer.recommendations" :key="`rec-${item.claim_ids.join('-')}`" class="answer-item">
                  <p>{{ item.text }}</p>
                  <span>{{ item.claim_ids.join(' · ') }}</span>
                </article>
              </div>
            </div>

            <details v-if="result?.rendered" class="trust-fallback">
              <summary>查看可信渲染 fallback</summary>
              <pre class="result-text">{{ result.rendered }}</pre>
            </details>
          </template>

          <pre v-else-if="result?.available && result.rendered" class="result-text">{{ result.rendered }}</pre>
          <div v-else-if="isTerminal" class="empty-state">
            <strong>{{ task?.state === 'FAILED' ? '任务失败，未生成可展示诊断结果。' : '任务已结束，但没有可展示诊断结果。' }}</strong>
            <pre v-if="resultProblem" class="result-text">{{ resultProblem }}</pre>
          </div>
          <div v-else class="empty-state">调查完成并通过证据校准后显示最终结果。</div>
        </section>

        <section class="panel">
          <details class="secondary-details" :open="!isTerminal">
            <summary class="details-heading">
              <span>
                <span class="eyebrow">PROGRESS</span>
                <strong>调查进度</strong>
              </span>
              <span class="muted">{{ events.length }} events</span>
            </summary>
            <div class="timeline">
              <div v-if="!events.length" class="empty-state">等待 Runtime 事件…</div>
              <div v-for="event in events" :key="event.seq" class="timeline-row" :data-kind="event.type">
                <span class="timeline-index">{{ event.seq }}</span>
                <div class="timeline-content">
                  <strong>{{ eventTitle(event) }}</strong>
                  <pre v-if="event.type === 'TOOL_RESULT' && dataString(event, 'preview')" class="timeline-result">{{ dataString(event, 'preview') }}</pre>
                  <p v-else-if="eventSummary(event)">{{ eventSummary(event) }}</p>
                </div>
              </div>
            </div>
          </details>
        </section>
      </div>

      <aside class="side-column">
        <section class="panel control-panel">
          <div class="eyebrow">CONTROL</div>
          <h2>任务控制</h2>
          <textarea v-model="instruction" rows="4" placeholder="追加方向，例如：优先检查 system.log，不要继续排查机器人。"></textarea>
          <div class="control-actions">
            <button v-if="isRunning" class="secondary-button" :disabled="actionBusy" @click="perform('steer')">Steer</button>
            <button v-if="isRunning" class="danger-button" :disabled="actionBusy" @click="perform('stop')">Stop</button>
            <button v-if="isPaused" class="primary-button" :disabled="actionBusy" @click="perform('resume')">Resume</button>
          </div>
          <p class="muted">Stop 在安全模型请求边界生效；已完成工具结果会保留。</p>
        </section>

        <section class="panel evidence-panel compact-evidence">
          <details>
            <summary class="details-heading">
              <span>
                <span class="eyebrow">EVIDENCE</span>
                <strong>相关证据</strong>
              </span>
              <span class="muted">{{ evidence.length }}</span>
            </summary>
            <div v-if="!evidence.length" class="empty-state">暂未形成 Evidence。</div>
            <article v-for="item in evidence" :key="item.ref" class="evidence-card">
              <div class="evidence-meta">
                <strong>{{ item.ref }}</strong>
                <span>{{ item.source }}<template v-if="item.metadata?.line_number">:L{{ item.metadata.line_number }}</template></span>
              </div>
              <code>{{ item.raw }}</code>
            </article>
          </details>
        </section>
      </aside>
    </div>
  </section>
</template>
