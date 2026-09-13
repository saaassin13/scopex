from __future__ import annotations

from dataclasses import dataclass
import hashlib
import mimetypes
from pathlib import Path, PurePosixPath
from typing import Protocol

from scopex.agent.trace import AgentTrace, ToolCall, ToolResult, tool_target
from scopex.evidence.catalog import EvidenceItem
from scopex.evidence.collector import EvidenceCollector


class EvidenceProjector(Protocol):
    @property
    def processed_call_ids(self) -> frozenset[str]: ...

    def process_trace(self, trace: AgentTrace) -> tuple[EvidenceItem, ...]: ...


@dataclass(frozen=True, slots=True)
class ResolvedBoundPath:
    agent_path: str
    host_path: Path


class DataBindResolver:
    """Resolve sandbox-visible paths back to configured read-only host roots.

    This is provenance plumbing only. It never discovers files and never grants
    access outside the explicit `HOST:AGENT:ro` bind set.
    """

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
    """Project claim-grade source material from OpenClaw trace into Evidence.

    OpenClaw remains the source of truth for the full transcript. This class
    freezes only minimal material needed for stable citation. It contains no
    business diagnosis logic and does not execute tools.
    """

    def __init__(
        self,
        collector: EvidenceCollector,
        *,
        exec_host: str = "sandbox",
        sandbox_binds: tuple[str, ...] = (),
        max_read_lines: int = 512,
        max_read_line_chars: int = 4096,
        max_exec_chars: int = 12_000,
    ) -> None:
        if max_read_lines <= 0 or max_read_line_chars <= 0 or max_exec_chars <= 0:
            raise ValueError("projection limits must be positive")
        self.collector = collector
        self.exec_host = exec_host
        self.bind_resolver = DataBindResolver(sandbox_binds)
        self.max_read_lines = max_read_lines
        self.max_read_line_chars = max_read_line_chars
        self.max_exec_chars = max_exec_chars
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
        if not target or not result.content.strip():
            return ()
        added: list[EvidenceItem] = []
        nonempty_seen = 0
        for line_number, raw_line in enumerate(result.content.splitlines(), 1):
            if not raw_line.strip():
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

    def _project_exec(self, call: ToolCall, result: ToolResult) -> tuple[EvidenceItem, ...]:
        content = result.content
        if not content.strip():
            return ()
        digest = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
        truncated = len(content) > self.max_exec_chars
        excerpt = self._bounded_excerpt(content, self.max_exec_chars)
        command = call.arguments.get("command")
        title = call.arguments.get("title")
        item = self.collector.add(
            source=f"exec:{call.id}",
            raw=excerpt,
            tool_call_id=call.id,
            metadata={
                "evidence_type": "command_output",
                "tool": "exec",
                "exec_host": self.exec_host,
                "command": command if isinstance(command, str) else None,
                "title": title if isinstance(title, str) else None,
                "result_sha256": digest,
                "original_chars": len(content),
                "truncated": truncated,
            },
        )
        return (item,)

    def _project_images(self, call: ToolCall) -> tuple[EvidenceItem, ...]:
        paths: list[str] = []
        one = call.arguments.get("path")
        if isinstance(one, str) and one:
            paths.append(one)
        many = call.arguments.get("paths")
        if isinstance(many, list):
            paths.extend(value for value in many if isinstance(value, str) and value)

        prompt = call.arguments.get("prompt")
        added: list[EvidenceItem] = []
        seen_paths: set[str] = set()
        for path in paths:
            if path in seen_paths:
                continue
            seen_paths.add(path)
            resolved = self.bind_resolver.resolve(path)
            if resolved is None:
                # Strong image evidence requires immutable identity. A tool call
                # alone is not enough if ScopeX cannot resolve/hash the file.
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
                    },
                )
            )
        return tuple(added)

    @staticmethod
    def _bounded_excerpt(content: str, limit: int) -> str:
        if len(content) <= limit:
            return content
        marker = "\n... [truncated; full result retained in OpenClaw audit] ...\n"
        remaining = max(2, limit - len(marker))
        head = remaining // 2
        tail = remaining - head
        return content[:head] + marker + content[-tail:]

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
