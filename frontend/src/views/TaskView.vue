<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { api, ApiError } from '../api'
import type {
  EvidenceItem,
  ProductAnswerItem,
  ProgressEvent,
  ResultResponse,
  TaskSnapshot,
} from '../types'

interface ProgressStep {
  step: string
  status: 'pending' | 'in_progress' | 'completed'
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
const productAnswer = computed(() => result.value?.product_answer ?? null)
const composerFallback = computed(() => (
  result.value?.available
  && Boolean(result.value?.rendered)
  && result.value?.answer_composer?.valid === false
))
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

function claimBadge(item: ProductAnswerItem): string {
  if (item.kind === 'fact') return '已验证'
  if (item.relation === 'temporal_association') return '时间关联'
  if (item.relation === 'causal_hypothesis') return '待验证假设'
  return '尚未确定'
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
        <section class="panel result-panel">
          <div class="section-heading result-heading">
            <div>
              <div class="eyebrow">RESULT</div>
              <h2>诊断结果</h2>
            </div>
            <span v-if="productAnswer" class="trust-state">Validated Claims</span>
          </div>

          <template v-if="productAnswer">
            <section class="answer-section conclusion-section">
              <div class="answer-label">结论</div>
              <article v-for="item in productAnswer.conclusion" :key="item.claim_id" class="answer-item primary-answer">
                <p>{{ item.text }}</p>
                <div class="answer-meta">
                  <span class="claim-badge" :data-kind="item.kind">{{ claimBadge(item) }}</span>
                  <span>{{ item.claim_id }}</span>
                  <span v-for="ref in item.evidence_refs" :key="ref" class="evidence-ref">{{ ref }}</span>
                </div>
              </article>
            </section>

            <section class="answer-section">
              <div class="answer-label">说明</div>
              <div v-if="productAnswer.explanation.length" class="answer-list">
                <article v-for="item in productAnswer.explanation" :key="item.claim_id" class="answer-item">
                  <p>{{ item.text }}</p>
                  <div class="answer-meta">
                    <span class="claim-badge" :data-kind="item.kind">{{ claimBadge(item) }}</span>
                    <span>{{ item.claim_id }}</span>
                    <span v-for="ref in item.evidence_refs" :key="ref" class="evidence-ref">{{ ref }}</span>
                  </div>
                </article>
              </div>
              <p v-else class="answer-empty">暂无额外说明。</p>
            </section>

            <div class="answer-two-column">
              <section class="answer-section compact-section">
                <div class="answer-label">执行情况</div>
                <div v-if="productAnswer.execution.length" class="answer-list">
                  <article v-for="item in productAnswer.execution" :key="item.claim_id" class="answer-item compact-answer">
                    <p>{{ item.text }}</p>
                    <div class="answer-meta">
                      <span class="claim-badge" data-kind="fact">已验证</span>
                      <span v-for="ref in item.evidence_refs" :key="ref" class="evidence-ref">{{ ref }}</span>
                    </div>
                  </article>
                </div>
                <p v-else class="answer-empty">本次结果中没有可发布的执行状态。</p>
              </section>

              <section class="answer-section compact-section">
                <div class="answer-label">建议</div>
                <div v-if="productAnswer.recommendation.length" class="answer-list">
                  <article v-for="item in productAnswer.recommendation" :key="item.claim_id" class="answer-item compact-answer recommendation-answer">
                    <p><strong>优先继续验证：</strong>{{ item.text }}</p>
                    <div class="answer-meta">
                      <span class="claim-badge" :data-kind="item.kind">{{ claimBadge(item) }}</span>
                      <span v-for="ref in item.evidence_refs" :key="ref" class="evidence-ref">{{ ref }}</span>
                    </div>
                  </article>
                </div>
                <p v-else class="answer-empty">暂无额外验证建议。</p>
              </section>
            </div>

            <details class="support-details evidence-details-main">
              <summary>
                <span>相关证据</span>
                <span class="muted">{{ evidence.length }} 条</span>
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

            <details v-if="result?.rendered" class="support-details audit-details">
              <summary>查看 deterministic audit fallback</summary>
              <pre class="result-text">{{ result.rendered }}</pre>
            </details>
          </template>

          <template v-else-if="result?.available && result.rendered">
            <p v-if="composerFallback" class="fallback-note">
              结构化产品答案未通过约束校验，当前显示可信的 deterministic fallback。
            </p>
            <pre class="result-text">{{ result.rendered }}</pre>
            <details class="support-details evidence-details-main">
              <summary>
                <span>相关证据</span>
                <span class="muted">{{ evidence.length }} 条</span>
              </summary>
              <article v-for="item in evidence" :key="item.ref" class="evidence-card">
                <div class="evidence-meta">
                  <strong>{{ item.ref }}</strong>
                  <span>{{ item.source }}<template v-if="item.metadata?.line_number">:L{{ item.metadata.line_number }}</template></span>
                </div>
                <code>{{ item.raw }}</code>
              </article>
            </details>
          </template>

          <div v-else-if="isTerminal" class="empty-state">
            <strong>{{ task?.state === 'FAILED' ? '任务失败，未生成可展示诊断结果。' : '任务已结束，但没有可展示诊断结果。' }}</strong>
            <pre v-if="resultProblem" class="result-text">{{ resultProblem }}</pre>
          </div>
          <div v-else class="result-pending">
            <span class="result-pulse"></span>
            <div>
              <strong>正在调查与验证</strong>
              <p>最终结果会在 Evidence 校准和可信输出完成后显示；下方可查看实时进度。</p>
            </div>
          </div>
        </section>

        <section class="panel progress-panel">
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

        <section class="panel trust-panel">
          <div class="eyebrow">TRUST</div>
          <h2>可信输出</h2>
          <p class="muted">最终展示只来自 Validated Claims。Evidence 和 deterministic renderer 保留为可追溯审计层。</p>
          <div class="trust-stat">
            <span>Evidence</span>
            <strong>{{ evidence.length }}</strong>
          </div>
          <div class="trust-stat">
            <span>Composer</span>
            <strong>{{ productAnswer ? 'PASS' : (composerFallback ? 'FALLBACK' : '—') }}</strong>
          </div>
        </section>
      </aside>
    </div>
  </section>
</template>
