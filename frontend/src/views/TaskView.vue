<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { api, ApiError } from '../api'
import type { Evaluation, EvidenceItem, ProductAnswer, ProgressEvent, ResultResponse, TaskSnapshot } from '../types'

const route = useRoute()
const taskId = computed(() => String(route.params.id))
const task = ref<TaskSnapshot | null>(null)
const events = ref<ProgressEvent[]>([])
const evidence = ref<EvidenceItem[]>([])
const result = ref<ResultResponse | null>(null)
const evaluation = ref<Evaluation | null>(null)
const cursor = ref(0)
const instruction = ref('')
const error = ref('')
const actionBusy = ref(false)
const feedbackOpen = ref(false)
const feedbackRating = ref<'up' | 'down'>('up')
const feedbackTags = ref<string[]>([])
const feedbackNote = ref('')
const feedbackBusy = ref(false)
let timer: number | undefined

const evaluationOptions = [
  ['wrong_result', '结果错误'],
  ['incomplete', '分析不完整'],
  ['scope_too_broad', '调查范围过大'],
  ['too_slow', '耗时过长'],
  ['wrong_skill', 'Skill 使用不正确'],
  ['tool_failed', '工具调用失败'],
  ['hard_to_read', '结果难以理解'],
  ['insufficient_evidence', '证据不足'],
  ['other', '其他'],
] as const

const isRunning = computed(() => task.value?.state === 'RUNNING')
const isPaused = computed(() => task.value?.state === 'PAUSED')
const isTerminal = computed(() => ['COMPLETED', 'FAILED', 'CANCELLED'].includes(task.value?.state ?? ''))
const isConversation = computed(() => task.value?.mode === 'conversation')
const conversationAnswer = computed(() => {
  const value = result.value?.result?.answer_text
  return typeof value === 'string' && value.trim() ? value : null
})
const answer = computed<ProductAnswer | null>(() => {
  const value = result.value?.result?.answer
  return value && typeof value === 'object' ? value as ProductAnswer : null
})
const userFacts = computed(() => evidence.value.filter(item => {
  const type = item.metadata?.evidence_type
  if (type === 'structured_business_facts' || type === 'image') return true
  if (item.source.startsWith('/agent-data/') || item.source.startsWith('/scopex-host/')) return true
  return false
}))

const lastTaskFailed = computed(() => {
  for (let index = events.value.length - 1; index >= 0; index -= 1) {
    if (events.value[index]?.type === 'TASK_FAILED') return events.value[index]
  }
  return null
})

