from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import json
import threading
from typing import Any, Protocol


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventType(str, Enum):
    TASK_CREATED = "TASK_CREATED"
    TASK_STARTED = "TASK_STARTED"
    MODEL_REQUEST = "MODEL_REQUEST"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"
    EVIDENCE_ADDED = "EVIDENCE_ADDED"
    USER_STEER = "USER_STEER"
    USER_STOP = "USER_STOP"
    SAFE_STOP = "SAFE_STOP"
    USER_RESUME = "USER_RESUME"
    INVESTIGATION_COMPLETED = "INVESTIGATION_COMPLETED"
    FINALIZATION_STARTED = "FINALIZATION_STARTED"
    FINALIZATION_COMPLETED = "FINALIZATION_COMPLETED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    TASK_CANCELLED = "TASK_CANCELLED"


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    seq: int
    task_id: str
    type: EventType
    created_at: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "task_id": self.task_id,
            "type": self.type.value,
            "created_at": self.created_at,
            "data": dict(self.data),
        }


class EventSink(Protocol):
    def emit(self, task_id: str, event_type: EventType, **data: Any) -> ProgressEvent: ...


class InMemoryEventSink:
    def __init__(self) -> None:
        self._seq = 0
        self._events: list[ProgressEvent] = []
        self._lock = threading.Lock()

    def emit(self, task_id: str, event_type: EventType, **data: Any) -> ProgressEvent:
        if not isinstance(event_type, EventType):
            event_type = EventType(event_type)
        with self._lock:
            self._seq += 1
            event = ProgressEvent(self._seq, task_id, event_type, utcnow(), dict(data))
            self._events.append(event)
            return event

    @property
    def events(self) -> tuple[ProgressEvent, ...]:
        return tuple(self._events)


class JsonlEventSink:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        self._lock = threading.Lock()

    def emit(self, task_id: str, event_type: EventType, **data: Any) -> ProgressEvent:
        if not isinstance(event_type, EventType):
            event_type = EventType(event_type)
        with self._lock:
            self._seq += 1
            event = ProgressEvent(self._seq, task_id, event_type, utcnow(), dict(data))
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event.to_dict(), ensure_ascii=False, allow_nan=False) + "\n")
            return event
