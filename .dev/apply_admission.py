from pathlib import Path
R=Path('.')
def edit(path,old,new):
 p=R/path;s=p.read_text(); assert old in s,(path,old[:80]);p.write_text(s.replace(old,new))
edit('scopex/runtime/task.py','    CREATED = "CREATED"','    CREATED = "CREATED"\n    QUEUED = "QUEUED"')
edit('scopex/runtime/task.py','    TaskState.CREATED: {TaskState.RUNNING, TaskState.CANCELLED, TaskState.FAILED},','    TaskState.CREATED: {TaskState.QUEUED, TaskState.RUNNING, TaskState.CANCELLED, TaskState.FAILED},\n    TaskState.QUEUED: {TaskState.CREATED, TaskState.CANCELLED, TaskState.FAILED},')
edit('scopex/runtime/task.py','            "duration_ms": self.duration_ms,','            "duration_ms": self.duration_ms,\n            "queue_wait_ms": _duration_ms(self.created_at, self.metadata.get("admitted_at")),\n            "total_duration_ms": _duration_ms(self.created_at, self.finished_at),')
edit('scopex/events/progress.py','    TASK_CREATED = "TASK_CREATED"','    TASK_CREATED = "TASK_CREATED"\n    TASK_QUEUED = "TASK_QUEUED"')
edit('scopex/api/service.py','from dataclasses import dataclass, field','from dataclasses import dataclass, field\nfrom collections import deque')
edit('scopex/api/service.py','from scopex.agent.trace import load_audit_trace','from scopex.agent.trace import load_audit_trace\nfrom scopex.evidence.catalog import EvidenceCatalog')
edit('scopex/api/service.py','    coordinator: InvestigationCoordinator','    coordinator: InvestigationCoordinator | None')
edit('scopex/api/service.py','    turn_index: int = 0','    turn_index: int = 0\n    queued_at: float | None = None\n    cleaned: bool = False')
edit('scopex/api/service.py','        export_root: Path | None = None,','        export_root: Path | None = None,\n        max_active_tasks: int = 1,\n        max_queued_tasks: int = 0,\n        queue_timeout_s: float = 600.0,\n        reconcile_interrupted: bool = False,')
edit('scopex/api/service.py','        self.store = AuditStore(Path(audit_root))','        if isinstance(max_active_tasks, bool) or not isinstance(max_active_tasks, int) or not 1 <= max_active_tasks <= 4:\n            raise ValueError("max_active_tasks must be 1..4")\n        if isinstance(max_queued_tasks, bool) or not isinstance(max_queued_tasks, int) or not 0 <= max_queued_tasks <= 64:\n            raise ValueError("max_queued_tasks must be 0..64")\n        if not 1 <= queue_timeout_s <= 3600:\n            raise ValueError("queue_timeout_s must be 1..3600")\n        self.store = AuditStore(Path(audit_root))')
edit('scopex/api/service.py','''        self._active_task_id: str | None = None

    @property
    def active_task_id(self) -> str | None:
        with self._lock:
            self._release_terminal_active_locked()
            return self._active_task_id''','''        self.max_active_tasks = max_active_tasks
        self.max_queued_tasks = max_queued_tasks
        self.queue_timeout_s = queue_timeout_s
        self._active_ids: set[str] = set()
        self._queue: deque[str] = deque()
        self._shutdown_event = threading.Event()
        self._build_commit = self._git_head()
        if reconcile_interrupted:
            self._reconcile_interrupted()
        self._queue_thread: threading.Thread | None = None
        if max_queued_tasks:
            self._queue_thread = threading.Thread(target=self._queue_watch, name="scopex-admission", daemon=True)
            self._queue_thread.start()

    @property
    def active_task_id(self) -> str | None:
        # Compatibility only. Product clients must use /activity for the full set.
        with self._lock:
            return next((key for key in sorted(self._active_ids)
                         if not self._handles[key].task.terminal), None)''')
edit('scopex/api/service.py','''            self._release_terminal_active_locked()
            if self._active_task_id is not None:
                active = self._handles.get(self._active_task_id)
                state = active.task.state.value if active is not None else "UNKNOWN"
                raise TaskBusyError(f"active task {self._active_task_id} is {state}")

            task_id''','''            if self._shutdown_event.is_set():
                raise TaskBusyError("service is shutting down")
            self._dispatch_locked()
            if schedule_id and any(not h.task.terminal and h.task.schedule_id == schedule_id
                                   for h in self._handles.values()):
                raise TaskBusyError("same schedule already has an active or queued run")
            full = len(self._active_ids) >= self.max_active_tasks
            if full and len(self._queue) >= self.max_queued_tasks:
                raise TaskBusyError("execution capacity and bounded queue are full")
            if agent_id and any((not h.task.terminal or h.worker_alive)
                                and h.task.metadata.get("agent_id") == agent_id
                                for h in self._handles.values()):
                raise TaskConflictError("same agent/session cannot run concurrently")

            task_id''')
