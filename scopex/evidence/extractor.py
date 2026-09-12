from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from scopex.agent.trace import AgentTrace, ToolCall, ToolResult, tool_target
from scopex.evidence.catalog import EvidenceItem
from scopex.evidence.collector import EvidenceCollector


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    """Extractor-produced candidate before Runtime assigns an E reference."""

    source: str
    raw: str
    metadata: dict[str, Any] = field(default_factory=dict)


class EvidenceExtractor(Protocol):
    """Decide what from one completed tool call is worth preserving as evidence."""

    def extract(self, call: ToolCall, result: ToolResult) -> Iterable[EvidenceCandidate]: ...


class EvidenceExtractionPipeline:
    """Run configured extractors once per completed tool call.

    Runtime owns processing/deduplication and provenance. Extractors own only
    selection/normalization of candidate material. No business diagnosis path is
    encoded here.
    """

    def __init__(
        self,
        collector: EvidenceCollector,
        extractors: Iterable[EvidenceExtractor] = (),
    ) -> None:
        self.collector = collector
        self.extractors = tuple(extractors)
        self._processed_call_ids: set[str] = set()

    @property
    def processed_call_ids(self) -> frozenset[str]:
        return frozenset(self._processed_call_ids)

    def process_trace(self, trace: AgentTrace) -> tuple[EvidenceItem, ...]:
        calls = trace.call_map
        results = trace.result_map
        added: list[EvidenceItem] = []
        for call_id in trace.completed_call_ids:
            if call_id in self._processed_call_ids:
                continue
            call = calls[call_id]
            result = results[call_id]
            for extractor in self.extractors:
                for candidate in extractor.extract(call, result):
                    if not isinstance(candidate, EvidenceCandidate):
                        raise TypeError("evidence extractor must yield EvidenceCandidate")
                    metadata = dict(candidate.metadata)
                    metadata.setdefault("tool", call.name)
                    target = tool_target(call)
                    if target:
                        metadata.setdefault("tool_target", target)
                    item = self.collector.add(
                        source=candidate.source,
                        raw=candidate.raw,
                        tool_call_id=call.id,
                        metadata=metadata,
                    )
                    added.append(item)
            self._processed_call_ids.add(call_id)
        return tuple(added)


class ReadResultExtractor:
    """Opt-in generic extractor that preserves bounded read-tool results.

    This is intentionally NOT installed by default. A product/Skill can choose
    it for data sources where the whole bounded read result is meaningful.
    """

    def __init__(self, *, max_chars: int = 16384, tool_names: tuple[str, ...] = ("read",)) -> None:
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        self.max_chars = max_chars
        self.tool_names = frozenset(tool_names)

    def extract(self, call: ToolCall, result: ToolResult) -> Iterable[EvidenceCandidate]:
        if call.name not in self.tool_names:
            return ()
        target = tool_target(call)
        if not target or not result.content.strip():
            return ()
        raw = result.content
        truncated = len(raw) > self.max_chars
        if truncated:
            raw = raw[: self.max_chars]
        return (
            EvidenceCandidate(
                source=target,
                raw=raw,
                metadata={"truncated": truncated, "original_chars": len(result.content)},
            ),
        )
