from __future__ import annotations

from typing import Any

from scopex.events.progress import EventSink, EventType
from scopex.runtime.session import Session
from scopex.runtime.task import Task, TaskState


class TaskController:
    """Own task lifecycle and user control semantics.

    The controller does not decide business investigation steps. OpenClaw owns
    the agent loop; ScopeX owns lifecycle, stop/resume/steer and finalization
    boundaries.
    """

    def __init__(self, task: Task, session: Session, events: EventSink) -> None:
        if task.id != session.task_id or task.session_key != session.session_key:
            raise ValueError("task/session identity mismatch")
        self.task = task
        self.session = session
        self.events = events

    def created(self) -> None:
        self.events.emit(self.task.id, EventType.TASK_CREATED, state=self.task.state.value)

    def start(self) -> None:
        self.task.transition(TaskState.RUNNING)
        self.events.emit(self.task.id, EventType.TASK_STARTED, state=self.task.state.value)

    def steer(self, message: str) -> None:
        if self.task.state not in {TaskState.RUNNING, TaskState.PAUSED}:
            raise ValueError("steering requires RUNNING or PAUSED task")
        turn = self.session.steer(message)
        self.events.emit(
            self.task.id,
            EventType.USER_STEER,
            turn_index=turn.index,
            message=message,
        )

    def request_stop(self, message: str = "") -> None:
        if self.task.state is not TaskState.RUNNING:
            raise ValueError("stop requires RUNNING task")
        self.session.stop(message)
        self.task.transition(TaskState.PAUSING, reason="user_stop")
        self.events.emit(self.task.id, EventType.USER_STOP, message=message)

    def safe_stop(self, **details: Any) -> None:
        if self.task.state is not TaskState.PAUSING:
            raise ValueError("safe_stop requires PAUSING task")
        self.task.transition(TaskState.PAUSED, reason="safe_boundary")
        self.events.emit(self.task.id, EventType.SAFE_STOP, **details)

    def resume(self, message: str) -> None:
        if self.task.state is not TaskState.PAUSED:
            raise ValueError("resume requires PAUSED task")
        turn = self.session.resume(message)
        self.task.transition(TaskState.RUNNING, reason="user_resume")
        self.events.emit(
            self.task.id,
            EventType.USER_RESUME,
            turn_index=turn.index,
            message=message,
        )

    def begin_finalization(self, *, reasons: tuple[str, ...] = ()) -> None:
        if self.task.state is not TaskState.RUNNING:
            raise ValueError("finalization requires RUNNING task")
        self.events.emit(
            self.task.id,
            EventType.INVESTIGATION_COMPLETED,
            reasons=list(reasons),
        )
        self.task.transition(TaskState.FINALIZING, reason=",".join(reasons) or None)
        self.events.emit(self.task.id, EventType.FINALIZATION_STARTED)

    def finalization_completed(self) -> None:
        if self.task.state is not TaskState.FINALIZING:
            raise ValueError("finalization_completed requires FINALIZING task")
        self.events.emit(self.task.id, EventType.FINALIZATION_COMPLETED)

    def complete(self) -> None:
        if self.task.state not in {TaskState.RUNNING, TaskState.FINALIZING}:
            raise ValueError("complete requires RUNNING or FINALIZING task")
        self.task.transition(TaskState.COMPLETED)
        self.events.emit(self.task.id, EventType.TASK_COMPLETED)

    def fail(self, reason: str) -> None:
        if self.task.terminal:
            return
        self.task.transition(TaskState.FAILED, reason=reason)
        self.events.emit(self.task.id, EventType.TASK_FAILED, reason=reason)

    def cancel(self, reason: str = "") -> None:
        if self.task.terminal:
            return
        self.task.transition(TaskState.CANCELLED, reason=reason)
        self.events.emit(self.task.id, EventType.TASK_CANCELLED, reason=reason)
