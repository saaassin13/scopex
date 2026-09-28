<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { api } from '../api'
import type { TaskSnapshot } from '../types'
import type { TaskFilters } from '../assessment'
import TaskList from '../components/TaskList.vue'
import TaskResultFilters from '../components/TaskResultFilters.vue'

const route = useRoute()
const id = computed(() => String(route.params.id))
const name = ref('定时任务')
const tasks = ref<TaskSnapshot[]>([])
const offset = ref(0)
const filters = ref<TaskFilters>({})
const hasNext = ref(false)
const busy = ref(false)
const error = ref('')
const pageSize = 50
let generation = 0
async function load(next = 0) {
  const token = ++generation
  busy.value = true
  error.value = ''
  try {
    const data = await api.listTasks({ schedule_id: id.value, limit: pageSize + 1, offset: next, ...filters.value })
    if (token !== generation) return
    tasks.value = data.tasks.slice(0, pageSize)
    hasNext.value = data.tasks.length > pageSize
    offset.value = next
  } catch (exc) {
    if (token === generation) error.value = exc instanceof Error ? exc.message : String(exc)
  } finally { if (token === generation) busy.value = false }
}
watch(filters, () => { tasks.value = []; void load() })
watch(id, async (value) => {
  tasks.value = []
  name.value = '定时任务'
  void load()
  try {
    const rows = (await api.listSchedules()).schedules
    if (id.value === value) name.value = rows.find(row => row.id === value)?.name || '已删除的定时任务'
  } catch { /* History remains available even when schedule metadata cannot load. */ }
}, { immediate: true })
</script>

<template>
  <section class="panel schedule-history">
    <RouterLink class="back-link" to="/schedules">← 返回定时任务</RouterLink>
    <div class="section-heading">
      <div><div class="eyebrow">执行历史</div><h1>{{ name }}</h1></div>
      <button class="ghost-button" :disabled="busy" @click="load()">刷新</button>
    </div>
    <p class="muted">按最新执行时间排序，包含该定时任务的“立即执行”记录；先筛选再分页。</p>
    <TaskResultFilters v-model="filters" />
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <p v-if="busy" role="status">正在加载…</p>
    <p v-else-if="!error && !tasks.length" class="empty-state">暂无执行记录。</p>
    <div :aria-busy="busy">
      <TaskList :tasks="tasks" :schedule-names="{ [id]: name }" />
    </div>
    <div v-if="tasks.length || offset" class="history-pagination">
      <button class="ghost-button" :disabled="busy || offset === 0" @click="load(Math.max(0, offset - pageSize))">上一页</button>
      <span>第 {{ Math.floor(offset / pageSize) + 1 }} 页</span>
      <button class="ghost-button" :disabled="busy || !hasNext" @click="load(offset + pageSize)">下一页</button>
    </div>
  </section>
</template>

<style scoped>
.schedule-history { padding: clamp(20px, 4vw, 40px); }
.history-run strong, .history-run small { display: block; margin-top: 8px; overflow-wrap: anywhere; }
.history-run { display: block; color: inherit; text-decoration: none; }
.history-run:focus-visible { outline: 2px solid currentColor; outline-offset: 3px; }
.history-pagination { display: flex; align-items: center; justify-content: center; gap: 1rem; margin-top: 1.5rem; }
</style>
