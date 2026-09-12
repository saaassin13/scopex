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
    encoded here. Completed calls are processed in trace order so E references
    remain deterministic across runs.
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
        results = trace.result_map
        added: list[EvidenceItem] = []
        for call in trace.calls:
            call_id = call.id
            if call_id not in results or call_id in self._processed_call_ids:
                continue
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
    """Opt-in generic extractor that preserves a bounded whole read result.

    Whole-result evidence is useful for short structured files, but log-like text
    should normally use ``ReadLineExtractor`` so claims can bind to exact events.
    This extractor remains opt-in and is never installed implicitly by Runtime.
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


class ReadLineExtractor:
    """Opt-in generic extractor that preserves non-empty read results line-by-line.

    This is intentionally format-agnostic: it does not parse timestamps, log
    levels, device names, error codes or business semantics. It only establishes
    a finer evidence identity so a claim can cite one exact observed line rather
    than an entire file. Source line numbers are 1-based within the returned read
    content and are stored as provenance metadata.
    """

    def __init__(
        self,
        *,
        max_lines: int = 512,
        max_line_chars: int = 4096,
        tool_names: tuple[str, ...] = ("read",),
    ) -> None:
        if max_lines <= 0:
            raise ValueError("max_lines must be positive")
        if max_line_chars <= 0:
            raise ValueError("max_line_chars must be positive")
        self.max_lines = max_lines
        self.max_line_chars = max_line_chars
        self.tool_names = frozenset(tool_names)

    def extract(self, call: ToolCall, result: ToolResult) -> Iterable[EvidenceCandidate]:
        if call.name not in self.tool_names:
            return ()
        target = tool_target(call)
        if not target or not result.content.strip():
            return ()

        candidates: list[EvidenceCandidate] = []
        nonempty_seen = 0
        for line_number, raw_line in enumerate(result.content.splitlines(), 1):
            if not raw_line.strip():
                continue
            nonempty_seen += 1
            if nonempty_seen > self.max_lines:
                break
            truncated = len(raw_line) > self.max_line_chars
            line = raw_line[: self.max_line_chars] if truncated else raw_line
            candidates.append(
                EvidenceCandidate(
                    source=target,
                    raw=line,
                    metadata={
                        "line_number": line_number,
                        "line_truncated": truncated,
                        "original_line_chars": len(raw_line),
                    },
                )
            )
        return tuple(candidates)
