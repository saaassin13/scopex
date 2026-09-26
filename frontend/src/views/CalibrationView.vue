<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api, ApiError } from '../api'
import type { CalibrationCamera, Point } from '../types'

type ToolMode = 'carousel' | 'forbidden' | 'line' | 'edit'

const cameras = ref<CalibrationCamera[]>([])
const camera = ref('')
const frameId = ref<string | null>(null)
const frameUrl = ref<string | null>(null)
const frameSize = ref<[number, number] | null>(null)
const step = ref(0)
const tool = ref<ToolMode>('carousel')
const error = ref('')
const busy = ref(false)
const saveResult = ref<{ version: number; warnings: string[]; command: string } | null>(null)

const carousel = ref<Point[]>([])
const forbidden = reactive<Point[][]>([])
const line = ref<Point[]>([])
const params = reactive({
  fps: 2.0, min_area_ratio: 0.02, warmup_frames: 50, trigger_sustain_s: 30.0,
  clear_sustain_s: 10.0, merge_distance_px: 300.0, min_on_carousel_ratio: 0.5,
  min_conf: 0.5, platform_line_margin_px: 8.0,
})
const note = ref('')

const canvas = ref<HTMLCanvasElement | null>(null)
const image = ref<HTMLImageElement | null>(null)
const dragIndex = ref<{ list: 'carousel' | 'line' | 'forbidden'; zone: number; point: number } | null>(null)
const hoverPoint = ref<Point | null>(null)

const steps = [
  '上传参考帧',
  '圈出转盘区域',
  '圈出禁区（掉落物落地的地面）',
  '平台边缘线（可选）',
  '确认参数并保存',
]

const canSave = computed(() =>
  frameSize.value !== null && carousel.value.length >= 3 && step.value === 4)

// 未画完不给进下一步，避免懵着走完保存出无效标定
const stepBlockedHint = computed(() => {
  if (step.value === 1 && carousel.value.length < 3)
    return `转盘区域至少要 3 个点才能围成面（已点 ${carousel.value.length} 个）`
  if (step.value === 2 && !forbidden.some((zone) => zone.length >= 3))
    return '至少圈出一块禁区（3 个点以上），或点"按转盘自动生成"先出一版再修正'
  return ''
})

const toolHint = computed(() => ({
  carousel: '正在圈转盘（绿）：沿转盘外沿每点一下落一个点，虚线是连线预览；拖动圆点修正，右键删除。这一步只动转盘。',
  forbidden: '正在圈禁区（红）：沿掉落物落地的地面边缘点击。转盘（绿）已锁定不会被碰到，到最后确认步可统一微调。',
  line: '正在画平台线（蓝）：沿平台边缘点 2 个点即可，可跳过。',
  edit: '微调模式：所有形状的顶点都可以拖动，右键删除。',
}[tool.value]))

onMounted(refresh)

async function refresh() {
  try {
    const result = await api.listCalibrations()
    cameras.value = result.cameras
  } catch (exc) {
    if (exc instanceof ApiError && exc.status !== 404) error.value = exc.message
  }
}

function selectCamera(entry: CalibrationCamera) {
  camera.value = entry.camera
  saveResult.value = null
  error.value = ''
}

async function onFrameChosen(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  if (!camera.value.trim()) {
    error.value = '先填写相机标识（例如 camera-96）'
    return
  }
  busy.value = true
  error.value = ''
  try {
    const dataUrl = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result))
      reader.onerror = () => reject(new Error('读取图片失败'))
      reader.readAsDataURL(file)
    })
    const result = await api.uploadCalibrationFrame(camera.value.trim(), dataUrl.slice(dataUrl.indexOf(',') + 1))
    frameId.value = result.frame_id
    frameUrl.value = api.calibrationFrameUrl(camera.value.trim(), result.frame_id)
    await loadImage()
    step.value = 1
    tool.value = 'carousel'
    await refresh()
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc)
  } finally {
    busy.value = false
    input.value = ''
  }
}

async function reuseFrame(existing: CalibrationCamera) {
  const latest = existing.versions.filter((v) => v.frame_id).at(-1)
  if (!latest?.frame_id) return
  camera.value = existing.camera
  frameId.value = latest.frame_id
  frameUrl.value = api.calibrationFrameUrl(existing.camera, latest.frame_id)
  await loadImage()
  step.value = 1
}