function fmt(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function durationText(ms?: number | null) {
  if (typeof ms !== 'number') return '—'
  const sec = Math.max(0, Math.round(ms / 1000))
  if (sec < 60) return `${sec} 秒`
  const min = Math.floor(sec / 60)
  return `${min} 分 ${sec % 60} 秒`
}

function friendlyReason(reason: string) {
  const labels: Record<string, string> = {
    investigation_turn_incomplete: 'Agent 调查过程未正常结束，因此没有形成可信的最终结果。',
    investigation_completed_without_evidence: '这次业务任务没有形成可审计事实，因此没有发布诊断结论。',
    fresh_structured_finalizer_failed: '调查已经形成事实依据，但最终结构化整理失败。',
    budget_reached_without_evidence: '任务达到运行预算前仍未形成可发布事实。',
    runtime_guard_reached_without_evidence: '运行时安全边界终止了调查，且没有形成可发布事实。',
  }
  return labels[reason] || '任务未正常完成，请查看技术详情。'
}

const resultProblem = computed(() => {
  if (!isTerminal.value || answer.value || conversationAnswer.value || result.value?.rendered) return ''
  const reason = lastTaskFailed.value?.data?.reason
  return typeof reason === 'string' && reason ? friendlyReason(reason) : '任务已结束，但没有形成可展示结果。'
})

const technicalProblem = computed(() => {
  const details: string[] = []
  const payload = result.value?.result
  if (payload) {
    const parseError = payload.parse_error
    if (typeof parseError === 'string' && parseError) details.push(`parse_error: ${parseError}`)
    const errors = payload.errors
    if (Array.isArray(errors)) for (const item of errors) if (typeof item === 'string' && item) details.push(item)
  }
  const failedReason = lastTaskFailed.value?.data?.reason
  if (typeof failedReason === 'string' && failedReason) details.push(`runtime: ${failedReason}`)
  return [...new Set(details)].join('\n')
})

function factText(item: EvidenceItem): string {
  if (item.metadata?.evidence_type !== 'structured_business_facts') return item.raw
  try {
    const value = JSON.parse(item.raw)
    const facts = value?.facts ?? value?.summary
    if (facts && typeof facts === 'object') {
      return Object.entries(facts)
        .filter(([, raw]) => ['string', 'number', 'boolean'].includes(typeof raw) || raw === null)
        .slice(0, 12)
        .map(([key, raw]) => `${key}: ${String(raw)}`)
        .join('\n')
    }
  } catch {
    // Fall back to the exact frozen business-fact payload.
  }
  return item.raw
}

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
  if (event.type === 'EVIDENCE_ADDED') return '新增事实依据'
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
    if (isTerminal.value) {
      evaluation.value = (await api.getEvaluation(taskId.value)).evaluation
      if (evaluation.value && !feedbackOpen.value) {
        feedbackRating.value = evaluation.value.rating
        feedbackTags.value = [...evaluation.value.tags]
        feedbackNote.value = evaluation.value.note
      }
    }
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

function toggleFeedbackTag(tag: string) {
  feedbackTags.value = feedbackTags.value.includes(tag)
    ? feedbackTags.value.filter(value => value !== tag)
    : [...feedbackTags.value, tag]
}

async function saveFeedback() {
  feedbackBusy.value = true
  error.value = ''
  try {
    evaluation.value = await api.setEvaluation(taskId.value, feedbackRating.value, feedbackTags.value, feedbackNote.value)
    feedbackOpen.value = false
  } catch (exc) {
    error.value = exc instanceof ApiError ? `${exc.code}: ${exc.message}` : String(exc)
  } finally {
    feedbackBusy.value = false
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
        <RouterLink class="back-link" to="/">← 返回执行记录</RouterLink>
        <div class="eyebrow">{{ isConversation ? 'CONVERSATION' : 'TASK' }} · {{ taskId }}</div>
        <h1>{{ task?.user_request || '加载任务…' }}</h1>
        <div class="run-meta">
          <span>触发：{{ task?.trigger_type === 'schedule' ? '定时' : '手动' }}</span>
          <span>开始：{{ fmt(task?.started_at) }}</span>
          <span>结束：{{ fmt(task?.finished_at) }}</span>
          <span>耗时：{{ durationText(task?.duration_ms) }}</span>
          <span v-if="task?.scheduled_for">计划：{{ fmt(task.scheduled_for) }}</span>
        </div>
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
              <h2>{{ isConversation ? '回答' : '任务结果' }}</h2>
            </div>
            <span v-if="answer" class="trust-badge">Validated Claims</span>
          </div>

          <div v-if="conversationAnswer" class="conversation-answer">
            <p>{{ conversationAnswer }}</p>
          </div>

          <template v-else-if="answer">
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
                <p v-if="!answer.execution.length" class="muted">本任务没有需要展示的业务执行动作。</p>
                <article v-for="item in answer.execution" :key="`run-${item.claim_ids.join('-')}`" class="answer-item">
                  <p>{{ item.text }}</p>
                </article>
              </div>
              <div class="result-section">
                <h3>建议</h3>
                <p v-if="!answer.recommendations.length" class="muted">当前没有额外待验证建议。</p>
                <article v-for="item in answer.recommendations" :key="`rec-${item.claim_ids.join('-')}`" class="answer-item">
                  <p>{{ item.text }}</p>
                </article>
              </div>
            </div>

            <details v-if="result?.rendered" class="trust-fallback">
              <summary>查看可信渲染 fallback</summary>
              <pre class="result-text">{{ result.rendered }}</pre>
            </details>
          </template>

          <pre v-else-if="result?.available && result.rendered" class="result-text">{{ result.rendered }}</pre>
          <div v-else-if="isTerminal" class="empty-state result-failure">
            <strong>{{ resultProblem }}</strong>
            <details v-if="technicalProblem" class="technical-details">
              <summary>技术详情</summary>
              <pre class="result-text">{{ technicalProblem }}</pre>
            </details>
          </div>
          <div v-else class="empty-state">执行结束后显示结果。</div>
        </section>

        <section v-if="isTerminal" class="panel feedback-panel">
          <div class="section-heading">
            <div>
              <div class="eyebrow">FEEDBACK</div>
              <h2>任务评价与复盘</h2>
            </div>
            <a class="ghost-button export-link" :href="api.exportUrl(taskId)" download>导出复盘包</a>
          </div>
          <div v-if="evaluation && !feedbackOpen" class="saved-feedback">
            <strong>{{ evaluation.rating === 'up' ? '👍 结果正确' : '👎 结果有问题' }}</strong>
            <span v-if="evaluation.tags.length">{{ evaluation.tags.join(' · ') }}</span>
            <p v-if="evaluation.note">{{ evaluation.note }}</p>
            <button class="ghost-button" @click="feedbackOpen = true">修改评价</button>
          </div>
          <div v-else class="feedback-editor">
            <div class="feedback-rating">
              <button :class="{ active: feedbackRating === 'up' }" @click="feedbackRating = 'up'">👍 正确</button>
              <button :class="{ active: feedbackRating === 'down' }" @click="feedbackRating = 'down'">👎 有问题</button>
            </div>
            <div v-if="feedbackRating === 'down'" class="feedback-tags">
              <button
                v-for="option in evaluationOptions"
                :key="option[0]"
                :class="{ active: feedbackTags.includes(option[0]) }"
                @click="toggleFeedbackTag(option[0])"
              >{{ option[1] }}</button>
            </div>
            <textarea v-model="feedbackNote" rows="3" placeholder="补充说明（可选）"></textarea>
            <button class="primary-button" :disabled="feedbackBusy" @click="saveFeedback">{{ feedbackBusy ? '保存中…' : '保存评价' }}</button>
          </div>
        </section>

        <section class="panel">
          <details class="secondary-details" :open="!isTerminal">
            <summary class="details-heading">
              <span><span class="eyebrow">PROGRESS</span><strong>调查进度 / 技术记录</strong></span>
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
          <textarea v-model="instruction" rows="4" placeholder="追加方向，例如：只检查编码器原始值，不要继续检查图片。"></textarea>
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
              <span><span class="eyebrow">FACTS</span><strong>事实依据</strong></span>
              <span class="muted">{{ userFacts.length }}</span>
            </summary>
            <div v-if="!userFacts.length" class="empty-state">{{ isConversation ? '普通问答不要求必须形成事实依据。' : '暂未形成可展示的业务事实依据。' }}</div>
            <article v-for="item in userFacts" :key="item.ref" class="evidence-card">
              <div class="evidence-meta">
                <strong>{{ item.ref }}</strong>
                <span>{{ item.source }}<template v-if="item.metadata?.line_number">:L{{ item.metadata.line_number }}</template></span>
              </div>
              <code>{{ factText(item) }}</code>
            </article>
          </details>
        </section>
      </aside>
    </div>
  </section>
</template>
