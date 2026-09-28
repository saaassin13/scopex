<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError } from '../api'
import type { TaskSnapshot } from '../types'
import type { TaskFilters } from '../assessment'
import TaskList from '../components/TaskList.vue'
import TaskResultFilters from '../components/TaskResultFilters.vue'

const router = useRouter()
const tasks = ref<TaskSnapshot[]>([])
const scheduleNames = ref<Record<string, string>>({})
const message = ref('')
const assessmentEnabled = ref(true)
const filters = ref<TaskFilters>({})
const showComposer = ref(false)
const query = ref('')
const day = ref('')
const scope = ref<'all' | 'attention' | 'manual' | 'schedule'>('all')
const offset = ref(0)
const pageSize = 30
const hasNext = ref(false)
const loading = ref(false)
const historyLoading = ref(false)
const deletingId = ref('')
const error = ref('')
let generation = 0
let timer: number | undefined
let searchTimer: number | undefined

async function refresh(next = offset.value) {
  const token = ++generation
  historyLoading.value = true
  try {
    const rows = await api.listTasks({ day: day.value || undefined, ...filters.value,
      query: query.value.trim() || undefined, attention: scope.value === 'attention',
      trigger_type: ['manual', 'schedule'].includes(scope.value) ? scope.value as 'manual' | 'schedule' : undefined,
      offset: next, limit: pageSize + 1 })
    if (token !== generation) return
    tasks.value = rows.tasks.slice(0, pageSize)
    hasNext.value = rows.tasks.length > pageSize
    offset.value = next
    error.value = ''
  } catch (exc) {
    if (token === generation) error.value = exc instanceof Error ? exc.message : String(exc)
  } finally { if (token === generation) historyLoading.value = false }
}
function resetList() { tasks.value = []; hasNext.value = false; void refresh(0) }
watch([filters, day, scope], resetList)
watch(query, () => { window.clearTimeout(searchTimer); searchTimer = window.setTimeout(resetList, 250) })

async function createEntry() {
  const value = message.value.trim()
  if (!value || loading.value) return
  loading.value = true
  error.value = ''
  try {
    const task = await api.createRun(value, assessmentEnabled.value)
    await router.push(`/tasks/${task.id}`)
  } catch (exc) { error.value = exc instanceof ApiError ? exc.message : String(exc) }
  finally { loading.value = false }
}
async function deleteEntry(task: TaskSnapshot) {
  if (deletingId.value || !window.confirm('删除本次执行记录、结果及复盘包？原始业务日志和图片会保留。')) return
  deletingId.value = task.id
  try { await api.deleteTask(task.id); await refresh() }
  catch (exc) { error.value = exc instanceof Error ? exc.message : String(exc) }
  finally { deletingId.value = '' }
}
onMounted(() => {
  void refresh()
  void api.listSchedules().then(data => {
    scheduleNames.value = Object.fromEntries(data.schedules.map(row => [row.id, row.name]))
  }).catch(() => { /* The original request remains the title when names are unavailable. */ })
  timer = window.setInterval(() => { if (!historyLoading.value) void refresh() }, 10000)
})
onBeforeUnmount(() => { generation++; window.clearInterval(timer); window.clearTimeout(searchTimer) })
</script>

<template>
  <section class="dashboard-page">
    <header class="page-heading">
      <div><h1>任务与结果</h1><p class="muted">查看检查结论，打开需要进一步排查的任务。</p></div>
      <button class="primary-button" :aria-expanded="showComposer" aria-controls="new-task" @click="showComposer = !showComposer">{{ showComposer ? '收起新建' : '新建任务' }}</button>
    </header>
    <section v-if="showComposer" id="new-task" class="panel compose-panel">
      <h2>新建检查</h2>
      <form @submit.prevent="createEntry">
        <label for="task-request">检查目标与时间范围</label>
        <textarea id="task-request" v-model="message" rows="3" placeholder="例如：检查今天 08:00–09:00 的编码器数据，说明疑点和排查依据。"></textarea>
        <div class="compose-footer">
          <label class="assessment-toggle"><input v-model="assessmentEnabled" type="checkbox" /> 同时给出结果标签</label>
          <button class="primary-button" :disabled="loading || !message.trim()">{{ loading ? '创建中…' : '开始检查' }}</button>
        </div>
      </form>
    </section>
    <section class="panel task-workspace">
      <div class="list-toolbar">
        <div class="scope-switch" aria-label="任务范围">
          <button v-for="item in ([['all', '全部任务'], ['attention', '需要关注'], ['schedule', '定时检查'], ['manual', '手动任务']] as const)"
            :key="item[0]" :class="{ active: scope === item[0] }" :aria-pressed="scope === item[0]" @click="scope = item[0]">{{ item[1] }}</button>
        </div>
        <button class="ghost-button" :disabled="historyLoading" @click="refresh()">刷新</button>
      </div>
      <div class="list-search">
        <label><span>搜索任务内容</span><input v-model="query" type="search" maxlength="200" placeholder="编码器、相机模糊…" /></label>
        <label><span>执行日期</span><input v-model="day" type="date" /></label>
        <button v-if="day" class="ghost-button" @click="day = ''">全部日期</button>
      </div>
      <details class="list-filters"><summary>按执行状态或结果筛选<span v-if="Object.values(filters).some(Boolean)"> · 已启用</span></summary><TaskResultFilters v-model="filters" /></details>
      <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
      <p v-if="historyLoading" class="muted" role="status">正在更新任务…</p>
      <p v-else-if="!error && !tasks.length" class="empty-state">没有符合条件的任务。可以调整日期、搜索词或筛选条件。</p>
      <TaskList :tasks="tasks" :schedule-names="scheduleNames" :deleting-id="deletingId" allow-delete @delete="deleteEntry" />
      <div v-if="tasks.length || offset" class="history-pagination">
        <button class="ghost-button" :disabled="historyLoading || offset === 0" @click="refresh(Math.max(0, offset - pageSize))">上一页</button>
        <span>第 {{ Math.floor(offset / pageSize) + 1 }} 页</span>
        <button class="ghost-button" :disabled="historyLoading || !hasNext" @click="refresh(offset + pageSize)">下一页</button>
      </div>
    </section>
  </section>
</template>