function loadImage() {
  return new Promise<void>((resolve) => {
    const img = new Image()
    img.onload = () => {
      image.value = img
      frameSize.value = [img.naturalWidth, img.naturalHeight]
      requestAnimationFrame(redraw)
      resolve()
    }
    img.src = frameUrl.value!
  })
}

function redraw() {
  const element = canvas.value
  const img = image.value
  if (!element || !img) return
  const maxW = Math.min(920, img.naturalWidth)
  const scale = maxW / img.naturalWidth
  element.width = Math.round(img.naturalWidth * scale)
  element.height = Math.round(img.naturalHeight * scale)
  const ctx = element.getContext('2d')!
  ctx.drawImage(img, 0, 0, element.width, element.height)
  const drawPolygon = (points: Point[], stroke: string, fill: string, close: boolean) => {
    if (!points.length) return
    ctx.beginPath()
    ctx.moveTo(points[0][0] * scale, points[0][1] * scale)
    for (const [x, y] of points.slice(1)) ctx.lineTo(x * scale, y * scale)
    if (close) ctx.closePath()
    ctx.fillStyle = fill
    if (close) ctx.fill()
    ctx.strokeStyle = stroke
    ctx.lineWidth = 2
    ctx.stroke()
    for (const [x, y] of points) {
      ctx.beginPath()
      ctx.arc(x * scale, y * scale, 5, 0, Math.PI * 2)
      ctx.fillStyle = stroke
      ctx.fill()
    }
  }
  drawPolygon(carousel.value, '#2e9e5b', 'rgba(46,158,91,0.18)', true)
  forbidden.forEach((zone) => drawPolygon(zone, '#c0392b', 'rgba(192,57,43,0.18)', zone.length >= 3))
  // 画点过程中的连线预览：最后一点 → 鼠标位置 →（多边形时）回到起点
  const preview: { points: Point[]; stroke: string; close: boolean }[] = []
  if (tool.value === 'carousel' && carousel.value.length) preview.push({ points: carousel.value, stroke: '#2e9e5b', close: true })
  else if (tool.value === 'forbidden' && forbidden.length && forbidden[forbidden.length - 1].length)
    preview.push({ points: forbidden[forbidden.length - 1], stroke: '#c0392b', close: true })
  else if (tool.value === 'line' && line.value.length === 1) preview.push({ points: line.value, stroke: '#2563eb', close: false })
  const hover = hoverPoint.value
  if (hover) {
    for (const { points, stroke, close } of preview) {
      const last = points[points.length - 1]
      ctx.setLineDash([6, 6])
      ctx.beginPath()
      ctx.moveTo(last[0] * scale, last[1] * scale)
      ctx.lineTo(hover[0] * scale, hover[1] * scale)
      if (close) ctx.lineTo(points[0][0] * scale, points[0][1] * scale)
      ctx.strokeStyle = stroke
      ctx.lineWidth = 2
      ctx.stroke()
      ctx.setLineDash([])
    }
  }
  if (line.value.length) {
    ctx.beginPath()
    ctx.moveTo(line.value[0][0] * scale, line.value[0][1] * scale)
    for (const [x, y] of line.value.slice(1)) ctx.lineTo(x * scale, y * scale)
    ctx.strokeStyle = '#2563eb'
    ctx.lineWidth = 3
    ctx.stroke()
    for (const [x, y] of line.value) {
      ctx.beginPath()
      ctx.arc(x * scale, y * scale, 5, 0, Math.PI * 2)
      ctx.fillStyle = '#2563eb'
      ctx.fill()
    }
  }
}

function toFrameCoords(event: MouseEvent): Point {
  const element = canvas.value!
  const rect = element.getBoundingClientRect()
  const scaleX = (frameSize.value?.[0] ?? 1) / rect.width
  const scaleY = (frameSize.value?.[1] ?? 1) / rect.height
  return [Math.round((event.clientX - rect.left) * scaleX), Math.round((event.clientY - rect.top) * scaleY)]
}

function hitPoint(point: Point, target: Point) {
  return Math.hypot(point[0] - target[0], point[1] - target[1]) < 12
}

function onCanvasClick(event: MouseEvent) {
  if (event.button === 2) return
  const point = toFrameCoords(event)
  if (tool.value === 'carousel') carousel.value.push(point)
  else if (tool.value === 'line' && line.value.length < 2) line.value.push(point)
  else if (tool.value === 'forbidden') {
    if (!forbidden.length) forbidden.push([])
    forbidden[forbidden.length - 1].push(point)
  }
  redraw()
}

