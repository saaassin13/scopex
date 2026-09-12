<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError } from '../api'
import type { TaskSnapshot } from '../types'

const router = useRouter()
const tasks = ref<TaskSnapshot[]>([])
const message = ref('')
const loading = ref(false)
const error = ref('')
let timer: number | undefined

async function refresh() {
  try {
    tasks.value = (await api.listTasks()).tasks
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc)
  }
}

async function createTask() {
  const value = message.value.trim()
  if (!value || loading.value) return
  loading.value = true
  error.value = ''
  try {
    const task = await api.createTask(value)
    message.value = ''
    await router.push(`/tasks/${task.id}`)
  } catch (exc) {
    error.value = exc instanceof ApiError ? `${exc.code}: ${exc.message}` : String(exc)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void refresh()
  timer = window.setInterval(refresh, 2500)
})
onBeforeUnmount(() => timer && window.clearInterval(timer))
</script>

<template>
  <section class="dashboard-grid">
    <div class="hero-panel panel">
      <div class="eyebrow">LOCAL · EVIDENCE-CALIBRATED</div>
      <h1>把现场问题交给 Agent 调查，<br />把结论交给证据约束。</h1>
      <p>ScopeX 在本机读取日志和设备状态，自主调查，并以可追溯 Evidence 输出结果。</p>

      <form class="task-compose" @submit.prevent="createTask">
        <textarea
          v-model="message"
          rows="5"
          placeholder="例如：分析 10:15 左右任务失败，先检查视觉和系统日志，不要把 status=137 直接等同于 OOM。"
        ></textarea>
        <div class="compose-footer">
          <span>当前版本：单个主要任务执行槽位</span>
          <button class="primary-button" :disabled="loading || !message.trim()">
            {{ loading ? '创建中…' : '开始调查' }}
          </button>
        </div>
      </form>
      <p v-if="error" class="error-banner">{{ error }}</p>
    </div>

    <aside class="panel task-history">
      <div class="section-heading">
        <div>
          <div class="eyebrow">HISTORY</div>
          <h2>最近任务</h2>
        </div>
        <button class="ghost-button" @click="refresh">刷新</button>
      </div>
      <div v-if="!tasks.length" class="empty-state">还没有任务。</div>
      <button
        v-for="task in tasks"
        :key="task.id"
        class="task-row"
        @click="router.push(`/tasks/${task.id}`)"
      >
        <span class="state-pill" :data-state="task.state">{{ task.state }}</span>
        <strong>{{ task.user_request || task.id }}</strong>
        <small>{{ task.id }}</small>
      </button>
    </aside>
  </section>
</template>
