from pathlib import Path
R=Path('.')
def edit(path,old,new):
 p=R/path;s=p.read_text(); assert old in s,(path,old[:70]);p.write_text(s.replace(old,new))
edit('scopex/api/fastapi_app.py','    @app.post("/runs", status_code=202)','    @app.get("/activity")\n    def activity() -> dict[str, Any]:\n        return service.activity()\n\n    @app.post("/tasks/{task_id}/cancel-queued", status_code=202)\n    def cancel_queued(task_id: str) -> dict[str, Any]:\n        return service.cancel_queued(task_id)\n\n    @app.post("/runs", status_code=202)')
edit('scopex/api/schedules.py','            task_id = task["id"]','            task_id = task["id"]\n            if task.get("state") == "QUEUED":\n                status = "QUEUED"')
edit('scripts/runtime_api.py','import argparse','import argparse\nimport fcntl')
edit('scripts/runtime_api.py','    parser.add_argument("--finalizer-max-tokens", type=int, default=768)','    parser.add_argument("--finalizer-max-tokens", type=int, default=768, help="legacy finalizer compatibility only")\n    parser.add_argument("--report-max-tokens", type=int, default=2048)\n    parser.add_argument("--max-active-tasks", type=int, default=2, help="independent task slots; not a GPU throughput guarantee")\n    parser.add_argument("--max-queued-tasks", type=int, default=16)\n    parser.add_argument("--queue-timeout", type=int, default=600)')
edit('scripts/runtime_api.py','    os.umask(0o077)','    if args.max_active_tasks > 1 and args.exec_host != "sandbox":\n        raise ValueError("parallel product runs require isolated sandbox execution")\n    os.umask(0o077)\n    data_root = args.data_root.expanduser().resolve()\n    data_root.mkdir(parents=True, exist_ok=True)\n    # The in-process admission queue has exactly one owner. Do not run multiple\n    # uvicorn workers against this state root or reconcile another live process.\n    runtime_lock = (data_root / ".runtime.lock").open("a")\n    try:\n        fcntl.flock(runtime_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n    except BlockingIOError as exc:\n        runtime_lock.close()\n        raise ValueError("another ScopeX Runtime owns this data-root") from exc')
edit('scripts/runtime_api.py','        finalizer_max_tokens=args.finalizer_max_tokens,','        finalizer_max_tokens=args.finalizer_max_tokens,\n        report_max_tokens=args.report_max_tokens,')
edit('scripts/runtime_api.py','        finalizer_factory=factory.finalizer,','        finalizer_factory=factory.finalizer,\n        max_active_tasks=args.max_active_tasks,\n        max_queued_tasks=args.max_queued_tasks,\n        queue_timeout_s=args.queue_timeout,\n        reconcile_interrupted=True,')
edit('scripts/runtime_api.py','    print(f"view_image:', '    print(f"report: text-v2; active task slots={args.max_active_tasks}; queue={args.max_queued_tasks}; queue timeout={args.queue_timeout}s", flush=True)\n    print("Model requests may overlap; vLLM batching capacity must be verified separately.", flush=True)\n    print(f"view_image:')
edit('scripts/runtime_api.py','    uvicorn.run(app, host=args.host, port=args.port, log_level="info", access_log=False)','    try:\n        uvicorn.run(app, host=args.host, port=args.port, log_level="info", access_log=False)\n    finally:\n        runtime_lock.close()')
edit('frontend/src/types.ts','  duration_ms?: number | null','  duration_ms?: number | null\n  queue_wait_ms?: number | null\n  total_duration_ms?: number | null\n  queue_position?: number | null\n  latest_activity?: { type: string; at: string; tool: string; title: string } | null')
p=R/'frontend/src/types.ts';p.write_text(p.read_text()+'''\nexport interface ActivitySnapshot {
  tasks: TaskSnapshot[]
  max_active_tasks: number
  max_queued_tasks: number
  occupied_slots: number
  running_count: number
  queued_count: number
  paused_count: number
  scopex_commit: string | null
}
''')
edit('frontend/src/api.ts','  Evaluation,','  ActivitySnapshot,\n  Evaluation,')
edit('frontend/src/api.ts','export const api = {','export const api = {\n  activity: () => request<ActivitySnapshot>(\'/activity\'),\n  cancelQueued: (id: string) => request<TaskSnapshot>(`/tasks/${encodeURIComponent(id)}/cancel-queued`, { method: \'POST\', body: \'{}\' }),')
edit('frontend/src/App.vue',"import { RouterLink, RouterView } from 'vue-router'", "import { RouterLink, RouterView } from 'vue-router'\nimport ActivityPanel from './components/ActivityPanel.vue'")
edit('frontend/src/App.vue','''      <div class="topbar-meta">
        <span class="status-dot"></span>
        Local Runtime
      </div>''','''      <ActivityPanel />''')
