from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    ref: str
    task_id: str
    session_key: str
    source: str
    raw: str
    tool_call_id: str | None
    observed_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "task_id": self.task_id,
            "session_key": self.session_key,
            "source": self.source,
            "raw": self.raw,
            "tool_call_id": self.tool_call_id,
            "observed_at": self.observed_at,
            "metadata": dict(self.metadata),
        }


class EvidenceCatalog:
    """Runtime-owned exact evidence identity.

    The model may reference E numbers, but it never owns or rewrites the raw
    evidence attached to those references. Whole-result evidence deduplicates by
    source/content as before. Fine-grained extractors can provide ``line_number``
    metadata; that position becomes part of identity so repeated equal text at
    different observed positions is not collapsed.
    """

    def __init__(self, task_id: str, session_key: str) -> None:
        self.task_id = task_id
        self.session_key = session_key
        self._items: list[EvidenceItem] = []
        self._by_ref: dict[str, EvidenceItem] = {}
        self._dedupe: dict[tuple[Any, ...], EvidenceItem] = {}

    @staticmethod
    def _dedupe_key(source: str, raw: str, metadata: dict[str, Any]) -> tuple[Any, ...]:
        line_number = metadata.get("line_number")
        if isinstance(line_number, int) and line_number > 0:
            return source, raw, "line_number", line_number
        return source, raw

    def add(
        self,
        *,
        source: str,
        raw: str,
        tool_call_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EvidenceItem:
        if not isinstance(source, str) or not source.strip():
            raise ValueError("evidence source is required")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("evidence raw content is required")
        metadata_value = dict(metadata or {})
        key = self._dedupe_key(source, raw, metadata_value)
        existing = self._dedupe.get(key)
        if existing is not None:
            return existing
        ref = f"E{len(self._items) + 1}"
        item = EvidenceItem(
            ref=ref,
            task_id=self.task_id,
            session_key=self.session_key,
            source=source,
            raw=raw,
            tool_call_id=tool_call_id,
            observed_at=utcnow(),
            metadata=metadata_value,
        )
        self._items.append(item)
        self._by_ref[ref] = item
        self._dedupe[key] = item
        return item

    def get(self, ref: str) -> EvidenceItem:
        try:
            return self._by_ref[ref]
        except KeyError as exc:
            raise KeyError(f"unknown evidence ref: {ref}") from exc

    def has(self, ref: str) -> bool:
        return ref in self._by_ref

    @property
    def refs(self) -> frozenset[str]:
        return frozenset(self._by_ref)

    @property
    def items(self) -> tuple[EvidenceItem, ...]:
        return tuple(self._items)

    def snapshot(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "session_key": self.session_key,
            "items": [item.to_dict() for item in self._items],
        }
