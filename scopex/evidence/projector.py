from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import mimetypes
from pathlib import Path, PurePosixPath
from typing import Protocol

from scopex.agent.trace import AgentTrace, ToolCall, ToolResult, tool_target
from scopex.evidence.catalog import EvidenceItem
from scopex.evidence.collector import EvidenceCollector


_OPENCLAW_LOOP_WARNING_PREFIX = "[System note: Tool-loop warning after "
_OPENCLAW_LOOP_RECOVERY_PREFIX = "Do not repeat this exact tool action."
_INTERNAL_READ_PREFIXES = (
    "/workspace/skills/",
    "/task-scratch/",
)
_INTERNAL_READ_EXACT = {
    "/workspace/scopex-data-catalog.json",
}


def _is_openclaw_runtime_control_line(raw_line: str) -> bool:
    line = raw_line.strip()
    if line.startswith(_OPENCLAW_LOOP_WARNING_PREFIX):
        return True
    if line.startswith(_OPENCLAW_LOOP_RECOVERY_PREFIX):
        return True
    if line.startswith("CRITICAL:"):
        lowered = line.lower()
        return (
            "session execution blocked" in lowered
            and ("runaway loop" in lowered or "global circuit breaker" in lowered)
        )
    return False


def _is_internal_read_target(target: str) -> bool:
    if target in _INTERNAL_READ_EXACT:
        return True
    return any(target.startswith(prefix) for prefix in _INTERNAL_READ_PREFIXES)


class EvidenceProjector(Protocol):
    @property
    def processed_call_ids(self) -> frozenset[str]: ...

    def process_trace(self, trace: AgentTrace) -> tuple[EvidenceItem, ...]: ...


@dataclass(frozen=True, slots=True)
class ResolvedBoundPath:
    agent_path: str
    host_path: Path


class DataBindResolver:
    """Resolve sandbox-visible paths back to configured read-only host roots."""

    def __init__(self, binds: tuple[str, ...] = ()) -> None:
        roots: list[tuple[Path, PurePosixPath]] = []
        for value in binds:
            parts = value.rsplit(":", 2)
            if len(parts) != 3:
                raise ValueError("sandbox bind must use host:container:mode format")
            host, target, mode = parts
            if mode != "ro":
                raise ValueError("evidence resolver accepts read-only binds only")
            host_root = Path(host).resolve()
            target_root = PurePosixPath(target)
            if not host_root.is_absolute() or not target_root.is_absolute():
                raise ValueError("bind roots must be absolute")
            roots.append((host_root, target_root))
        self._roots = tuple(sorted(roots, key=lambda row: len(str(row[1])), reverse=True))

    def resolve(self, agent_path: str) -> ResolvedBoundPath | None:
        if not isinstance(agent_path, str) or not agent_path.startswith("/"):
            return None
        candidate = PurePosixPath(agent_path)
        for host_root, target_root in self._roots:
            try:
                relative = candidate.relative_to(target_root)
            except ValueError:
                continue
            host_candidate = (host_root / Path(*relative.parts)).resolve()
            try:
                host_candidate.relative_to(host_root)
            except ValueError:
                return None
            if host_candidate.is_file():
                return ResolvedBoundPath(agent_path=agent_path, host_path=host_candidate)
            return None
        return None


