<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
const props = defineProps<{ taskId: string }>()
const images = ref<Array<{ index: number; filename: string; source: string }>>([])
const error = ref('')
const failed = ref(new Set<number>())
const selected = ref(0)
const dialog = ref<HTMLDialogElement | null>(null)
const current = computed(() => images.value[selected.value])
function caption(filename: string) {
  const stamp = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})(\d{3})\./.exec(filename)
  return stamp ? `${stamp[1]}-${stamp[2]}-${stamp[3]} ${stamp[4]}:${stamp[5]}:${stamp[6]}.${stamp[7]}` : filename
}
function open(index: number) {
  selected.value = index
  dialog.value?.showModal()
}
function move(step: number) {
  selected.value = Math.max(0, Math.min(images.value.length - 1, selected.value + step))
}
function keydown(event: KeyboardEvent) {
  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
    event.preventDefault()
    move(event.key === 'ArrowLeft' ? -1 : 1)
  }
}
onMounted(async () => {
  try { images.value = (await api.getImages(props.taskId)).images }
  catch (exc) { error.value = exc instanceof Error ? exc.message : String(exc) }
})
</script>

<template>
  <section v-if="images.length || error" class="panel task-images">
    <div class="section-heading"><h2>已分析照片</h2><span class="muted">{{ images.length }} 张</span></div>
    <p class="muted">点击放大，使用左右方向键切换。照片按来源及拍摄时间排列，可对照任务结果复核。</p>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <div class="image-grid">
      <button v-for="(item, index) in images" :key="item.index" class="image-card" @click="open(index)">
        <span v-if="failed.has(item.index)" class="image-unavailable">原图无法读取或已变化</span>
        <img v-else :src="api.imageUrl(taskId, item.index)" loading="lazy" :alt="`已分析照片 ${caption(item.filename)}`" @error="failed.add(item.index)" />
        <span>{{ caption(item.filename) }}</span>
      </button>
    </div>
    <dialog ref="dialog" class="image-dialog" aria-label="已分析照片原图" @keydown="keydown" @click="($event.target === dialog) && dialog?.close()">
      <template v-if="current">
        <div class="image-toolbar"><strong>{{ selected + 1 }} / {{ images.length }} · {{ caption(current.filename) }}</strong><button class="ghost-button" autofocus @click="dialog?.close()">关闭</button></div>
        <p v-if="failed.has(current.index)" class="error-banner" role="alert">原图无法读取或与保存的分析证据不一致。</p>
        <img v-else :key="current.index" :src="api.imageUrl(taskId, current.index)" :alt="`原图 ${caption(current.filename)}`" @error="failed.add(current.index)" />
        <div class="image-toolbar">
          <button class="ghost-button" :disabled="selected === 0" @click="move(-1)">上一张</button>
          <a class="ghost-button" :href="api.imageUrl(taskId, current.index)" target="_blank" rel="noopener">查看原图</a>
          <button class="ghost-button" :disabled="selected === images.length - 1" @click="move(1)">下一张</button>
        </div>
        <p class="image-source muted">{{ current.source }}</p>
      </template>
    </dialog>
  </section>
</template>

<style scoped>
.task-images h2 { margin: 0; font-size: 20px; }
.task-images > p { font-size: 14px; line-height: 1.6; }
.image-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 220px), 1fr)); gap: 12px; }
.image-card { display: grid; gap: 8px; padding: 8px; border: 1px solid var(--panel-border); border-radius: 8px; background: transparent; color: inherit; cursor: pointer; text-align: left; min-width: 0; }
.image-card img, .image-unavailable { display: block; width: 100%; aspect-ratio: 4 / 3; object-fit: contain; background: rgba(255,255,255,.04); border-radius: 4px; }
.image-card > span { font-size: 13px; overflow-wrap: anywhere; }
.image-unavailable { padding: 16px; box-sizing: border-box; }
.image-dialog { width: min(1100px, calc(100vw - 32px)); max-height: calc(100dvh - 32px); padding: 16px; border: 1px solid var(--panel-border); background: #10151d; color: inherit; border-radius: 12px; box-sizing: border-box; }
.image-dialog::backdrop { background: rgb(0 0 0 / 65%); }
.image-dialog > img { display: block; width: 100%; max-height: 70dvh; object-fit: contain; background: rgba(255,255,255,.04); }
.image-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin: 8px 0; }
.image-toolbar strong { font-size: 14px; }
.image-toolbar button, .image-toolbar a { min-height: 44px; }
.image-source { font-size: 12px; overflow-wrap: anywhere; }
</style>
