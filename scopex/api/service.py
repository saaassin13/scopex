from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid
from typing import Callable, Protocol
import zipfile

from scopex.agent.runtime import OpenClawTurnResult
from scopex.agent.trace import load_audit_trace
from scopex.events.progress import EventSink, InMemoryEventSink
from scopex.finalizer.structured import StructuredFinalizer
from scopex.runtime.investigation import InvestigationCoordinator
from scopex.runtime.session import Session
from scopex.runtime.task import Task, TaskState
from scopex.storage.audit import AuditStore
from scopex.storage.runtime_audit import AuditEventSink, RuntimeAudit


class TaskNotFoundError(KeyError):
    pass


class TaskBusyError(RuntimeError):
    pass


class TaskConflictError(RuntimeError):
    pass


class CoordinatorFactory(Protocol):
    def __call__(
        self,
        task: Task,
        session: Session,
        events: EventSink,
        audit: RuntimeAudit,
    ) -> InvestigationCoordinator: ...


FinalizerFactory = Callable[[], StructuredFinalizer]

_BUSINESS_PATH_PREFIXES = ("/agent-data/", "/scopex-host/")
_BUSINESS_COMMAND_MARKERS = (
    "/agent-data/",
    "/scopex-host/",
    "data_locator.py",
    "encoder_health.py",
    "nipple_stats.py",
    "image_quality_metrics.py",
    "log_context.py",
)


@dataclass(slots=True)
class TaskHandle:
    task: Task
    session: Session
    coordinator: InvestigationCoordinator
    audit: RuntimeAudit
    memory_events: InMemoryEventSink
    thread: threading.Thread | None = None
    turn_index: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock)

    def next_turn_name(self) -> str:
        with self.lock:
            self.turn_index += 1
            return f"turn-{self.turn_index:03d}"

    @property
    def worker_alive(self) -> bool:
        thread = self.thread
        return bool(thread is not None and thread.is_alive())


