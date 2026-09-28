<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
const props = defineProps<{ taskId: string }>()
const plots = ref<Array<{ index: number; filename: string; candidate_id: string | null; kind: string }>>([])
const selected = ref(0)
const error = ref('')
const imageError = ref(false)
const current = computed(() => plots.value.find(row => row.index === selected.value))
onMounted(async () => {
  try {
    plots.value = (await api.getPlots(props.taskId)).plots
    selected.value = plots.value.find(row => row.candidate_id)?.index ?? 0
  } catch (exc) { error.value = exc instanceof Error ? exc.message : String(exc) }
})
</script>

<template>
  <section v-if="plots.length || error" class="panel encoder-plots">
    <h2>疑点曲线</h2>
    <p v-if="error" class="error-banner">曲线暂时无法加载：{{ error }}</p>
    <template v-if="plots.length">
      <label><span>选择片段</span><select v-model.number="selected" @change="imageError = false">
        <option v-for="plot in plots" :key="plot.index" :value="plot.index">{{ plot.candidate_id ? `疑点 ${plot.candidate_id} · 局部放大` : '完整时间窗 · 总览' }}</option>
      </select></label>
      <p class="muted">从上到下：累计读数、采样间隔、每次读数变化、短时速度。黄色区域标出疑点，横轴为日志时间；数据缺口不会连线。</p>
      <p v-if="imageError" class="error-banner">图表无法读取或已发生变化，请重新打开任务核对。</p>
      <a v-else-if="current" :href="api.plotUrl(taskId, selected)" target="_blank" rel="noopener">
        <img :key="selected" :src="api.plotUrl(taskId, selected)" :alt="current.candidate_id ? `编码器疑点 ${current.candidate_id} 的局部原始读数曲线` : '编码器原始读数全窗曲线'" @error="imageError = true" />
      </a>
      <p class="muted">点击曲线查看原尺寸。曲线来自保存的原始读数快照；突出显示表示需要检查，不代表已经确诊。</p>
    </template>
  </section>
</template>

<style scoped>
.encoder-plots h2 { margin-top: 0; font-size: 20px; }
.encoder-plots label { display: grid; gap: 8px; max-width: 360px; font-size: 14px; }
.encoder-plots p { font-size: 13px; line-height: 1.7; }
.encoder-plots img { display: block; width: 100%; height: auto; border-radius: 8px; }
</style>
