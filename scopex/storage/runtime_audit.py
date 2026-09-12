from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scopex.evidence.catalog import EvidenceCatalog
from scopex.events.progress import EventSink, EventType, ProgressEvent
from scopex.runtime.session import Session
from scopex.runtime.task import Task
from scopex.storage.audit import AuditStore


class AuditEventSink:
    """Persist runtime progress events into one task-local events.jsonl."""

    def __init__(self, store: AuditStore, *, downstream: EventSink | None = None) -> None:
        self.store = store
        self.downstream = downstream
        self._seq_by_task: dict[str, int] = {}

    def emit(self, task_id: str, event_type: EventType, **data: Any) -> ProgressEvent:
        if not isinstance(event_type, EventType):
            event_type = EventType(event_type)
        if self.downstream is not None:
            event = self.downstream.emit(task_id, event_type, **data)
        else:
            from scopex.events.progress import utcnow

            seq = self._seq_by_task.get(task_id, 0) + 1
            self._seq_by_task[task_id] = seq
            event = ProgressEvent(seq, task_id, event_type, utcnow(), dict(data))
        self.store.append_jsonl(task_id, "events.jsonl", event.to_dict())
        return event


@dataclass(slots=True)
class RuntimeAudit:
    """Write authoritative ScopeX-owned task/session/evidence/final records."""

    store: AuditStore
    task_id: str

    def persist_task(self, task: Task) -> None:
        if task.id != self.task_id:
            raise ValueError("audit task identity mismatch")
        self.store.write_json(self.task_id, "task.json", task.snapshot())

    def persist_session(self, session: Session) -> None:
        if session.task_id != self.task_id:
            raise ValueError("audit session identity mismatch")
        self.store.write_json(self.task_id, "session.json", session.snapshot())

    def persist_evidence(self, catalog: EvidenceCatalog) -> None:
        if catalog.task_id != self.task_id:
            raise ValueError("audit evidence identity mismatch")
        self.store.write_json(self.task_id, "evidence.json", catalog.snapshot())

    def persist_claims(self, payload: dict[str, Any]) -> None:
        self.store.write_json(self.task_id, "claims.json", payload)

    def persist_result(self, result: dict[str, Any], *, rendered: str | None = None) -> None:
        self.store.write_json(self.task_id, "result.json", result)
        if rendered is not None:
            self.store.write_text(self.task_id, "final.txt", rendered)

    def snapshot_control(self, task: Task, session: Session, catalog: EvidenceCatalog) -> None:
        self.persist_task(task)
        self.persist_session(session)
        self.persist_evidence(catalog)
