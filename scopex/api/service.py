from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import threading
import time
import uuid
from typing import Callable, Protocol

from scopex.agent.runtime import OpenClawTurnResult
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
    """Single-user local task service over the proven ScopeX Runtime.

    One non-terminal major task owns the execution slot. A PAUSED task still
    owns that slot because its OpenClaw session/sandbox must remain resumable.
    Historical reads come from AuditStore, so no database is required.
    """

    def __init__(
        self,
        *,
        audit_root: Path,
        coordinator_factory: CoordinatorFactory,
        finalizer_factory: FinalizerFactory,
    ) -> None:
        self.store = AuditStore(Path(audit_root))
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

    def create_task(self, message: str) -> dict:
        message = self._message(message)
        with self._lock:
            self._release_terminal_active_locked()
            if self._active_task_id is not None:
                active = self._handles.get(self._active_task_id)
                state = active.task.state.value if active is not None else "UNKNOWN"
                raise TaskBusyError(f"active task {self._active_task_id} is {state}")

            task_id = "task-" + uuid.uuid4().hex[:12]
            agent_id = "sxapi" + uuid.uuid4().hex[:8]
            session_key = f"agent:{agent_id}:{task_id}"
            task = Task(task_id, message, session_key, metadata={"agent_id": agent_id})
            session = Session(task_id, session_key)
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

    def list_tasks(self) -> list[dict]:
        tasks: list[dict] = []
        for task_id in self.store.list_task_ids():
            try:
                tasks.append(self.get_task(task_id))
            except (TaskNotFoundError, json.JSONDecodeError, OSError):
                continue
        tasks.sort(key=lambda row: str(row.get("created_at", "")), reverse=True)
        return tasks

    def get_events(self, task_id: str, *, after: int = 0) -> list[dict]:
        if after < 0:
            raise ValueError("after must be non-negative")
        self._require_task(task_id)
        try:
            rows = self.store.read_jsonl(task_id, "events.jsonl")
        except FileNotFoundError:
            return []
        return [
            row
            for row in rows
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
            return {
                "task_id": task_id,
                "state": task.get("state"),
                "available": False,
            }
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

    def shutdown(self, timeout_s: float = 10.0) -> None:
        """Stop active work and wait for every worker to finish touching local state.

        A task can become terminal before its worker finishes sandbox cleanup and
        cleanup.json persistence. Shutdown therefore joins all known workers,
        not only the current active task. This gives callers a quiescence
        boundary before unmounting/removing the audit directory or exiting the
        process.
        """

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
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)

        with self._lock:
            for handle in handles:
                if handle.worker_alive:
                    continue
                if not handle.task.terminal:
                    handle.coordinator.controller.cancel("server_shutdown")
                    handle.audit.snapshot_control(
                        handle.task,
                        handle.session,
                        handle.coordinator.catalog,
                    )
                    self._cleanup_terminal_locked(handle)

    def _run_initial(self, handle: TaskHandle, _unused: str) -> None:
        turn = handle.coordinator.start(
            handle.task.user_request,
            turn_name=handle.next_turn_name(),
        )
        self._drive_after_turn(handle, turn)

    def _run_resume(self, handle: TaskHandle, message: str) -> None:
        turn = handle.coordinator.resume(
            message,
            turn_name=handle.next_turn_name(),
        )
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
                    coordinator.controller.safe_stop(
                        at_turn_end=True,
                        running_tool_cancelled=False,
                    )
                    handle.audit.snapshot_control(
                        handle.task,
                        handle.session,
                        coordinator.catalog,
                    )
                    return
                if state is TaskState.PAUSED:
                    return
                if state is TaskState.RUNNING and coordinator.steering.pending:
                    action = "steer"
                    turn_name = handle.next_turn_name()
                elif state is TaskState.RUNNING:
                    if not coordinator.catalog.items:
                        coordinator.controller.fail(
                            "investigation_completed_without_evidence"
                        )
                        handle.audit.snapshot_control(
                            handle.task,
                            handle.session,
                            coordinator.catalog,
                        )
                        return

                    if self._turn_completed_normally(current_turn):
                        coordinator.begin_finalization(goal_satisfied=True)
                        action = "finalize"
                    else:
                        decision = coordinator.convergence(goal_satisfied=False)
                        if decision.should_finalize:
                            coordinator.begin_finalization(goal_satisfied=False)
                            action = "finalize"
                        else:
                            coordinator.controller.fail(
                                "investigation_turn_incomplete"
                            )
                            handle.audit.store.write_json(
                                handle.task.id,
                                "investigation-error.json",
                                {
                                    "returncode": current_turn.process.returncode,
                                    "stop_reason": current_turn.process.stop_reason,
                                    "cli_blockers": list(current_turn.cli_outcome.blockers)
                                    if current_turn.cli_outcome is not None else ["missing_cli_outcome"],
                                },
                            )
                            handle.audit.snapshot_control(
                                handle.task,
                                handle.session,
                                coordinator.catalog,
                            )
                            return
                else:
                    return

            if action == "steer":
                try:
                    current_turn = coordinator.continue_pending_steering(turn_name=turn_name)
                except ValueError:
                    with self._lock:
                        if coordinator.controller.state in {
                            TaskState.PAUSING,
                            TaskState.PAUSED,
                        }:
                            continue
                    raise
                continue

            if action == "finalize":
                coordinator.finish_fresh_finalization(self.finalizer_factory())
                return

    @staticmethod
    def _turn_completed_normally(turn: OpenClawTurnResult) -> bool:
        return bool(
            turn.process.returncode == 0
            and turn.process.stop_reason is None
            and turn.cli_outcome is not None
            and turn.cli_outcome.completed
        )

    def _start_worker_locked(
        self,
        handle: TaskHandle,
        target: Callable[[TaskHandle, str], None],
        arg: str,
    ) -> None:
        if handle.worker_alive:
            raise TaskConflictError("task worker is already running")

        def run() -> None:
            try:
                target(handle, arg)
            except Exception as exc:
                if not handle.task.terminal:
                    handle.coordinator.controller.fail(
                        "runtime_api_worker_exception"
                    )
                handle.audit.store.write_json(
                    handle.task.id,
                    "worker-error.json",
                    {
                        "type": type(exc).__name__,
                        "message": str(exc)[:800],
                    },
                )
                handle.audit.snapshot_control(
                    handle.task,
                    handle.session,
                    handle.coordinator.catalog,
                )
            finally:
                with self._lock:
                    if handle.task.terminal:
                        self._cleanup_terminal_locked(handle)

        thread = threading.Thread(
            target=run,
            name=f"scopex-task-{handle.task.id}",
            daemon=True,
        )
        handle.thread = thread
        thread.start()

    def _cleanup_terminal_locked(self, handle: TaskHandle) -> None:
        try:
            cleanup = handle.coordinator.close()
            self.store.write_json(
                handle.task.id,
                "cleanup.json",
                {
                    "container_ids": list(cleanup.container_ids),
                    "warnings": list(cleanup.warnings),
                },
            )
        except Exception as exc:
            self.store.write_json(
                handle.task.id,
                "cleanup.json",
                {"error": type(exc).__name__ + ": " + str(exc)[:500]},
            )
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
        raise TaskConflictError(
            "task is historical and cannot be controlled after process restart"
        )

    def _require_task(self, task_id: str) -> None:
        try:
            self.store.existing_task_dir(task_id)
        except FileNotFoundError:
            raise TaskNotFoundError(task_id) from None

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