edit('scopex/api/service.py','            task_metadata = {"agent_id": resolved_agent_id}','            task_metadata = {"agent_id": resolved_agent_id, "scopex_commit_at_start": self._build_commit,\n                             "max_active_tasks": self.max_active_tasks}')
edit('scopex/api/service.py','''            coordinator = self.coordinator_factory(task, session, events, audit)
            handle = TaskHandle(task, session, coordinator, audit, memory_events)
            self._handles[task_id] = handle
            self._active_task_id = task_id
            audit.snapshot_control(task, session, coordinator.catalog)
            self._start_worker_locked(handle, self._run_initial, "initial")
            return task.snapshot()''','''            # Queued tasks have no Runtime, sandbox or host snapshot yet.
            handle = TaskHandle(task, session, None, audit, memory_events)
            self._handles[task_id] = handle
            audit.snapshot_control(task, session, EvidenceCatalog(task.id, task.session_key))
            if full:
                task.transition(TaskState.QUEUED, reason="execution_capacity")
                handle.queued_at = time.monotonic()
                self._queue.append(task_id)
                events.emit(task.id, "TASK_QUEUED")
                audit.persist_task(task)
            else:
                self._launch_locked(handle)
            return task.snapshot()''')
edit('scopex/api/service.py','            if self._active_task_id == task_id:\n                self._active_task_id = None','            self._active_ids.discard(task_id)')
edit('scopex/api/service.py','            "worker-error.json", "cleanup.json",','            "worker-error.json", "cleanup.json", "report.md", "report-meta.json",\n            "report.json", "report-error.json",')
edit('scopex/api/service.py','            "scopex_commit": self._git_head(),','            "scopex_commit": (task.get("metadata") or {}).get("scopex_commit_at_start"),\n            "export_commit": self._git_head(),')
edit('scopex/api/service.py','        turn = handle.coordinator.start(handle.task.user_request, turn_name=handle.next_turn_name())','        if handle.coordinator is None:\n            events = AuditEventSink(self.store, downstream=handle.memory_events)\n            handle.coordinator = self.coordinator_factory(handle.task, handle.session, events, handle.audit)\n        turn = handle.coordinator.start(handle.task.user_request, turn_name=handle.next_turn_name())')
edit('scopex/api/service.py','        deadline = time.monotonic() + timeout_s\n        with self._lock:', '        deadline = time.monotonic() + timeout_s\n        self._shutdown_event.set()\n        if self._queue_thread is not None:\n            self._queue_thread.join(timeout=max(0.0, deadline - time.monotonic()))\n        with self._lock:\n            for task_id in list(self._queue):\n                self._cancel_unstarted_locked(self._handles[task_id], "server_shutdown")\n            self._queue.clear()')
edit('scopex/api/service.py','                if not handle.task.terminal:\n                    handle.coordinator.controller.cancel("server_shutdown")','                if not handle.task.terminal and handle.coordinator is not None:\n                    handle.coordinator.controller.cancel("server_shutdown")')
edit('scopex/api/service.py','''                if not handle.task.terminal:
                    handle.coordinator.controller.fail("runtime_api_worker_exception")
                handle.audit.store.write_json(handle.task.id, "worker-error.json", {
                    "type": type(exc).__name__, "message": str(exc)[:800],
                })
                handle.audit.snapshot_control(handle.task, handle.session, handle.coordinator.catalog)
            finally:
                with self._lock:
                    if handle.task.terminal:
                        self._cleanup_terminal_locked(handle)''','''                if not handle.task.terminal:
                    if handle.coordinator is not None:
                        handle.coordinator.controller.fail("runtime_api_worker_exception")
                    else:
                        handle.task.transition(TaskState.FAILED, reason="runtime_setup_failed")
                handle.audit.store.write_json(handle.task.id, "worker-error.json", {
                    "type": type(exc).__name__, "message": str(exc)[:800],
                })
                handle.audit.persist_task(handle.task)
            finally:
                # Cleanup outside admission lock; never block other tasks' model/tool progress.
                if handle.task.terminal:
                    self._cleanup_terminal_locked(handle)
                with self._lock:
                    if handle.task.terminal:
                        self._active_ids.discard(handle.task.id)
                    self._dispatch_locked()''')
edit('scopex/api/service.py','''    def _cleanup_terminal_locked(self, handle: TaskHandle) -> None:
        try:
            cleanup = handle.coordinator.close()''','''    def _cleanup_terminal_locked(self, handle: TaskHandle) -> None:
        if handle.cleaned:
            return
        handle.cleaned = True
        if handle.coordinator is None:
            return
        try:
            cleanup = handle.coordinator.close()''')
