<script setup lang="ts">
import { assessmentLabels, executionLabels, pushLabels } from '../assessment'
import type { TaskFilters } from '../assessment'
const props = defineProps<{ modelValue: TaskFilters }>()
const emit = defineEmits<{ 'update:modelValue': [value: TaskFilters] }>()
function update(key: keyof TaskFilters, event: Event) {
  emit('update:modelValue', { ...props.modelValue, [key]: (event.target as HTMLSelectElement).value || undefined })
}
</script>

<template>
  <div class="result-filters" aria-label="任务筛选">
    <label><span>执行状态</span><select :value="modelValue.state || ''" @change="update('state', $event)">
      <option value="">全部执行状态</option>
      <option v-for="(label, key) in executionLabels" :key="key" :value="key">{{ label }}</option>
    </select></label>
    <label><span>结果状态</span><select :value="modelValue.assessment_status || ''" @change="update('assessment_status', $event)">
      <option value="">全部结果</option>
      <option v-for="(label, key) in assessmentLabels" :key="key" :value="key">{{ label }}</option>
    </select></label>
    <label><span>推送建议</span><select :value="modelValue.push_decision || ''" @change="update('push_decision', $event)">
      <option value="">全部推送建议</option>
      <option v-for="(label, key) in pushLabels" :key="key" :value="key">{{ label }}</option>
    </select></label>
    <button class="ghost-button" type="button" @click="emit('update:modelValue', {})">清除筛选</button>
  </div>
</template>

<style scoped>
.result-filters { display: flex; flex-wrap: wrap; align-items: end; gap: 10px; margin: 16px 0; }
.result-filters label { display: grid; gap: 6px; flex: 1 1 130px; font-size: 12px; color: var(--muted); }
.result-filters select { min-width: 0; font-size: 13px; }
</style>