// 每个步骤只允许拖动/删除当前正在画的形状；最后确认步（edit）全部放开统一微调
function editableTargets(): { list: 'carousel' | 'line' | 'forbidden'; zone: number }[] {
  if (tool.value === 'edit') {
    const targets: { list: 'carousel' | 'line' | 'forbidden'; zone: number }[] = [
      { list: 'carousel', zone: -1 },
      { list: 'line', zone: -1 },
    ]
    forbidden.forEach((_, zone) => targets.push({ list: 'forbidden', zone }))
    return targets
  }
  if (tool.value === 'carousel') return [{ list: 'carousel', zone: -1 }]
  if (tool.value === 'line') return [{ list: 'line', zone: -1 }]
  return forbidden.map((_, zone) => ({ list: 'forbidden' as const, zone }))
}

function pointsOf(target: { list: 'carousel' | 'line' | 'forbidden'; zone: number }): Point[] {
  if (target.list === 'carousel') return carousel.value
  if (target.list === 'line') return line.value
  return forbidden[target.zone]
}

function onCanvasRightClick(event: MouseEvent) {
  const point = toFrameCoords(event)
  for (const target of editableTargets()) {
    const points = pointsOf(target)
    const index = points.findIndex((p) => hitPoint(p, point))
    if (index >= 0) {
      points.splice(index, 1)
      if (target.list === 'forbidden' && !points.length) forbidden.splice(target.zone, 1)
      redraw()
      return
    }
  }
}

function onCanvasDown(event: MouseEvent) {
  if (event.button !== 0) return
  const point = toFrameCoords(event)
  for (const target of editableTargets()) {
    const index = pointsOf(target).findIndex((p) => hitPoint(p, point))
    if (index >= 0) { dragIndex.value = { ...target, point: index }; return }
  }
}

function onMouseMove(event: MouseEvent) {
  const point = toFrameCoords(event)
  const drag = dragIndex.value
  if (!drag) {
    if (hoverPoint.value?.[0] !== point[0] || hoverPoint.value?.[1] !== point[1]) {
      hoverPoint.value = point
      redraw()
    }
    return
  }
  if (drag.list === 'carousel') carousel.value[drag.point] = point
  else if (drag.list === 'line') line.value[drag.point] = point
  else forbidden[drag.zone][drag.point] = point
  redraw()
}

function stopDrag() { dragIndex.value = null }

function onCanvasLeave() {
  dragIndex.value = null
  if (hoverPoint.value) {
    hoverPoint.value = null
    redraw()
  }
}

function expandFromCarousel() {
  if (carousel.value.length < 3) {
    error.value = '先圈出转盘区域，再自动生成禁区'
    return
  }
  const [width, height] = frameSize.value!
  const xs = carousel.value.map((p) => p[0])
  const ys = carousel.value.map((p) => p[1])
  const cx = xs.reduce((a, b) => a + b, 0) / xs.length
  const cy = ys.reduce((a, b) => a + b, 0) / ys.length
  const margin = Math.round(Math.max(width, height) * 0.06)
  const grow = (value: number, center: number, span: number) =>
    Math.round(center + (value - center) * (span + 2 * margin) / span)
  const spanX = Math.max(...xs) - Math.min(...xs)
  const spanY = Math.max(...ys) - Math.min(...ys)
  const zone: Point[] = carousel.value.map(([x, y]) => [
    Math.min(Math.max(grow(x, cx, spanX), 0), width),
    Math.min(Math.max(grow(y, cy, spanY), 0), height),
  ])
  forbidden.push(zone)
  redraw()
}

function buildConfig() {
  return {
    fps: params.fps,
    min_area_ratio: params.min_area_ratio,
    warmup_frames: params.warmup_frames,
    trigger_sustain_s: params.trigger_sustain_s,
    clear_sustain_s: params.clear_sustain_s,
    merge_distance_px: params.merge_distance_px,
    frame_size: frameSize.value,
    zones: {
      carousel: carousel.value.map((p) => [p[0], p[1]]),
      forbidden_zones: forbidden.filter((z) => z.length >= 3).map((z) => z.map((p) => [p[0], p[1]])),
      platform_line: line.value.length === 2 ? line.value.map((p) => [p[0], p[1]]) : null,
      min_on_carousel_ratio: params.min_on_carousel_ratio,
      platform_line_margin_px: params.platform_line_margin_px,
      min_conf: params.min_conf,
    },
  }
}