edit('scopex/api/service.py','''        if self._active_task_id == handle.task.id:
            self._active_task_id = None

    def _release_terminal_active_locked(self) -> None:
        if self._active_task_id is None:
            return
        handle = self._handles.get(self._active_task_id)
        if handle is None or handle.task.terminal:
            self._active_task_id = None

''','')
p=R/'scopex/api/service.py';s=p.read_text();pos=s.index('    def _live_handle(')
s=s[:pos]+'''    def _launch_locked(self, handle: TaskHandle) -> None:
        if handle.task.state is TaskState.QUEUED:
            handle.task.transition(TaskState.CREATED, reason="admitted")
        handle.task.metadata["admitted_at"] = datetime.now(timezone.utc).isoformat()
        handle.audit.persist_task(handle.task)
        self._active_ids.add(handle.task.id)
        try:
            self._start_worker_locked(handle, self._run_initial, "initial")
        except Exception:
            self._active_ids.discard(handle.task.id)
            handle.task.transition(TaskState.FAILED, reason="worker_start_failed")
            handle.audit.persist_task(handle.task)
            raise

    def _dispatch_locked(self) -> None:
        if self._shutdown_event.is_set():
            return
        now = time.monotonic()
        for task_id in tuple(self._queue):
            handle = self._handles[task_id]
            if handle.queued_at is not None and now - handle.queued_at >= self.queue_timeout_s:
                self._queue.remove(task_id)
                self._cancel_unstarted_locked(handle, "queue_expired")
        while self._queue and len(self._active_ids) < self.max_active_tasks:
            task_id = self._queue.popleft()
            handle = self._handles[task_id]
            if not handle.task.terminal:
                self._launch_locked(handle)

    def _queue_watch(self) -> None:
        while not self._shutdown_event.wait(1.0):
            with self._lock:
                self._dispatch_locked()

    def _cancel_unstarted_locked(self, handle: TaskHandle, reason: str) -> None:
        handle.task.transition(TaskState.CANCELLED, reason=reason)
        handle.audit.persist_task(handle.task)
        AuditEventSink(self.store, downstream=handle.memory_events).emit(
            handle.task.id, "TASK_CANCELLED", reason=reason)

    def cancel_queued(self, task_id: str) -> dict:
        with self._lock:
            handle = self._live_handle(task_id)
            if handle.task.state is not TaskState.QUEUED:
                raise TaskConflictError("only queued, unstarted tasks can use cancel-queued")
            self._queue.remove(task_id)
            self._cancel_unstarted_locked(handle, "user_cancelled_queue")
            return handle.task.snapshot()

    def activity(self) -> dict:
        """Global bounded live metadata, independent of date and business data."""
        with self._lock:
            positions = {key: i + 1 for i, key in enumerate(self._queue)}
            rows = []
            for handle in self._handles.values():
                if handle.task.terminal:
                    continue
                row = handle.task.snapshot()
                row["queue_position"] = positions.get(handle.task.id)
                events = handle.memory_events.events
                last = next((x for x in reversed(events) if x.type.value in {
                    "MODEL_REQUEST", "TOOL_CALL", "TOOL_RESULT", "FINALIZATION_STARTED"}), None)
                row["latest_activity"] = None if last is None else {
                    "type": last.type.value, "at": last.created_at,
                    "tool": str(last.data.get("tool", ""))[:60],
                    "title": str(last.data.get("title", ""))[:160],
                }
                rows.append(row)
            rows.sort(key=lambda x: (x["state"] == "QUEUED", x["created_at"]))
            return {
                "tasks": rows, "max_active_tasks": self.max_active_tasks,
                "max_queued_tasks": self.max_queued_tasks,
                "occupied_slots": len(self._active_ids),
                "running_count": sum(x["state"] not in {"QUEUED", "PAUSED"} for x in rows),
                "queued_count": len(self._queue),
                "paused_count": sum(x["state"] == "PAUSED" for x in rows),
                "scopex_commit": self._build_commit,
            }

    def _reconcile_interrupted(self) -> None:
        # Called only by the single-owner product server. Never execute, replay or
        # clean arbitrary old containers on startup. Historical evidence is intact.
        for task_id in self.store.list_task_ids():
            try:
                row = self.store.read_json(task_id, "task.json")
            except (OSError, ValueError):
                continue
            if not isinstance(row, dict) or row.get("state") not in {"CREATED", "QUEUED", "RUNNING", "PAUSING", "PAUSED", "FINALIZING"}:
                continue
            old = row["state"]
            row.update(state="CANCELLED" if old == "QUEUED" else "FAILED",
                       last_reason="missed_on_restart" if old == "QUEUED" else "interrupted_on_restart",
                       finished_at=datetime.now(timezone.utc).isoformat())
            row["updated_at"] = row["finished_at"]
            row["duration_ms"] = None  # Actual interruption time is unknown.
            self.store.write_json(task_id, "task.json", row)

'''+s[pos:]
s=s.replace('                    self._cleanup_terminal_locked(handle)\n\n    def _run_initial','                    self._cleanup_terminal_locked(handle)\n                    self._active_ids.discard(handle.task.id)\n\n    def _run_initial')
s=s.replace('{"CREATED", "RUNNING", "PAUSING", "PAUSED", "FINALIZING"}', '{"CREATED", "QUEUED", "RUNNING", "PAUSING", "PAUSED", "FINALIZING"}')
p.write_text(s)
