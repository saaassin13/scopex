from __future__ import annotations

from dataclasses import dataclass, field
from collections import deque
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

from scopex.assessment import (
    STATUSES as ASSESSMENT_STATUSES, PUSH_DECISIONS, digest, from_native,
    parse_footer, record as assessment_record, task_summary,
)
from scopex.agent.runtime import OpenClawTurnResult
from scopex.agent.trace import load_audit_trace
from scopex.api.data_packages import DataPackageService
from scopex.evidence.catalog import EvidenceCatalog
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
    coordinator: InvestigationCoordinator | None
    audit: RuntimeAudit
    memory_events: InMemoryEventSink
    thread: threading.Thread | None = None
    turn_index: int = 0
    queued_at: float | None = None
    cleaned: bool = False
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
        max_active_tasks: int = 1,
        max_queued_tasks: int = 0,
        queue_timeout_s: float = 600.0,
        data_binds: tuple[str, ...] = (),
        collection_max_bytes: int = 2 * 1024 * 1024 * 1024,
        collection_max_files: int = 5000,
        reconcile_interrupted: bool = False,
        assessment_classifier: Callable[..., dict] | None = None,
    ) -> None:
        if isinstance(max_active_tasks, bool) or not isinstance(max_active_tasks, int) or not 1 <= max_active_tasks <= 4:
            raise ValueError("max_active_tasks must be 1..4")
        if isinstance(max_queued_tasks, bool) or not isinstance(max_queued_tasks, int) or not 0 <= max_queued_tasks <= 64:
            raise ValueError("max_queued_tasks must be 0..64")
        if not 1 <= queue_timeout_s <= 3600:
            raise ValueError("queue_timeout_s must be 1..3600")
        self.store = AuditStore(Path(audit_root))
        self.work_root = Path(work_root) if work_root is not None else self.store.root.parent / "work"
        self.export_root = Path(export_root) if export_root is not None else self.store.root.parent / "exports"
        if collection_max_bytes < 1 or collection_max_files < 1:
            raise ValueError("collection limits must be positive")
        self.data_packages = DataPackageService(
            work_root=self.work_root,
            data_binds=data_binds,
            max_bytes=collection_max_bytes,
            max_files=collection_max_files,
        )
        self.coordinator_factory = coordinator_factory
        self.finalizer_factory = finalizer_factory
        self._lock = threading.RLock()
        self._handles: dict[str, TaskHandle] = {}
        self.max_active_tasks = max_active_tasks
        self.max_queued_tasks = max_queued_tasks
        self.queue_timeout_s = queue_timeout_s
        self._active_ids: set[str] = set()
        self._queue: deque[str] = deque()
        self._shutdown_event = threading.Event()
        self.assessment_classifier = assessment_classifier
        self._assessment_task_id: str | None = None
        self._assessment_thread: threading.Thread | None = None
        self._build_commit = self._git_head()
        if reconcile_interrupted:
            self._reconcile_interrupted()
            self._reconcile_assessments()
        self._queue_thread: threading.Thread | None = None
        if max_queued_tasks:
            self._queue_thread = threading.Thread(target=self._queue_watch, name="scopex-admission", daemon=True)
            self._queue_thread.start()

    @property
    def active_task_id(self) -> str | None:
        # Compatibility only. Product clients must use /activity for the full set.
        with self._lock:
            return next((key for key in sorted(self._active_ids)
                         if not self._handles[key].task.terminal), None)

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
            if self._shutdown_event.is_set():
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

            task_id = "task-" + uuid.uuid4().hex[:12]
            resolved_agent_id = agent_id or ("sxapi" + uuid.uuid4().hex[:8])
            resolved_session_key = session_key or f"agent:{resolved_agent_id}:{task_id}"
            if not resolved_session_key.startswith(f"agent:{resolved_agent_id}:"):
                raise ValueError("session_key does not belong to agent_id")
            task_metadata = {"agent_id": resolved_agent_id, "scopex_commit_at_start": self._build_commit,
                             "max_active_tasks": self.max_active_tasks}
            if metadata:
                task_metadata.update(metadata)
            if "assessment_enabled" in task_metadata and not isinstance(task_metadata["assessment_enabled"], bool):
                raise ValueError("assessment_enabled must be a boolean")
            # Never inherit a verdict from another Run/session.
            task_metadata.pop("assessment", None)
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
            # Queued tasks have no Runtime, sandbox or host snapshot yet.
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
            return task.snapshot()

    def create_auto_run(self, message: str, *, assessment_enabled: bool = False) -> dict:
        return self.create_task(message, mode="auto", trigger_type="manual",
                                metadata={"assessment_enabled": assessment_enabled})

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
                row = handle.task.snapshot()
                row["assessment"] = task_summary(row)
                return row
        try:
            row = self.store.read_json(task_id, "task.json")
            row["assessment"] = task_summary(row)
            return row
        except (FileNotFoundError, OSError):
            raise TaskNotFoundError(task_id) from None

    def list_tasks(self, *, mode: str | None = None, day: str | None = None,
                   schedule_id: str | None = None, limit: int | None = None, offset: int = 0,
                   state: str | None = None, assessment_status: str | None = None,
                   push_decision: str | None = None) -> list[dict]:
        if mode is not None and mode not in {"task", "conversation", "auto"}:
            raise ValueError("mode must be task, conversation or auto")
        if offset < 0 or (limit is not None and not 1 <= limit <= 200):
            raise ValueError("offset must be nonnegative and limit must be 1..200")
        if state is not None and state not in {item.value for item in TaskState}:
            raise ValueError("unsupported task state")
        if assessment_status is not None and assessment_status not in ASSESSMENT_STATUSES:
            raise ValueError("unsupported assessment status")
        if push_decision is not None and push_decision not in PUSH_DECISIONS:
            raise ValueError("unsupported push decision")
        day_value = self._parse_day(day) if day is not None else None
        tasks: list[dict] = []
        for stored_task_id in self.store.list_task_ids():
            try:
                row = self.get_task(stored_task_id)
                if mode is not None and row.get("mode", "task") != mode:
                    continue
                if day_value is not None and self._task_local_day(row) != day_value:
                    continue
                if schedule_id is not None and row.get("schedule_id") != schedule_id:
                    continue
                if state is not None and row.get("state") != state:
                    continue
                if assessment_status is not None and row["assessment"]["status"] != assessment_status:
                    continue
                if push_decision is not None and row["assessment"]["push_decision"] != push_decision:
                    continue
                tasks.append(row)
            except (TaskNotFoundError, json.JSONDecodeError, OSError):
                continue
        tasks.sort(key=lambda row: str(row.get("started_at") or row.get("created_at") or ""), reverse=True)
        return tasks[offset:offset + limit] if limit is not None else tasks[offset:]

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
            elif state in {"CREATED", "QUEUED", "RUNNING", "PAUSING", "PAUSED", "FINALIZING"}:
                slot["running"] += 1
            if row.get("trigger_type") == "schedule":
                slot["scheduled"] += 1
            else:
                slot["manual"] += 1
        return {"month": month, "days": [days[key] for key in sorted(days)]}

    def delete_task(self, task_id: str) -> dict:
        with self.data_packages.operation(task_id), self._lock:
            if self._assessment_task_id == task_id:
                raise TaskConflictError("该任务正在评估，不能删除")
            return self._delete_task(task_id)

    def _delete_task(self, task_id: str) -> dict:
        task = self.get_task(task_id)
        if task.get("state") not in {"COMPLETED", "FAILED", "CANCELLED"}:
            raise TaskConflictError("only terminal tasks can be deleted")
        with self._lock:
            handle = self._handles.get(task_id)
            if handle is not None and handle.worker_alive:
                raise TaskConflictError("task cleanup is still running; retry deletion shortly")
            self._active_ids.discard(task_id)
            self._handles.pop(task_id, None)

        task_dir = self.store.existing_task_dir(task_id)
        work_dir = self._safe_child(self.work_root, task_id)
        export_file = self._safe_child(self.export_root, f"scopex-review-{task_id}.zip")
        collected = work_dir / "collected"
        removed = {"audit": False, "work": False, "review_export": False,
                   "collected_business_data": collected.is_dir()}
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
            "collected_business_data_deleted": removed["collected_business_data"],
            "external_business_data_deleted": False,
        }

    def get_data_package(self, task_id: str) -> dict:
        task = self.get_task(task_id)
        if task.get("state") not in {"COMPLETED", "FAILED", "CANCELLED"}:
            raise TaskConflictError("data can only be collected for a terminal task")
        return self.data_packages.options(task_id, self.get_evidence(task_id))

    def build_data_package(self, task_id: str, modes: list[str]) -> dict:
        with self.data_packages.operation(task_id):
            return self._build_data_package(task_id, modes)

    def _build_data_package(self, task_id: str, modes: list[str]) -> dict:
        self.get_data_package(task_id)
        path = self.data_packages.build(task_id, modes, self.get_evidence(task_id))
        return {
            **self.data_packages.options(task_id, self.get_evidence(task_id)),
            "package_ready": True,
            "filename": f"scopex-data-{task_id}.zip",
            "bytes": path.stat().st_size,
        }

    def data_package_file(self, task_id: str) -> Path:
        self.get_data_package(task_id)
        path = self._safe_child(self.work_root, task_id) / "collected" / "scopex-data.zip"
        if not path.is_file():
            raise TaskConflictError("data package has not been collected")
        return path

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

    def get_assessment(self, task_id: str) -> dict:
        return {"task_id": task_id, "assessment": self.get_task(task_id)["assessment"]}

    def _save_assessment_locked(self, task_id: str, value: dict) -> dict:
        # The caller owns _lock; deletion cannot race this metadata-only update.
        row = self.get_task(task_id)
        previous = (row.get("metadata") or {}).get("assessment") or {}
        value = dict(value)
        value["manual_model_calls_total"] = int(previous.get("manual_model_calls_total", 0)) + value.get("model_calls", 0)
        self.store.write_json(task_id, "assessment.json", value)
        self.store.append_jsonl(task_id, "assessment-history.jsonl", value)
        handle = self._handles.get(task_id)
        if handle is not None:
            handle.task.metadata["assessment"] = value
            handle.audit.persist_task(handle.task)
        else:
            row.pop("assessment", None)
            row.setdefault("metadata", {})["assessment"] = value
            self.store.write_json(task_id, "task.json", row)
        return {"task_id": task_id, "assessment": value}

    def request_assessment(self, task_id: str, *, allow_model: bool = False, retry: bool = False) -> dict:
        """Explicit manual action; no source scan, auto backfill or native rerun.

        At most one short text call may be in flight. It starts only when the
        execution slots/queue are idle; a later foreground run is not blocked.
        A repeated POST is idempotent unless the user explicitly requests retry.
        """
        if not isinstance(allow_model, bool) or not isinstance(retry, bool):
            raise ValueError("allow_model/retry must be booleans")
        with self.data_packages.operation(task_id), self._lock:
            if self._shutdown_event.is_set():
                raise TaskBusyError("服务正在关闭，未启动评估")
            task = self.get_task(task_id)
            if task["state"] not in {"COMPLETED", "FAILED", "CANCELLED"}:
                raise TaskConflictError("只能评估已经结束的任务")
            if self._assessment_task_id == task_id:
                return self.get_assessment(task_id)
            old = (task.get("metadata") or {}).get("assessment")
            if (task["state"] == "COMPLETED" and isinstance(old, dict)
                    and old.get("status") in {"normal", "abnormal", "needs_review"} and not retry):
                return self.get_assessment(task_id)
            # Failed tasks must never be promoted by a saved non-empty draft.
            if task["state"] != "COMPLETED":
                return self._save_assessment_locked(task_id, assessment_record(
                    "needs_review", "原任务执行失败或中断，未追加模型判断。", reason="execution_incomplete"))
            saved = self.get_result(task_id)
            payload = saved.get("result") or {}
            if not isinstance(payload, dict) or payload.get("valid") is not True:
                return self._save_assessment_locked(task_id, assessment_record(
                    "needs_review", "没有完整的原生结果，不能自动认定正常或异常。", reason="result_unavailable"))
            answer = payload.get("report_text") or payload.get("answer_text") or saved.get("rendered")
            if not isinstance(answer, str) or not answer.strip():
                return self._save_assessment_locked(task_id, assessment_record(
                    "needs_review", "没有可供归类的正文。", reason="result_unavailable"))
            meta = payload.get("report_meta") or {}
            if isinstance(meta, dict) and (meta.get("no_data") or meta.get("producer") == "scopex_no_data"):
                value, _ = from_native(answer, complete=True, no_data=True, request=task["user_request"])
                return self._save_assessment_locked(task_id, value)
            footer, _ = parse_footer(answer)
            if footer is not None:
                value, _ = from_native(answer, complete=True, no_data=False, request=task["user_request"])
                value["source"] = "native_reuse"
                return self._save_assessment_locked(task_id, value)
            if not allow_model:
                return {"task_id": task_id, "assessment": task["assessment"], "requires_model": True}
            if self.assessment_classifier is None:
                return self._save_assessment_locked(task_id, assessment_record(
                    "needs_review", "手动文本归类尚未配置；原结果保持不变。", reason="classifier_unavailable"))
            if self._assessment_task_id is not None or self._active_ids or self._queue:
                raise TaskBusyError("已有任务或文本评估在执行；未增加模型负载，请空闲时再试")
            session_path = self.store.existing_task_dir(task_id) / "session.json"
            try:
                if session_path.stat().st_size > 65536:
                    raise ValueError("control history too large")
                session = self.store.read_json(task_id, "session.json")
                if session.get("task_id") != task_id or session.get("session_key") != task.get("session_key"):
                    raise ValueError("control identity mismatch")
                turns = session.get("turns")
                if not isinstance(turns, list) or any(not isinstance(x, dict) for x in turns):
                    raise ValueError("invalid control history")
                controls = [{"kind": x["kind"], "content": x["content"]} for x in turns
                            if x.get("kind") in {"STEER", "RESUME"}]
                if any(not isinstance(x["content"], str) for x in controls):
                    raise ValueError("invalid control content")
            except (OSError, ValueError, KeyError, AttributeError):
                return self._save_assessment_locked(task_id, assessment_record(
                    "needs_review", "原任务控制记录缺失或不可用，未猜测判据或调用模型。", reason="control_context_unavailable"))
            self._assessment_task_id = task_id
            pending = assessment_record("pending", "正在对已保存正文进行一次短文本归类，不重新调查。", source="manual_text")
            try:
                result = self._save_assessment_locked(task_id, pending)
                def run() -> None:
                    try:
                        value = self.assessment_classifier(
                            request=task["user_request"], answer=answer, controls=controls,
                            anchor=task.get("scheduled_for") or task.get("created_at"))
                    except Exception:
                        value = assessment_record("needs_review", "手动评估未完成，原任务不受影响。",
                                                  reason="assessment_worker_error", source="manual_text", model_calls=1)
                    try:
                        with self._lock:
                            current = self.get_result(task_id)
                            current_payload = current.get("result") or {}
                            current_text = (current_payload.get("report_text") or
                                            current_payload.get("answer_text") or current.get("rendered"))
                            if current_text != answer:
                                value = assessment_record("needs_review", "归类期间原正文发生变化，未采用旧正文的判定。",
                                                          reason="assessment_source_changed", source="manual_text",
                                                          model_calls=value.get("model_calls", 0))
                            value["control_sha256"] = digest(json.dumps(controls, ensure_ascii=False, sort_keys=True))
                            if self._shutdown_event.is_set():
                                value = assessment_record("needs_review", "评估被服务关闭中断，未自动重跑。",
                                                          reason="assessment_interrupted", source="manual_text",
                                                          model_calls=value.get("model_calls", 0))
                            self._save_assessment_locked(task_id, value)
                    except (OSError, ValueError):
                        with self._lock:
                            failed = assessment_record("needs_review", "评估结果保存失败，原任务正文仍保留。",
                                                       reason="assessment_storage_error")
                            handle = self._handles.get(task_id)
                            if handle is not None:
                                handle.task.metadata["assessment"] = failed
                            try:
                                self._save_assessment_locked(task_id, failed)
                            except OSError:
                                pass
                    finally:
                        with self._lock:
                            self._assessment_task_id = None
                thread = threading.Thread(target=run, name=f"scopex-assessment-{task_id}", daemon=True)
                self._assessment_thread = thread
                thread.start()
                return result
            except Exception:
                self._assessment_task_id = None
                self._save_assessment_locked(task_id, assessment_record(
                    "needs_review", "未能启动文本评估，原任务保持不变。", reason="assessment_start_failed"))
                raise

    def _reconcile_assessments(self) -> None:
        # A restart never replays model calls, changes original execution state,
        # or backfills old/disabled tasks. Only an already-pending label expires.
        for task_id in self.store.list_task_ids():
            try:
                row = self.store.read_json(task_id, "task.json")
                old = (row.get("metadata") or {}).get("assessment") or {}
                if old.get("status") != "pending":
                    continue
                with self._lock:
                    self._save_assessment_locked(task_id, assessment_record(
                        "needs_review", "上次评估因服务重启中断，未自动重跑。", reason="assessment_interrupted"))
            except (OSError, ValueError, AttributeError):
                continue

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
            "worker-error.json", "cleanup.json", "report.md", "report-meta.json",
            "report.json", "report-error.json", "assessment.json", "assessment-history.jsonl",
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
            "scopex_commit": (task.get("metadata") or {}).get("scopex_commit_at_start"),
            "export_commit": self._git_head(),
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
        self._shutdown_event.set()
        if self._queue_thread is not None:
            self._queue_thread.join(timeout=max(0.0, deadline - time.monotonic()))
        with self._lock:
            for task_id in list(self._queue):
                self._cancel_unstarted_locked(self._handles[task_id], "server_shutdown")
            self._queue.clear()
            handles = list(self._handles.values())
            for handle in handles:
                if handle.task.state is TaskState.RUNNING:
                    try:
                        handle.coordinator.request_stop("server_shutdown")
                    except Exception:
                        pass
        if self._assessment_thread is not None:
            self._assessment_thread.join(timeout=max(0.0, deadline - time.monotonic()))
        for handle in handles:
            thread = handle.thread
            if thread is None or not thread.is_alive():
                continue
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        with self._lock:
            for handle in handles:
                if handle.worker_alive:
                    continue
                if not handle.task.terminal and handle.coordinator is not None:
                    handle.coordinator.controller.cancel("server_shutdown")
                    handle.audit.snapshot_control(handle.task, handle.session, handle.coordinator.catalog)
                    self._cleanup_terminal_locked(handle)
                    self._active_ids.discard(handle.task.id)

    def _run_initial(self, handle: TaskHandle, _unused: str) -> None:
        if handle.coordinator is None:
            events = AuditEventSink(self.store, downstream=handle.memory_events)
            handle.coordinator = self.coordinator_factory(handle.task, handle.session, events, handle.audit)
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
                elif state is TaskState.RUNNING and getattr(coordinator, "native_answers", False):
                    if handle.task.mode == "auto":
                        if (self._has_business_evidence(coordinator.catalog.items)
                                or self._turn_attempted_business_work(current_turn)
                                or not self._turn_completed_normally(current_turn)):
                            self._resolve_auto_as_task(handle)
                        else:
                            self._resolve_auto_as_conversation(handle)
                    # Same publication path for all native answers; no Evidence
                    # schema gate or extra model call, including on failure.
                    coordinator.finish_native_answer(current_turn)
                    return
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
                    self._dispatch_locked()

        thread = threading.Thread(target=run, name=f"scopex-task-{handle.task.id}", daemon=True)
        handle.thread = thread
        thread.start()

    def _cleanup_terminal_locked(self, handle: TaskHandle) -> None:
        if handle.cleaned:
            return
        handle.cleaned = True
        if handle.coordinator is None:
            return
        try:
            cleanup = handle.coordinator.close()
            self.store.write_json(handle.task.id, "cleanup.json", {
                "container_ids": list(cleanup.container_ids), "warnings": list(cleanup.warnings),
            })
        except Exception as exc:
            self.store.write_json(handle.task.id, "cleanup.json", {
                "error": type(exc).__name__ + ": " + str(exc)[:500],
            })
    def _launch_locked(self, handle: TaskHandle) -> None:
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
                row["assessment"] = task_summary(row)
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
