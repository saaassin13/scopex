from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _duration_ms(started_at: str | None, finished_at: str | None) -> int | None:
    if not started_at or not finished_at:
        return None
    try:
        start = datetime.fromisoformat(started_at)
        finish = datetime.fromisoformat(finished_at)
    except ValueError:
        return None
    return max(0, int(round((finish - start).total_seconds() * 1000)))


class TaskState(str, Enum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSING = "PAUSING"
    PAUSED = "PAUSED"
    FINALIZING = "FINALIZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


_ALLOWED: dict[TaskState, set[TaskState]] = {
    TaskState.CREATED: {TaskState.QUEUED, TaskState.RUNNING, TaskState.CANCELLED, TaskState.FAILED},
    TaskState.QUEUED: {TaskState.CREATED, TaskState.CANCELLED, TaskState.FAILED},
    TaskState.RUNNING: {
        TaskState.PAUSING,
        TaskState.FINALIZING,
        TaskState.COMPLETED,
        TaskState.CANCELLED,
        TaskState.FAILED,
    },
    TaskState.PAUSING: {TaskState.PAUSED, TaskState.CANCELLED, TaskState.FAILED},
    TaskState.PAUSED: {TaskState.RUNNING, TaskState.CANCELLED, TaskState.FAILED},
    TaskState.FINALIZING: {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.COMPLETED: set(),
    TaskState.FAILED: set(),
    TaskState.CANCELLED: set(),
}


@dataclass(slots=True)
class Task:
    id: str
    user_request: str
    session_key: str
    state: TaskState = TaskState.CREATED
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_reason: str | None = None
    mode: str = "task"
    trigger_type: str = "manual"
    schedule_id: str | None = None
    scheduled_for: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None

    def transition(self, target: TaskState, *, reason: str | None = None) -> None:
        if not isinstance(target, TaskState):
            target = TaskState(target)
        if target == self.state:
            return
        if target not in _ALLOWED[self.state]:
            raise ValueError(f"invalid task transition: {self.state.value} -> {target.value}")
        now = utcnow()
        if target is TaskState.RUNNING and self.started_at is None:
            self.started_at = now
        self.state = target
        self.updated_at = now
        self.last_reason = reason
        if target in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}:
            self.finished_at = now
            self.duration_ms = _duration_ms(self.started_at, self.finished_at)

    @property
    def terminal(self) -> bool:
        return self.state in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_request": self.user_request,
            "session_key": self.session_key,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
            "last_reason": self.last_reason,
            "mode": self.mode,
            "trigger_type": self.trigger_type,
            "schedule_id": self.schedule_id,
            "scheduled_for": self.scheduled_for,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "queue_wait_ms": _duration_ms(self.created_at, self.metadata.get("admitted_at")),
            "total_duration_ms": _duration_ms(self.created_at, self.finished_at),
        }
