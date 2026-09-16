<script setup lang="ts">
import { computed, ref } from 'vue'
import type { TaskSnapshot } from '../types'
import { api, ApiError } from '../api'
import ResultBadge from './ResultBadge.vue'
const props = defineProps<{ task: TaskSnapshot }>()
const emit = defineEmits<{ refresh: [] }>()
const busy = ref(false)
const error = ref('')
const value = computed(() => props.task.assessment)
const terminal = computed(() => ['COMPLETED', 'FAILED', 'CANCELLED'].includes(props.task.state))
const canAssess = computed(() => terminal.value && (!value.value || ['not_assessed', 'needs_review'].includes(value.value.status)))
async function assess() {
  if (busy.value) return
  if (!window.confirm('评估只归类本次已保存的任务说明和正文；必要时最多发起一次短文本模型调用，不重读原图或日志，不修改任务执行状态，也不会推送。继续吗？')) return
  const id = props.task.id
  busy.value = true
  error.value = ''
  try {
    await api.assessTask(id, true, value.value?.status === 'needs_review')
    if (props.task.id === id) emit('refresh')
  } catch (exc) {
    if (props.task.id === id) error.value = exc instanceof ApiError ? exc.message : String(exc)
  } finally { if (props.task.id === id) busy.value = false }
}
</script>

<template>
  <section class="panel assessment-panel" aria-label="结果判定">
    <div class="section-heading">
      <div><div class="eyebrow">结果判定</div><h2><ResultBadge :value="value" /></h2></div>
      <button v-if="canAssess" class="ghost-button" :disabled="busy" @click="assess">{{ busy ? '提交中…' : '手动评估结果' }}</button>
    </div>
    <p>{{ value?.summary || '本次未开启自动判定；任务完成后可主动评估。' }}</p>
    <p v-if="value?.source === 'manual_text'" class="muted">仅归类已保存正文，未重新调查或独立核验事实。本次模型调用尝试：{{ value.model_calls }}。</p>
    <p v-else class="muted">异常条件来自任务说明；标签不代表独立可信度认证。平台推送尚未接入。</p>
    <p v-if="value?.push_decision === 'suggested'" class="muted">当前仅保存推送建议，未发送任何通知。</p>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.assessment-panel { padding: 24px; }
.assessment-panel p { line-height: 1.7; overflow-wrap: anywhere; }
.assessment-panel .section-heading { flex-wrap: wrap; }
</style>