edit('frontend/src/App.vue','      <RouterView />','      <RouterView :key="$route.fullPath" />')
edit('frontend/src/views/TaskView.vue',"const isRunning = computed", "const textReport = computed(() => {\n  const value = result.value?.result?.report_text\n  return typeof value === 'string' && value.trim() ? value : null\n})\nconst textMeta = computed(() => {\n  const value = result.value?.result?.report_meta\n  return value && typeof value === 'object' ? value as Record<string, unknown> : null\n})\nconst isTextResult = computed(() => result.value?.result?.version === 2)\nconst isQueued = computed(() => task.value?.state === 'QUEUED')\nconst isRunning = computed")
edit('frontend/src/views/TaskView.vue','    fresh_structured_finalizer_failed:', '''    text_report_partial: '调查已形成依据，但报告不完整；下方正文只能作为未完成草稿。',
    text_report_unavailable: '调查已形成依据，但文字报告未能生成；业务依据和执行记录已保留。',
    interrupted_on_restart: '服务重启中断了本次执行，没有自动重跑。',
    missed_on_restart: '排队任务在重启后已过期，没有补跑。',
    queue_expired: '超过排队等待期限，任务没有开始执行。',
    runtime_setup_failed: '任务运行环境准备失败，未完成调查。',
    fresh_structured_finalizer_failed:''')
edit('frontend/src/views/TaskView.vue','report.value || answer.value || conversationAnswer.value','textReport.value || report.value || answer.value || conversationAnswer.value')
edit('frontend/src/views/TaskView.vue',"const reason = lastTaskFailed.value?.data?.reason", "const reason = lastTaskFailed.value?.data?.reason || task.value?.last_reason")
edit('frontend/src/views/TaskView.vue','async function refresh() {\n  try {','let refreshing = false\nasync function refresh() {\n  if (refreshing) return\n  refreshing = true\n  try {')
edit('frontend/src/views/TaskView.vue','''    error.value = exc instanceof Error ? exc.message : String(exc)
  }
}

async function perform''','''    error.value = exc instanceof Error ? exc.message : String(exc)
  } finally {
    refreshing = false
  }
}

async function cancelQueued() {
  try {
    task.value = await api.cancelQueued(taskId.value)
    await refresh()
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc)
  }
}

async function perform''')
edit('frontend/src/views/TaskView.vue',"if (event.type === 'FINALIZATION_STARTED') return '正在校准事实与结论'", "if (event.type === 'FINALIZATION_STARTED') return '正在整理结果报告'")
edit('frontend/src/views/TaskView.vue',"if (event.type === 'FINALIZATION_COMPLETED') return '可信结论已生成'", "if (event.type === 'FINALIZATION_COMPLETED') return '结果报告已生成'")
edit('frontend/src/views/TaskView.vue','          <span>耗时：{{ durationText(task?.duration_ms) }}</span>','          <span>执行耗时：{{ durationText(task?.duration_ms) }}</span>\n          <span v-if="task?.queue_wait_ms != null">准入等待：{{ durationText(task.queue_wait_ms) }}</span>\n          <span v-if="task?.total_duration_ms != null">总耗时：{{ durationText(task.total_duration_ms) }}</span>')
edit('frontend/src/views/TaskView.vue','            <span v-if="report" class="trust-badge">Validated Report</span>','            <span v-if="textReport" class="trust-badge">{{ textMeta?.status === \'complete\' ? \'文字报告\' : \'未完成草稿\' }}</span>\n            <span v-else-if="report" class="trust-badge">历史结构化报告</span>')
edit('frontend/src/views/TaskView.vue','          <div v-if="conversationAnswer"', '''          <div v-if="textReport" class="text-report">
            <p v-if="textMeta?.status !== 'complete'" class="error-banner">报告不完整，不作为完整交付；已有业务依据保留。</p>
            <p v-if="Array.isArray(textMeta?.unresolved_citation_refs) && textMeta.unresolved_citation_refs.length" class="error-banner">部分正文引用无法对应来源，需要人工核实。</p>
            <div class="report-prose">{{ textReport }}</div>
            <p class="muted">来源可追溯不代表所有语义、数字和因果关系均已自动验证。</p>
          </div>
          <div v-else-if="conversationAnswer"''')
