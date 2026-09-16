<script setup lang="ts">
import type { Assessment } from '../types'
import { assessmentLabel, pushLabels } from '../assessment'
defineProps<{ value?: Assessment | null }>()
</script>

<template>
  <span class="result-badges">
    <span class="result-badge" :data-result="value?.status || 'not_assessed'" :title="value?.summary">
      {{ assessmentLabel(value) }}
    </span>
    <span v-if="value?.push_decision && value.push_decision !== 'undecided'" class="result-push-badge">
      {{ pushLabels[value.push_decision] }}
    </span>
  </span>
</template>

<style scoped>
.result-badges { display: inline-flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.result-badge, .result-push-badge { border-radius: 999px; padding: 4px 8px; font-size: 12px; background: rgba(255,255,255,.07); color: var(--muted); }
.result-badge[data-result="normal"] { color: var(--accent); }
.result-badge[data-result="abnormal"] { color: var(--danger); border: 1px solid currentColor; }
.result-badge[data-result="needs_review"] { color: #f1d28c; }
.result-push-badge { color: inherit; }
</style>
