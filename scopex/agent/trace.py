from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str | None
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_call_id: str
    content: str


@dataclass(frozen=True, slots=True)
class AgentTrace:
    calls: tuple[ToolCall, ...]
    results: tuple[ToolResult, ...]

    @property
    def call_map(self) -> dict[str, ToolCall]:
        return {call.id: call for call in self.calls}

    @property
    def result_map(self) -> dict[str, ToolResult]:
        return {result.tool_call_id: result for result in self.results}

    @property
    def completed_call_ids(self) -> frozenset[str]:
        return frozenset(self.call_map).intersection(self.result_map)


def text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            part.get("text", "")
            for part in value
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def _arguments(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str):
        return {}
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def parse_messages(messages: Any) -> AgentTrace:
    """Parse OpenAI-style assistant tool calls and tool-role results.

    Last occurrence wins for a repeated ID, matching the effective transcript
    semantics used by the validated POCs.
    """

    calls: dict[str, ToolCall] = {}
    results: dict[str, ToolResult] = {}
    if not isinstance(messages, list):
        return AgentTrace((), ())

    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "assistant":
            tool_calls = message.get("tool_calls") or []
            if not isinstance(tool_calls, list):
                continue
            for row in tool_calls:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                    continue
                function = row.get("function") or {}
                if not isinstance(function, dict):
                    function = {}
                cid = row["id"]
                calls[cid] = ToolCall(
                    id=cid,
                    name=function.get("name") if isinstance(function.get("name"), str) else None,
                    arguments=_arguments(function.get("arguments")),
                )
        elif role == "tool" and isinstance(message.get("tool_call_id"), str):
            cid = message["tool_call_id"]
            results[cid] = ToolResult(cid, text_content(message.get("content")))

    return AgentTrace(tuple(calls.values()), tuple(results.values()))


def load_audit_trace(audit_dir: Path) -> AgentTrace:
    """Aggregate all recorded OpenClaw requests for one turn into one trace."""

    calls: dict[str, ToolCall] = {}
    results: dict[str, ToolResult] = {}
    for path in sorted(Path(audit_dir).glob("wire-*-request.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        trace = parse_messages(payload.get("messages") if isinstance(payload, dict) else None)
        for call in trace.calls:
            calls[call.id] = call
        for result in trace.results:
            results[result.tool_call_id] = result
    return AgentTrace(tuple(calls.values()), tuple(results.values()))


def tool_target(call: ToolCall) -> str | None:
    """Return a compact UI-safe target when one is obvious.

    Shell command bodies are intentionally not surfaced here; Progress is an
    action/status channel, not a transcript or chain-of-thought channel.
    """

    for key in ("path", "file_path"):
        value = call.arguments.get(key)
        if isinstance(value, str) and value:
            return value
    return None