edit('frontend/src/views/TaskView.vue','          <div v-else class="empty-state">执行结束后显示结果。</div>', '          <div v-else class="empty-state">{{ isQueued ? \'已进入等待队列，尚未开始分析。\' : \'执行结束后显示结果。\' }}</div>')
edit('frontend/src/views/TaskView.vue','            <button v-if="isRunning" class="secondary-button"', '            <button v-if="isQueued" class="danger-button" @click="cancelQueued">取消排队</button>\n            <button v-if="isRunning" class="secondary-button"')
edit('frontend/src/views/TaskView.vue',"@click=\"perform('stop')\">Stop", "@click=\"perform('stop')\">暂停")
edit('frontend/src/views/TaskView.vue','          <p class="muted">Stop 在安全模型请求边界生效；已完成工具结果会保留。</p>','          <p class="muted">暂停在安全模型请求边界生效；结果会保留。当前暂停任务仍保留执行名额。</p>')
edit('frontend/src/views/TaskView.vue','            <template v-if="report">','''            <template v-if="isTextResult">
              <p class="muted">以下是本次实际取得的依据，不是模型逐句校验结论。</p>
              <details v-for="item in fallbackUserFacts" :key="item.ref" class="evidence-card">
                <summary>{{ item.ref }} · {{ item.source }}</summary>
                <pre class="result-text">{{ item.raw }}</pre>
              </details>
            </template>
            <template v-else-if="report">''')
p=R/'frontend/src/styles.css';p.write_text(p.read_text()+'''\n.report-prose { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.85; font-size: 15px; }
.state-pill[data-state="QUEUED"] { background: #fff4dc; color: #855d14; }
.activity-trigger { border: 1px solid #c9d7e6; border-radius: 9px; padding: 8px 12px; background: #fff; cursor: pointer; color: #173954; }
.activity-backdrop { position: fixed; inset: 0; background: #11263866; z-index: 80; }
.activity-drawer { position: fixed; right: 0; top: 0; bottom: 0; width: min(440px, 96vw); background: #f5f8fb; z-index: 81; padding: 24px; overflow: auto; box-shadow: -12px 0 40px #152c4826; }
.activity-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.activity-card { background: white; border: 1px solid #dce4ee; border-radius: 12px; padding: 16px; margin: 12px 0; }
.activity-card a { display: block; font-weight: 650; color: #17476b; overflow-wrap: anywhere; }
.activity-card p { font-size: 13px; margin: 8px 0; }
.activity-actions { display: flex; gap: 8px; margin-top: 12px; }
''')
