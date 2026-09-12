from __future__ import annotations

from typing import Any

from scopex.evidence.catalog import EvidenceCatalog, EvidenceItem
from scopex.events.progress import EventSink, EventType


class EvidenceCollector:
    """Add already-observed source material to the runtime evidence catalog.

    Extraction/selection is deliberately outside this class. Skills, tool
    adapters or domain-specific extractors may decide *what* is evidence; this
    class owns stable identity, provenance and progress emission.
    """

    def __init__(self, catalog: EvidenceCatalog, events: EventSink) -> None:
        self.catalog = catalog
        self.events = events

    def add(
        self,
        *,
        source: str,
        raw: str,
        tool_call_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EvidenceItem:
        before = self.catalog.refs
        item = self.catalog.add(
            source=source,
            raw=raw,
            tool_call_id=tool_call_id,
            metadata=metadata,
        )
        if item.ref not in before:
            self.events.emit(
                self.catalog.task_id,
                EventType.EVIDENCE_ADDED,
                ref=item.ref,
                source=item.source,
                tool_call_id=item.tool_call_id,
            )
        return item
