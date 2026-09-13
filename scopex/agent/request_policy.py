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
        """Validate model/runtime invariants without blocking OpenClaw utility calls.

        Normal agent requests usually expose the configured tool surface, while
        OpenClaw's internal utility requests (notably compaction summarization)
        may intentionally omit ``tools``. Missing tools are capability-reducing,
        not capability-expanding, so they are safe to forward. Whenever tools
        are present, every exposed tool must still belong to the configured
        allowlist and duplicate/malformed definitions remain rejected.
        """

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
        if rows is not None:
            if not isinstance(rows, list):
                problems.append("tools malformed")
            else:
                names: list[str | None] = []
                for row in rows:
                    if not isinstance(row, dict):
                        names.append(None)
                        continue
                    function = row.get("function") or {}
                    name = function.get("name") if isinstance(function, dict) else None
                    names.append(name if isinstance(name, str) and name else None)

                valid_names = [name for name in names if name is not None]
                if (
                    len(valid_names) != len(names)
                    or len(valid_names) != len(set(valid_names))
                    or not set(valid_names).issubset(self.approved_tools)
                ):
                    problems.append("tool surface mismatch")

        if payload.get("tool_choice") not in (None, "auto"):
            problems.append("tool_choice must be auto/omitted")

        if problems:
            raise RequestRejected("; ".join(problems))
