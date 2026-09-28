<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ text: string }>()
// Render common native-answer blocks as Vue text nodes. Raw HTML is never executed.
const blocks = computed(() => {
  const result: Array<{ type: 'heading' | 'paragraph' | 'list' | 'code'; lines: string[] }> = []
  let fenced = false
  for (const line of props.text.split('\n')) {
    if (/^\s*```/.test(line)) {
      fenced = !fenced
      if (fenced) result.push({ type: 'code', lines: [] })
      continue
    }
    if (fenced) { result[result.length - 1].lines.push(line); continue }
    if (!line.trim()) { result.push({ type: 'paragraph', lines: [] }); continue }
    const heading = line.match(/^#{1,6}\s+(.+)$/) || line.match(/^\*\*(.+)\*\*\s*$/)
    const list = line.match(/^\s*(?:[-*]|\d+[.)、])\s+(.+)$/)
    const type = heading ? 'heading' : list ? 'list' : 'paragraph'
    const value = heading?.[1] || list?.[1] || line
    const last = result[result.length - 1]
    if (last?.type === type && type !== 'heading') last.lines.push(value)
    else result.push({ type, lines: [value] })
  }
  return result.filter(block => block.lines.length)
})
function spans(text: string) {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean).map(value => ({
    type: value.startsWith('**') ? 'strong' : value.startsWith('`') ? 'code' : 'span',
    text: value.startsWith('**') ? value.slice(2, -2) : value.startsWith('`') ? value.slice(1, -1) : value,
  }))
}
</script>

<template>
  <div class="readable-report">
    <template v-for="(block, index) in blocks" :key="index">
      <h3 v-if="block.type === 'heading'">{{ block.lines[0] }}</h3>
      <pre v-else-if="block.type === 'code'"><code>{{ block.lines.join('\n') }}</code></pre>
      <ul v-else-if="block.type === 'list'"><li v-for="(line, i) in block.lines" :key="i"><component :is="span.type" v-for="(span, j) in spans(line)" :key="j">{{ span.text }}</component></li></ul>
      <p v-else><component :is="span.type" v-for="(span, j) in spans(block.lines.join('\n'))" :key="j">{{ span.text }}</component></p>
    </template>
  </div>
</template>

<style scoped>
.readable-report { line-height: 1.9; font-size: 16px; overflow-wrap: anywhere; }
.readable-report h3 { margin: 28px 0 10px; font-size: 17px; }
.readable-report h3:first-child { margin-top: 0; }
.readable-report p { white-space: pre-wrap; margin: 0 0 16px; }
.readable-report ul { padding-left: 22px; margin: 8px 0 20px; }
.readable-report li { margin: 8px 0; }
.readable-report code { font-size: .87em; background: rgba(255,255,255,.06); border-radius: 4px; padding: 2px 4px; }
.readable-report pre { overflow: auto; padding: 16px; background: rgba(0,0,0,.22); }
</style>
