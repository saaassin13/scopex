from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any

from scopex.evidence.catalog import EvidenceCatalog
from scopex.events.progress import EventSink, EventType, ProgressEvent
from scopex.finalizer.answer import compose_product_answer
from scopex.finalizer.claims import claim_set_from_dict
from scopex.finalizer.validator import normalize_claim_payload, validate_claim_payload
from scopex.runtime.session import Session
from scopex.runtime.task import Task
from scopex.storage.audit import AuditStore


class AuditEventSink:
    """Persist runtime progress events into one task-local events.jsonl."""

    def __init__(self, store: AuditStore, *, downstream: EventSink | None = None) -> None:
        self.store = store
        self.downstream = downstream
        self._seq_by_task: dict[str, int] = {}
        self._lock = threading.Lock()

    def emit(self, task_id: str, event_type: EventType, **data: Any) -> ProgressEvent:
        if not isinstance(event_type, EventType):
            event_type = EventType(event_type)
        with self._lock:
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

    def persist_answer(self, payload: dict[str, Any]) -> None:
        self.store.write_json(self.task_id, "answer.json", payload)

    def persist_result(self, result: dict[str, Any], *, rendered: str | None = None) -> None:
        payload = dict(result)
        if payload.get("valid") is True:
            answer = self._build_product_answer()
            if answer is not None:
                payload["answer"] = answer
                self.persist_answer(answer)
        self.store.write_json(self.task_id, "result.json", payload)
        if rendered is not None:
            self.store.write_text(self.task_id, "final.txt", rendered)

    def snapshot_control(self, task: Task, session: Session, catalog: EvidenceCatalog) -> None:
        self.persist_task(task)
        self.persist_session(session)
        self.persist_evidence(catalog)

    def _build_product_answer(self) -> dict[str, Any] | None:
        """Re-validate persisted claims before creating the product projection.

        This keeps Step 7 downstream of the existing trust boundary: answer.json
        is derived only from claims that still validate against the frozen
        Evidence snapshot. It never calls a model or tool.
        """

        try:
            claim_payload = self.store.read_json(self.task_id, "claims.json")
            evidence = self.store.read_json(self.task_id, "evidence.json")
        except (FileNotFoundError, OSError, ValueError):
            return None
        if not isinstance(claim_payload, dict) or not isinstance(evidence, dict):
            return None

        session_key = evidence.get("session_key")
        items = evidence.get("items")
        if not isinstance(session_key, str) or not isinstance(items, list):
            return None

        catalog = EvidenceCatalog(self.task_id, session_key)
        try:
            for row in items:
                if not isinstance(row, dict):
                    return None
                item = catalog.add(
                    source=row["source"],
                    raw=row["raw"],
                    tool_call_id=row.get("tool_call_id"),
                    metadata=row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
                )
                if item.ref != row.get("ref"):
                    return None
            normalized, _ = normalize_claim_payload(claim_payload)
            if validate_claim_payload(normalized, catalog):
                return None
            claims = claim_set_from_dict(normalized)
            return compose_product_answer(claims, catalog).to_dict()
        except (KeyError, TypeError, ValueError):
            return None
