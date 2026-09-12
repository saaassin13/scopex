from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskState(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    PAUSING = "PAUSING"
    PAUSED = "PAUSED"
    FINALIZING = "FINALIZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


_ALLOWED: dict[TaskState, set[TaskState]] = {
    TaskState.CREATED: {TaskState.RUNNING, TaskState.CANCELLED, TaskState.FAILED},
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

    def transition(self, target: TaskState, *, reason: str | None = None) -> None:
        if not isinstance(target, TaskState):
            target = TaskState(target)
        if target == self.state:
            return
        if target not in _ALLOWED[self.state]:
            raise ValueError(f"invalid task transition: {self.state.value} -> {target.value}")
        self.state = target
        self.updated_at = utcnow()
        self.last_reason = reason

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
        }