class OpenClawEvidenceProjector:
    """Project claim-grade business observations from OpenClaw trace.

    The full OpenClaw transcript remains the investigation record. Workspace
    Skills, data-catalog text and task-scratch intermediates are operational
    context and are intentionally not promoted to claim-grade Evidence.

    Stable ScopeX scripts can emit a compact JSON object with
    ``scopex_role=business_facts``. That object becomes one structured Evidence
    item instead of hundreds of line Evidence refs. ``scopex_role=locator`` is
    routing/working-set metadata and remains Trace-only.
    """

    def __init__(
        self,
        collector: EvidenceCollector,
        *,
        exec_host: str = "sandbox",
        sandbox_binds: tuple[str, ...] = (),
        max_read_lines: int = 512,
        max_read_line_chars: int = 4096,
        max_exec_lines: int = 256,
        max_exec_line_chars: int = 4096,
        max_exec_chars: int = 12_000,
        max_claim_images: int = 4,
    ) -> None:
        if (
            max_read_lines <= 0
            or max_read_line_chars <= 0
            or max_exec_lines <= 0
            or max_exec_line_chars <= 0
            or max_exec_chars <= 0
            or max_claim_images <= 0
        ):
            raise ValueError("projection limits must be positive")
        self.collector = collector
        self.exec_host = exec_host
        self.bind_resolver = DataBindResolver(sandbox_binds)
        self.max_read_lines = max_read_lines
        self.max_read_line_chars = max_read_line_chars
        self.max_exec_lines = max_exec_lines
        self.max_exec_line_chars = max_exec_line_chars
        self.max_exec_chars = max_exec_chars
        self.max_claim_images = max_claim_images
        self._processed_call_ids: set[str] = set()

    @property
    def processed_call_ids(self) -> frozenset[str]:
        return frozenset(self._processed_call_ids)

    def process_trace(self, trace: AgentTrace) -> tuple[EvidenceItem, ...]:
        results = trace.result_map
        added: list[EvidenceItem] = []
        for call in trace.calls:
            if call.id in self._processed_call_ids or call.id not in results:
                continue
            result = results[call.id]
            if call.name == "read":
                added.extend(self._project_read(call, result))
            elif call.name == "exec":
                added.extend(self._project_exec(call, result))
            elif call.name == "view_image":
                added.extend(self._project_images(call))
            self._processed_call_ids.add(call.id)
        return tuple(added)

    def _project_read(self, call: ToolCall, result: ToolResult) -> tuple[EvidenceItem, ...]:
        target = tool_target(call)
        if not target or not result.content.strip() or _is_internal_read_target(target):
            return ()
        added: list[EvidenceItem] = []
        nonempty_seen = 0
        for line_number, raw_line in enumerate(result.content.splitlines(), 1):
            if not raw_line.strip() or _is_openclaw_runtime_control_line(raw_line):
                continue
            nonempty_seen += 1
            if nonempty_seen > self.max_read_lines:
                break
            truncated = len(raw_line) > self.max_read_line_chars
            line = raw_line[: self.max_read_line_chars] if truncated else raw_line
            added.append(
                self.collector.add(
                    source=target,
                    raw=line,
                    tool_call_id=call.id,
                    metadata={
                        "evidence_type": "file_line",
                        "tool": "read",
                        "line_number": line_number,
                        "line_truncated": truncated,
                        "original_line_chars": len(raw_line),
                    },
                )
            )
        return tuple(added)

    @staticmethod
    def _structured_payload(content: str) -> dict | None:
        text = content.strip()
        if not text.startswith("{"):
            return None
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def _compact_business_facts(self, payload: dict) -> str:
        keep: dict = {}
        for key in (
            "scopex_role",
            "schema",
            "source",
            "window",
            "facts",
            "summary",
            "quality",
            "candidate_events_total",
            "top_candidates",
            "logs",
            "per_file_matching_samples",
            "details_out",
            "events_out",
        ):
            if key in payload:
                keep[key] = payload[key]
        if isinstance(keep.get("top_candidates"), list):
            keep["top_candidates"] = keep["top_candidates"][:10]
        text = json.dumps(keep, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if len(text) <= self.max_exec_chars:
            return text
        # Prefer facts/KPI over verbose provenance when the structured helper
        # unexpectedly exceeds the projector bound.
        for key in ("logs", "per_file_matching_samples", "top_candidates", "quality"):
            keep.pop(key, None)
            text = json.dumps(keep, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            if len(text) <= self.max_exec_chars:
                return text
        return text[: self.max_exec_chars]

    def _project_exec(self, call: ToolCall, result: ToolResult) -> tuple[EvidenceItem, ...]:
        content = result.content
        if not content.strip():
            return ()

        digest = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
        command = call.arguments.get("command")
        title = call.arguments.get("title")
        call_host = call.arguments.get("host")
        actual_host = call_host if isinstance(call_host, str) and call_host else self.exec_host

        structured = self._structured_payload(content)
        if structured is not None:
            role = structured.get("scopex_role")
            if role == "locator":
                return ()
            if role == "business_facts":
                raw = self._compact_business_facts(structured)
                return (
                    self.collector.add(
                        source=f"business_facts:{structured.get('source') or call.id}",
                        raw=raw,
                        tool_call_id=call.id,
                        metadata={
                            "evidence_type": "structured_business_facts",
                            "evidence_role": "business_facts",
                            "tool": "exec",
                            "exec_host": actual_host,
                            "command": command if isinstance(command, str) else None,
                            "title": title if isinstance(title, str) else None,
                            "result_sha256": digest,
                            "original_result_chars": len(content),
                        },
                    ),
                )

        added: list[EvidenceItem] = []
        nonempty_seen = 0
        projected_chars = 0
        output_truncated = False
        for line_number, raw_line in enumerate(content.splitlines(), 1):
            if not raw_line.strip() or _is_openclaw_runtime_control_line(raw_line):
                continue
            if nonempty_seen >= self.max_exec_lines or projected_chars >= self.max_exec_chars:
                output_truncated = True
                break
            nonempty_seen += 1
            remaining = self.max_exec_chars - projected_chars
            per_line_limit = min(self.max_exec_line_chars, remaining)
            if per_line_limit <= 0:
                output_truncated = True
                break
            line_truncated = len(raw_line) > per_line_limit
            line = raw_line[:per_line_limit] if line_truncated else raw_line
            projected_chars += len(line)
            if line_truncated:
                output_truncated = True
            added.append(
                self.collector.add(
                    source=f"exec:{call.id}",
                    raw=line,
                    tool_call_id=call.id,
                    metadata={
                        "evidence_type": "command_line",
                        "tool": "exec",
                        "exec_host": actual_host,
                        "command": command if isinstance(command, str) else None,
                        "title": title if isinstance(title, str) else None,
                        "line_number": line_number,
                        "line_truncated": line_truncated,
                        "original_line_chars": len(raw_line),
                        "result_sha256": digest,
                        "original_result_chars": len(content),
                        "result_projection_truncated": output_truncated,
                    },
                )
            )
        return tuple(added)

    @staticmethod
    def _image_paths(call: ToolCall) -> tuple[str, ...]:
        paths: list[str] = []
        one = call.arguments.get("path")
        if isinstance(one, str) and one:
            paths.append(one)
        many = call.arguments.get("paths")
        if isinstance(many, list):
            paths.extend(value for value in many if isinstance(value, str) and value)
        return tuple(dict.fromkeys(paths))

    def _project_images(self, call: ToolCall) -> tuple[EvidenceItem, ...]:
        paths = self._image_paths(call)
        if not paths or len(paths) > self.max_claim_images:
            return ()
        prompt = call.arguments.get("prompt")
        added: list[EvidenceItem] = []
        for path in paths:
            resolved = self.bind_resolver.resolve(path)
            if resolved is None:
                continue
            digest = self._sha256_file(resolved.host_path)
            media_type = mimetypes.guess_type(resolved.host_path.name)[0] or "application/octet-stream"
            stat = resolved.host_path.stat()
            added.append(
                self.collector.add(
                    source=path,
                    raw=f"image:{Path(path).name}",
                    tool_call_id=call.id,
                    metadata={
                        "evidence_type": "image",
                        "tool": "view_image",
                        "sha256": digest,
                        "byte_size": stat.st_size,
                        "media_type": media_type,
                        "view_prompt": prompt if isinstance(prompt, str) else None,
                        "evidence_role": "claim_grade_bounded_image_set",
                        "view_set_size": len(paths),
                    },
                )
            )
        return tuple(added)

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()