class TaskService:
    """One local execution service for manual, conversational and scheduled runs.

    The UI can create a manual ``auto`` run without asking the user to classify
    their request. ScopeX does not call a separate router model. A normal turn
    that never touches business data/capabilities is published as conversation;
    once the turn actually touches business data/capabilities it must satisfy the
    audited Task result boundary.
    """

    def __init__(
        self,
        *,
        audit_root: Path,
        coordinator_factory: CoordinatorFactory,
        finalizer_factory: FinalizerFactory,
        work_root: Path | None = None,
        export_root: Path | None = None,
    ) -> None:
        self.store = AuditStore(Path(audit_root))
        self.work_root = Path(work_root) if work_root is not None else self.store.root.parent / "work"
        self.export_root = Path(export_root) if export_root is not None else self.store.root.parent / "exports"
        self.coordinator_factory = coordinator_factory
        self.finalizer_factory = finalizer_factory
        self._lock = threading.RLock()
        self._handles: dict[str, TaskHandle] = {}
        self._active_task_id: str | None = None

    @property
    def active_task_id(self) -> str | None:
        with self._lock:
            self._release_terminal_active_locked()
            return self._active_task_id

    def create_task(
        self,
        message: str,
        *,
        mode: str = "task",
        trigger_type: str = "manual",
        schedule_id: str | None = None,
        scheduled_for: str | None = None,
        agent_id: str | None = None,
        session_key: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        message = self._message(message)
        if mode not in {"task", "conversation", "auto"}:
            raise ValueError("mode must be task, conversation or auto")
        if trigger_type not in {"manual", "schedule"}:
            raise ValueError("trigger_type must be manual or schedule")
        if trigger_type == "schedule" and mode != "task":
            raise ValueError("scheduled runs must use audited task mode")
        with self._lock:
            self._release_terminal_active_locked()
            if self._active_task_id is not None:
                active = self._handles.get(self._active_task_id)
                state = active.task.state.value if active is not None else "UNKNOWN"
                raise TaskBusyError(f"active task {self._active_task_id} is {state}")

            task_id = "task-" + uuid.uuid4().hex[:12]
            resolved_agent_id = agent_id or ("sxapi" + uuid.uuid4().hex[:8])
            resolved_session_key = session_key or f"agent:{resolved_agent_id}:{task_id}"
            if not resolved_session_key.startswith(f"agent:{resolved_agent_id}:"):
                raise ValueError("session_key does not belong to agent_id")
            task_metadata = {"agent_id": resolved_agent_id}
            if metadata:
                task_metadata.update(metadata)
            if mode == "conversation" and not task_metadata.get("conversation_id"):
                task_metadata["conversation_id"] = task_id

            task = Task(
                task_id,
                message,
                resolved_session_key,
                metadata=task_metadata,
                mode=mode,
                trigger_type=trigger_type,
                schedule_id=schedule_id,
                scheduled_for=scheduled_for,
            )
            session = Session(task_id, resolved_session_key)
            memory_events = InMemoryEventSink()
            events = AuditEventSink(self.store, downstream=memory_events)
            audit = RuntimeAudit(self.store, task_id)
            coordinator = self.coordinator_factory(task, session, events, audit)
            handle = TaskHandle(task, session, coordinator, audit, memory_events)
            self._handles[task_id] = handle
            self._active_task_id = task_id
            audit.snapshot_control(task, session, coordinator.catalog)
            self._start_worker_locked(handle, self._run_initial, "initial")
            return task.snapshot()

    def create_auto_run(self, message: str) -> dict:
        return self.create_task(message, mode="auto", trigger_type="manual")

    def continue_conversation(self, task_id: str, message: str) -> dict:
        parent = self.get_task(task_id)
        if parent.get("mode") != "conversation":
            raise TaskConflictError("conversation continuation requires a conversation task")
        if parent.get("state") not in {"COMPLETED", "FAILED", "CANCELLED"}:
            raise TaskConflictError("conversation turn must be terminal before continuing")
        metadata = parent.get("metadata") or {}
        agent_id = metadata.get("agent_id")
        session_key = parent.get("session_key")
        if not isinstance(agent_id, str) or not agent_id:
            raise TaskConflictError("conversation task is missing agent_id")
        if not isinstance(session_key, str) or not session_key:
            raise TaskConflictError("conversation task is missing session_key")
        conversation_id = metadata.get("conversation_id") or task_id
        return self.create_task(
            message,
            mode="conversation",
            trigger_type="manual",
            agent_id=agent_id,
            session_key=session_key,
            metadata={
                "conversation_id": conversation_id,
                "parent_task_id": task_id,
            },
        )

    def stop(self, task_id: str, message: str = "") -> dict:
        handle = self._live_handle(task_id)
        with self._lock:
            if handle.task.state is not TaskState.RUNNING:
                raise TaskConflictError(f"stop requires RUNNING task, got {handle.task.state.value}")
            handle.coordinator.request_stop(message)
            return handle.task.snapshot()

    def steer(self, task_id: str, message: str) -> dict:
        message = self._message(message)
        handle = self._live_handle(task_id)
        with self._lock:
            if handle.task.state is not TaskState.RUNNING:
                raise TaskConflictError(f"steer requires RUNNING task, got {handle.task.state.value}")
            handle.coordinator.request_steer(message)
            return handle.task.snapshot()

    def resume(self, task_id: str, message: str) -> dict:
        message = self._message(message)
        handle = self._live_handle(task_id)
        with self._lock:
            if handle.task.state is not TaskState.PAUSED:
                raise TaskConflictError(f"resume requires PAUSED task, got {handle.task.state.value}")
            if handle.worker_alive:
                raise TaskConflictError("stopped turn is still unwinding; retry resume shortly")
            self._start_worker_locked(handle, self._run_resume, message)
            return handle.task.snapshot()

    def get_task(self, task_id: str) -> dict:
        with self._lock:
            handle = self._handles.get(task_id)
            if handle is not None:
                return handle.task.snapshot()
        try:
            return self.store.read_json(task_id, "task.json")
        except (FileNotFoundError, OSError):
            raise TaskNotFoundError(task_id) from None

    def list_tasks(self, *, mode: str | None = None, day: str | None = None) -> list[dict]:
        if mode is not None and mode not in {"task", "conversation", "auto"}:
            raise ValueError("mode must be task, conversation or auto")
        day_value = self._parse_day(day) if day is not None else None
        tasks: list[dict] = []
        for stored_task_id in self.store.list_task_ids():
            try:
                row = self.get_task(stored_task_id)
                if mode is not None and row.get("mode", "task") != mode:
                    continue
                if day_value is not None and self._task_local_day(row) != day_value:
                    continue
                tasks.append(row)
            except (TaskNotFoundError, json.JSONDecodeError, OSError):
                continue
        tasks.sort(key=lambda row: str(row.get("started_at") or row.get("created_at") or ""), reverse=True)
        return tasks

    def calendar_month(self, month: str) -> dict:
        try:
            month_start = datetime.strptime(month, "%Y-%m")
        except ValueError as exc:
            raise ValueError("month must use YYYY-MM") from exc
        days: dict[str, dict] = {}
        for row in self.list_tasks():
            local_day = self._task_local_day(row)
            if local_day is None or not local_day.startswith(month_start.strftime("%Y-%m-")):
                continue
            slot = days.setdefault(local_day, {
                "date": local_day,
                "count": 0,
                "completed": 0,
                "failed": 0,
                "running": 0,
                "scheduled": 0,
                "manual": 0,
            })
            slot["count"] += 1
            state = row.get("state")
            if state == "COMPLETED":
                slot["completed"] += 1
            elif state in {"FAILED", "CANCELLED"}:
                slot["failed"] += 1
            elif state in {"CREATED", "RUNNING", "PAUSING", "PAUSED", "FINALIZING"}:
                slot["running"] += 1
            if row.get("trigger_type") == "schedule":
                slot["scheduled"] += 1
            else:
                slot["manual"] += 1
        return {"month": month, "days": [days[key] for key in sorted(days)]}

    def delete_task(self, task_id: str) -> dict:
        task = self.get_task(task_id)
        if task.get("state") not in {"COMPLETED", "FAILED", "CANCELLED"}:
            raise TaskConflictError("only terminal tasks can be deleted")
        with self._lock:
            handle = self._handles.get(task_id)
            if handle is not None and handle.worker_alive:
                raise TaskConflictError("task cleanup is still running; retry deletion shortly")
            if self._active_task_id == task_id:
                self._active_task_id = None
            self._handles.pop(task_id, None)

        task_dir = self.store.existing_task_dir(task_id)
        work_dir = self._safe_child(self.work_root, task_id)
        export_file = self._safe_child(self.export_root, f"scopex-review-{task_id}.zip")
        removed = {"audit": False, "work": False, "review_export": False}
        if work_dir.is_dir():
            shutil.rmtree(work_dir)
            removed["work"] = True
        if export_file.is_file():
            export_file.unlink()
            removed["review_export"] = True
        shutil.rmtree(task_dir)
        removed["audit"] = True
        return {
            "task_id": task_id,
            "deleted": True,
            "removed": removed,
            "external_business_data_deleted": False,
        }

    def get_events(self, task_id: str, *, after: int = 0) -> list[dict]:
        if after < 0:
            raise ValueError("after must be non-negative")
        self._require_task(task_id)
        try:
            rows = self.store.read_jsonl(task_id, "events.jsonl")
        except FileNotFoundError:
            return []
        return [
            row for row in rows
            if isinstance(row, dict)
            and isinstance(row.get("seq"), int)
            and row["seq"] > after
        ]

    def get_evidence(self, task_id: str) -> dict:
        self._require_task(task_id)
        try:
            return self.store.read_json(task_id, "evidence.json")
        except FileNotFoundError:
            return {"task_id": task_id, "items": []}

    def get_result(self, task_id: str) -> dict:
        task = self.get_task(task_id)
        try:
            result = self.store.read_json(task_id, "result.json")
        except FileNotFoundError:
            return {"task_id": task_id, "state": task.get("state"), "available": False}
        rendered = None
        try:
            rendered = self.store.read_text(task_id, "final.txt")
        except FileNotFoundError:
            pass
        return {
            "task_id": task_id,
            "state": task.get("state"),
            "available": True,
            "result": result,
            "rendered": rendered,
        }

    def get_evaluation(self, task_id: str) -> dict | None:
        self._require_task(task_id)
        try:
            value = self.store.read_json(task_id, "evaluation.json")
        except FileNotFoundError:
            return None
        return value if isinstance(value, dict) else None

    def set_evaluation(
        self,
        task_id: str,
        *,
        rating: str,
        tags: list[str] | None = None,
        note: str = "",
    ) -> dict:
        self._require_task(task_id)
        if rating not in {"up", "down"}:
            raise ValueError("rating must be up or down")
        allowed_tags = {
            "wrong_result", "incomplete", "scope_too_broad", "too_slow",
            "wrong_skill", "tool_failed", "hard_to_read", "insufficient_evidence", "other",
        }
        clean_tags = []
        for tag in tags or []:
            if tag not in allowed_tags:
                raise ValueError(f"unsupported evaluation tag: {tag}")
            if tag not in clean_tags:
                clean_tags.append(tag)
        note = note.strip()
        if len(note) > 4000:
            raise ValueError("evaluation note exceeds 4000 characters")
        payload = {
            "task_id": task_id,
            "rating": rating,
            "tags": clean_tags,
            "note": note,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.store.write_json(task_id, "evaluation.json", payload)
        return payload

    def export_review_bundle(self, task_id: str) -> Path:
        task_dir = self.store.existing_task_dir(task_id)
        self.export_root.mkdir(parents=True, exist_ok=True)
        target = self.export_root / f"scopex-review-{task_id}.zip"
        allow = (
            "task.json", "session.json", "result.json", "answer.json", "claims.json",
            "evidence.json", "events.jsonl", "final.txt", "evaluation.json",
            "runtime-limit.json", "runtime-guard.json", "investigation-error.json",
            "worker-error.json", "cleanup.json",
        )
        included = [name for name in allow if (task_dir / name).is_file()]
        runtime_context = self._review_runtime_context(task_dir)
        manifest = {
            "schema": 1,
            "task_id": task_id,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "purpose": "offline/larger-model review of ScopeX task quality",
            "included": included,
            "runtime_context": runtime_context,
            "raw_external_business_files_included": False,
            "review_instruction": (
                "Review why ScopeX produced this result. Separate Model, Skill, Tool, Runtime, "
                "Evidence, Finalizer and UI issues. Do not silently redo the business diagnosis."
            ),
        }
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            for name in included:
                archive.write(task_dir / name, arcname=name)
        return target

    def _review_runtime_context(self, task_dir: Path) -> dict:
        task = self.store.read_json(task_dir.name, "task.json")
        context = {
            "scopex_commit": self._git_head(),
            "model_id": None,
            "agent_id": (task.get("metadata") or {}).get("agent_id") if isinstance(task, dict) else None,
            "mode": task.get("mode") if isinstance(task, dict) else None,
            "trigger_type": task.get("trigger_type") if isinstance(task, dict) else None,
        }
        try:
            session = self.store.read_json(task_dir.name, "session.json")
            if isinstance(session, dict):
                context["session_key"] = session.get("key") or session.get("session_key")
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            pass
        return context

    @staticmethod
    def _git_head() -> str | None:
        root = Path(__file__).resolve().parents[2]
        try:
            proc = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        value = proc.stdout.strip()
        return value if proc.returncode == 0 and value else None

    def shutdown(self, timeout_s: float = 10.0) -> None:
        timeout_s = max(0.0, float(timeout_s))
        deadline = time.monotonic() + timeout_s
        with self._lock:
            handles = list(self._handles.values())
            for handle in handles:
                if handle.task.state is TaskState.RUNNING:
                    try:
                        handle.coordinator.request_stop("server_shutdown")
                    except Exception:
                        pass
        for handle in handles:
            thread = handle.thread
            if thread is None or not thread.is_alive():
                continue
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        with self._lock:
            for handle in handles:
                if handle.worker_alive:
                    continue
                if not handle.task.terminal:
                    handle.coordinator.controller.cancel("server_shutdown")
                    handle.audit.snapshot_control(handle.task, handle.session, handle.coordinator.catalog)
                    self._cleanup_terminal_locked(handle)

    def _run_initial(self, handle: TaskHandle, _unused: str) -> None:
        turn = handle.coordinator.start(handle.task.user_request, turn_name=handle.next_turn_name())
        self._drive_after_turn(handle, turn)

    def _run_resume(self, handle: TaskHandle, message: str) -> None:
        turn = handle.coordinator.resume(message, turn_name=handle.next_turn_name())
        self._drive_after_turn(handle, turn)

    def _drive_after_turn(self, handle: TaskHandle, turn: OpenClawTurnResult) -> None:
        coordinator = handle.coordinator
        current_turn = turn
        while True:
            action = None
            turn_name = None
            with self._lock:
                state = coordinator.controller.state
                if state is TaskState.PAUSING:
                    coordinator.controller.safe_stop(at_turn_end=True, running_tool_cancelled=False)
                    handle.audit.snapshot_control(handle.task, handle.session, coordinator.catalog)
                    return
                if state is TaskState.PAUSED:
                    return
                if state is TaskState.RUNNING and coordinator.steering.pending:
                    action = "steer"
                    turn_name = handle.next_turn_name()
                elif state is TaskState.RUNNING:
                    runtime_limit_reason = current_turn.runtime_limit_reason
                    runtime_guard_reason = current_turn.runtime_guard_reason
                    if runtime_limit_reason is not None:
                        handle.audit.store.write_json(handle.task.id, "runtime-limit.json", {
                            "reason": runtime_limit_reason,
                            "turn_name": current_turn.turn_name,
                            "returncode": current_turn.process.returncode,
                            "stop_reason": current_turn.process.stop_reason,
                            "forwarded_model_requests": sum(1 for row in current_turn.proxy_records if row.get("forwarded") is True),
                        })
                        if coordinator.catalog.items:
                            self._resolve_auto_as_task(handle)
                            coordinator.begin_runtime_limit_finalization(runtime_limit_reason)
                            action = "finalize"
                        else:
                            self._resolve_auto_as_task(handle)
                            coordinator.controller.fail("budget_reached_without_evidence")
                            handle.audit.snapshot_control(handle.task, handle.session, coordinator.catalog)
                            return
                    elif runtime_guard_reason is not None:
                        handle.audit.store.write_json(handle.task.id, "runtime-guard.json", {
                            "reason": runtime_guard_reason,
                            "turn_name": current_turn.turn_name,
                            "returncode": current_turn.process.returncode,
                            "stop_reason": current_turn.process.stop_reason,
                            "cli_blockers": list(current_turn.cli_outcome.blockers) if current_turn.cli_outcome is not None else ["missing_cli_outcome"],
                            "cli_flags": dict(current_turn.cli_outcome.flags) if current_turn.cli_outcome is not None else {},
                            "forwarded_model_requests": sum(1 for row in current_turn.proxy_records if row.get("forwarded") is True),
                        })
                        if coordinator.catalog.items:
                            self._resolve_auto_as_task(handle)
                            coordinator.begin_runtime_guard_finalization(runtime_guard_reason)
                            action = "finalize"
                        else:
                            self._resolve_auto_as_task(handle)
                            coordinator.controller.fail("runtime_guard_reached_without_evidence")
                            handle.audit.snapshot_control(handle.task, handle.session, coordinator.catalog)
                            return
                    elif handle.task.mode == "auto":
                        business_attempt = self._turn_attempted_business_work(current_turn)
                        business_evidence = self._has_business_evidence(coordinator.catalog.items)
                        if business_evidence:
                            self._resolve_auto_as_task(handle)
                            if self._turn_completed_normally(current_turn):
                                coordinator.begin_finalization(goal_satisfied=True)
                                action = "finalize"
                            else:
                                decision = coordinator.convergence(goal_satisfied=False)
                                if decision.should_finalize:
                                    coordinator.begin_finalization(goal_satisfied=False)
                                    action = "finalize"
                                else:
                                    self._fail_incomplete_turn(handle, current_turn)
                                    return
                        elif business_attempt:
                            self._resolve_auto_as_task(handle)
                            coordinator.controller.fail("investigation_completed_without_business_evidence")
                            handle.audit.snapshot_control(handle.task, handle.session, coordinator.catalog)
                            return
                        elif self._turn_completed_normally(current_turn):
                            self._resolve_auto_as_conversation(handle)
                            self._complete_conversation(handle, current_turn)
                            return
                        else:
                            self._fail_incomplete_turn(handle, current_turn)
                            return
                    elif handle.task.mode == "conversation" and self._turn_completed_normally(current_turn):
                        self._complete_conversation(handle, current_turn)
                        return
                    elif not coordinator.catalog.items:
                        coordinator.controller.fail("investigation_completed_without_evidence")
                        handle.audit.snapshot_control(handle.task, handle.session, coordinator.catalog)
                        return
                    elif self._turn_completed_normally(current_turn):
                        coordinator.begin_finalization(goal_satisfied=True)
                        action = "finalize"
                    else:
                        decision = coordinator.convergence(goal_satisfied=False)
                        if decision.should_finalize:
                            coordinator.begin_finalization(goal_satisfied=False)
                            action = "finalize"
                        else:
                            self._fail_incomplete_turn(handle, current_turn)
                            return
                else:
                    return

            if action == "steer":
                try:
                    current_turn = coordinator.continue_pending_steering(turn_name=turn_name)
                except ValueError:
                    with self._lock:
                        if coordinator.controller.state in {TaskState.PAUSING, TaskState.PAUSED}:
                            continue
                    raise
                continue
            if action == "finalize":
                coordinator.finish_fresh_finalization(self.finalizer_factory())
                return

    def _fail_incomplete_turn(self, handle: TaskHandle, turn: OpenClawTurnResult) -> None:
        handle.coordinator.controller.fail("investigation_turn_incomplete")
        outcome = turn.cli_outcome
        handle.audit.store.write_json(handle.task.id, "investigation-error.json", {
            "returncode": turn.process.returncode,
            "stop_reason": turn.process.stop_reason,
            "runtime_limit_reason": turn.runtime_limit_reason,
            "runtime_guard_reason": turn.runtime_guard_reason,
            "cli_blockers": list(outcome.blockers) if outcome is not None else ["missing_cli_outcome"],
            "cli_flags": dict(outcome.flags) if outcome is not None else {},
        })
        handle.audit.snapshot_control(handle.task, handle.session, handle.coordinator.catalog)

    def _complete_conversation(self, handle: TaskHandle, turn: OpenClawTurnResult) -> None:
        outcome = turn.cli_outcome
        if outcome is None or outcome.answer is None:
            raise ValueError("conversation completion requires visible OpenClaw answer")
        handle.audit.persist_result(
            {"valid": True, "mode": "conversation", "answer_text": outcome.answer, "task_state": TaskState.COMPLETED.value},
            rendered=outcome.answer,
        )
        handle.coordinator.controller.complete()
        handle.audit.snapshot_control(handle.task, handle.session, handle.coordinator.catalog)

    def _resolve_auto_as_task(self, handle: TaskHandle) -> None:
        if handle.task.mode != "auto":
            return
        handle.task.mode = "task"
        handle.audit.persist_task(handle.task)

    def _resolve_auto_as_conversation(self, handle: TaskHandle) -> None:
        if handle.task.mode != "auto":
            return
        handle.task.mode = "conversation"
        handle.task.metadata.setdefault("conversation_id", handle.task.id)
        handle.audit.persist_task(handle.task)

    @staticmethod
    def _has_business_evidence(items) -> bool:
        for item in items:
            source = item.source
            metadata = item.metadata
            if source.startswith(_BUSINESS_PATH_PREFIXES):
                return True
            if metadata.get("evidence_type") in {"image", "structured_business_facts"}:
                return True
            command = metadata.get("command")
            if isinstance(command, str) and any(marker in command for marker in _BUSINESS_COMMAND_MARKERS):
                return True
        return False

    @staticmethod
    def _turn_attempted_business_work(turn: OpenClawTurnResult) -> bool:
        trace = load_audit_trace(turn.audit_dir)
        for call in trace.calls:
            for key in ("path", "file_path"):
                value = call.arguments.get(key)
                if isinstance(value, str) and value.startswith(_BUSINESS_PATH_PREFIXES):
                    return True
            if call.name == "view_image":
                paths = []
                one = call.arguments.get("path")
                if isinstance(one, str):
                    paths.append(one)
                many = call.arguments.get("paths")
                if isinstance(many, list):
                    paths.extend(value for value in many if isinstance(value, str))
                if any(path.startswith(_BUSINESS_PATH_PREFIXES) for path in paths):
                    return True
            if call.name == "exec":
                command = call.arguments.get("command")
                if isinstance(command, str) and any(marker in command for marker in _BUSINESS_COMMAND_MARKERS):
                    return True
        return False

    @staticmethod
    def _turn_completed_normally(turn: OpenClawTurnResult) -> bool:
        return bool(
            turn.process.returncode == 0
            and turn.process.stop_reason is None
            and turn.cli_outcome is not None
            and turn.cli_outcome.completed
        )

    def _start_worker_locked(self, handle: TaskHandle, target: Callable[[TaskHandle, str], None], arg: str) -> None:
        if handle.worker_alive:
            raise TaskConflictError("task worker is already running")

        def run() -> None:
            try:
                target(handle, arg)
            except Exception as exc:
                if not handle.task.terminal:
                    handle.coordinator.controller.fail("runtime_api_worker_exception")
                handle.audit.store.write_json(handle.task.id, "worker-error.json", {
                    "type": type(exc).__name__, "message": str(exc)[:800],
                })
                handle.audit.snapshot_control(handle.task, handle.session, handle.coordinator.catalog)
            finally:
                with self._lock:
                    if handle.task.terminal:
                        self._cleanup_terminal_locked(handle)

        thread = threading.Thread(target=run, name=f"scopex-task-{handle.task.id}", daemon=True)
        handle.thread = thread
        thread.start()

    def _cleanup_terminal_locked(self, handle: TaskHandle) -> None:
        try:
            cleanup = handle.coordinator.close()
            self.store.write_json(handle.task.id, "cleanup.json", {
                "container_ids": list(cleanup.container_ids), "warnings": list(cleanup.warnings),
            })
        except Exception as exc:
            self.store.write_json(handle.task.id, "cleanup.json", {
                "error": type(exc).__name__ + ": " + str(exc)[:500],
            })
        if self._active_task_id == handle.task.id:
            self._active_task_id = None

    def _release_terminal_active_locked(self) -> None:
        if self._active_task_id is None:
            return
        handle = self._handles.get(self._active_task_id)
        if handle is None or handle.task.terminal:
            self._active_task_id = None

    def _live_handle(self, task_id: str) -> TaskHandle:
        with self._lock:
            handle = self._handles.get(task_id)
            if handle is not None:
                return handle
        self._require_task(task_id)
        raise TaskConflictError("task is historical and cannot be controlled after process restart")

    def _require_task(self, task_id: str) -> None:
        try:
            self.store.existing_task_dir(task_id)
        except FileNotFoundError:
            raise TaskNotFoundError(task_id) from None

    @staticmethod
    def _safe_child(root: Path, name: str) -> Path:
        root = Path(root).resolve()
        candidate = (root / name).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("unsafe task-owned path") from exc
        return candidate

    @staticmethod
    def _task_local_day(row: dict) -> str | None:
        value = row.get("started_at") or row.get("created_at")
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d")
        except ValueError:
            return None

    @staticmethod
    def _parse_day(value: str) -> str:
        try:
            return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("day must use YYYY-MM-DD") from exc

    @staticmethod
    def _message(value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("message must be a string")
        value = value.strip()
        if not value:
            raise ValueError("message is required")
        if len(value) > 32768:
            raise ValueError("message exceeds 32768 characters")
        return value