async function save() {
  busy.value = true
  error.value = ''
  try {
    const result = await api.saveCalibration(camera.value.trim(), buildConfig(), note.value, frameId.value)
    saveResult.value = { version: result.version, warnings: result.warnings, command: result.verify.command }
    step.value = 4
    await refresh()
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc)
  } finally {
    busy.value = false
  }
}

async function activate(version: number) {
  try {
    await api.activateCalibration(camera.value, version)
    await refresh()
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc)
  }
}

async function markVerify(version: number, result: 'passed' | 'failed') {
  const remark = result === 'passed' ? '静止场景验证通过' : '静止场景出现误报'
  try {
    await api.verifyCalibration(camera.value, version, result, remark)
    await refresh()
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc)
  }
}

const activeCamera = computed(() => cameras.value.find((c) => c.camera === camera.value))

watch(step, (value) => {
  if (value === 1) tool.value = 'carousel'
  if (value === 2) tool.value = 'forbidden'
  if (value === 3) tool.value = 'line'
  if (value === 4) tool.value = 'edit'
})
</script>

<template>
  <section class="page">
    <h2>监控标定</h2>
    <p class="hint">
      每个机位标定一次：上传一帧参考图，按引导圈出转盘和禁区，保存为带版本的配置；
      监控视角变化后可重新标定，旧版本保留可回滚。
    </p>
    <p v-if="error" class="error">{{ error }}</p>

    <div class="calibration-layout">
      <div class="calibration-canvas-area">
        <div v-if="!frameUrl" class="empty">
          <p>还没有参考帧。填写相机标识并上传一张参考图开始。</p>
        </div>
        <canvas v-else ref="canvas"
                @click="onCanvasClick" @contextmenu.prevent="onCanvasRightClick"
                @mousedown="onCanvasDown" @mousemove="onMouseMove"
                @mouseup="stopDrag" @mouseleave="onCanvasLeave" />
        <p v-if="frameUrl" class="hint">{{ toolHint }}</p>
      </div>

      <div class="calibration-panel">
        <ol class="steps">
          <li v-for="(title, index) in steps" :key="title"
              :class="{ active: step === index, done: step > index }"
              @click="frameSize && index <= step + 1 && (step = index)">
            {{ title }}
          </li>
        </ol>

        <div v-if="step === 0">
          <label>相机标识（如 camera-96）</label>
          <input v-model="camera" placeholder="camera-96" />
          <label>参考帧（现场画面截图，JPEG/PNG）</label>
          <input type="file" accept="image/jpeg,image/png" @change="onFrameChosen" />
          <p v-if="busy">上传中…</p>
          <div v-if="cameras.length" class="history">
            <h4>已有相机</h4>
            <div v-for="entry in cameras" :key="entry.camera" class="camera-row">
              <button class="link" @click="selectCamera(entry)">{{ entry.camera }}</button>
              <span>当前版本 v{{ entry.active_version ?? '—' }}（{{ entry.versions.length }} 个版本）</span>
              <button v-if="entry.versions.some((v) => v.frame_id)" class="link"
                      @click="reuseFrame(entry)">复用参考帧</button>
            </div>
          </div>
        </div>

        <div v-else-if="step === 1">
          <p>在画面上沿转盘外沿依次点击：每点一下落一个点，点与点自动连成线，围一圈后自动闭合成一个面。至少 3 个点，建议 6~10 个点更贴合边缘。画歪的点直接拖走，右键删掉。</p>
          <button @click="carousel = []; redraw()">清空重来</button>
        </div>

        <div v-else-if="step === 2">
          <p>用同样的方式圈出掉落物会落到的地面（禁区，红色）。转盘（绿色）在这一步是锁定的，碰不到。机器人手臂经过的地方可以画进来——它驻留不超过 {{ params.trigger_sustain_s }} 秒，不会触发。</p>
          <button @click="forbidden.push([])">新建一块禁区</button>
          <button @click="expandFromCarousel">按转盘自动生成（再手动修正）</button>
          <button @click="forbidden.pop(); redraw()">删除最后一块</button>
        </div>

        <div v-else-if="step === 3">
          <p>可选：沿平台边缘从左到右点两个点，用于判断牛悬挂在平台下方。跳过可直接下一步。</p>
          <button @click="line = []; redraw()">清空</button>
        </div>

        <div v-else>
          <div class="params">
            <label>抽帧频率 fps<input v-model.number="params.fps" type="number" step="0.5" /></label>
            <label>禁区占用比例下限<input v-model.number="params.min_area_ratio" type="number" step="0.01" /></label>
            <label>触发持续秒数<input v-model.number="params.trigger_sustain_s" type="number" step="5" /></label>
            <label>置信度下限<input v-model.number="params.min_conf" type="number" step="0.05" /></label>
          </div>
          <label>备注</label>
          <input v-model="note" placeholder="例如：初版 / 缩小禁区" />
          <button :disabled="!canSave || busy" @click="save">{{ busy ? '保存中…' : '保存新版本' }}</button>
          <div v-if="saveResult" class="save-result">
            <p>已保存 v{{ saveResult.version }}。</p>
            <p v-for="warning in saveResult.warnings" :key="warning" class="warn">{{ warning }}</p>
            <p class="hint">静止场景预检命令（在端侧对一段无异常录像运行，最长占用需短于触发秒数）：</p>
            <code>{{ saveResult.command }}</code>
          </div>
        </div>

        <div v-if="frameSize" class="nav-buttons">
          <button :disabled="step === 0" @click="step -= 1">上一步</button>
          <button :disabled="step >= 4 || !!stepBlockedHint" @click="step += 1">下一步</button>
        </div>
        <p v-if="stepBlockedHint" class="warn">{{ stepBlockedHint }}</p>

        <div v-if="activeCamera" class="history">
          <h4>{{ activeCamera.camera }} 的版本历史</h4>
          <table>
            <thead>
              <tr><th>版本</th><th>时间</th><th>备注</th><th>预检</th><th></th></tr>
            </thead>
            <tbody>
              <tr v-for="entry in [...activeCamera.versions].reverse()" :key="entry.version"
                  :class="{ active: entry.version === activeCamera.active_version }">
                <td>v{{ entry.version }}</td>
                <td>{{ entry.created_at.slice(0, 16) }}</td>
                <td>{{ entry.note }}</td>
                <td>{{ { pending: '待验证', passed: '通过', failed: '未通过' }[entry.verify.status] }}</td>
                <td>
                  <button v-if="entry.version !== activeCamera.active_version" class="link"
                          @click="activate(entry.version)">启用</button>
                  <button v-if="entry.verify.status !== 'passed'" class="link" @click="markVerify(entry.version, 'passed')">✓</button>
                  <button v-if="entry.verify.status !== 'failed'" class="link" @click="markVerify(entry.version, 'failed')">✗</button>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-for="entry in activeCamera.versions" :key="'w' + entry.version">
            <span v-for="warning in entry.warnings" :key="warning" class="warn">v{{ entry.version }}：{{ warning }}</span>
          </p>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.calibration-layout { display: flex; gap: 24px; align-items: flex-start; flex-wrap: wrap; }
