from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scopex.agent.model_proxy import RequestRejected


@dataclass(frozen=True, slots=True)
class OpenClawRequestPolicy:
    model_id: str
    max_tokens: int
    approved_tools: frozenset[str] = frozenset({"read", "exec", "process"})

    def validate(self, payload: dict[str, Any]) -> None:
        problems: list[str] = []
        if payload.get("model") != self.model_id:
            problems.append("model mismatch")

        thinking = payload.get("chat_template_kwargs")
        if not isinstance(thinking, dict) or thinking.get("enable_thinking") is not False:
            problems.append("thinking must be disabled")

        if payload.get("max_tokens") != self.max_tokens:
            problems.append("max_tokens mismatch")
        if "max_completion_tokens" in payload:
            problems.append("conflicting max_completion_tokens")

        rows = payload.get("tools")
        if not isinstance(rows, list):
            problems.append("tools missing")
        else:
            names = []
            for row in rows:
                if not isinstance(row, dict):
                    names.append(None)
                    continue
                function = row.get("function") or {}
                names.append(function.get("name") if isinstance(function, dict) else None)
            if len(names) != len(set(names)) or set(names) != set(self.approved_tools):
                problems.append("tool surface mismatch")

        if payload.get("tool_choice") not in (None, "auto"):
            problems.append("tool_choice must be auto/omitted")

        if problems:
            raise RequestRejected("; ".join(problems))