.calibration-canvas-area { flex: 1 1 560px; min-width: 320px; }
.calibration-canvas-area canvas { max-width: 100%; border: 1px solid #ddd; border-radius: 6px; cursor: crosshair; }
.calibration-panel { flex: 1 1 340px; min-width: 300px; }
.steps { list-style: none; padding: 0; margin: 0 0 16px; }
.steps li { padding: 6px 10px; border-left: 3px solid #ddd; color: #888; cursor: pointer; }
.steps li.active { border-left-color: #2563eb; color: inherit; font-weight: 600; }
.steps li.done { color: inherit; border-left-color: #2e9e5b; }
.params { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 8px; }
.params label, .calibration-panel label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; margin-bottom: 8px; }
.nav-buttons { display: flex; gap: 8px; margin-top: 16px; }
.history { margin-top: 24px; }
.camera-row { display: flex; gap: 8px; align-items: center; margin: 4px 0; flex-wrap: wrap; }
.warn { color: #a05a00; }
.save-result code { display: block; background: #f5f5f5; padding: 8px; border-radius: 4px; font-size: 12px; word-break: break-all; }
table { width: 100%; font-size: 13px; border-collapse: collapse; }
th, td { text-align: left; padding: 4px 6px; border-bottom: 1px solid #eee; }
tr.active td { background: #eef5ff; }
.empty { border: 1px dashed #bbb; border-radius: 6px; padding: 32px; text-align: center; color: #777; }
</style>
