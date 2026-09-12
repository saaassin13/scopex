from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


@dataclass(frozen=True, slots=True)
class CliOutcome:
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    answer: str | None
    visible_payloads: int
    flags: dict[str, Any]

    @property
    def completed(self) -> bool:
        return not self.blockers and self.answer is not None


def parse_cli_outcome(text: str) -> CliOutcome:
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("unrecognized OpenClaw CLI JSON envelope") from exc
    if not isinstance(value, dict):
        raise ValueError("OpenClaw CLI result must be an object")

    meta = value.get("meta") or {}
    if not isinstance(meta, dict):
        raise ValueError("OpenClaw CLI meta must be an object")
    trace = meta.get("executionTrace") or {}
    if not isinstance(trace, dict):
        raise ValueError("OpenClaw executionTrace must be an object")

    blockers: list[str] = []
    for key in ("aborted", "replayInvalid"):
        if meta.get(key) is not None and type(meta[key]) is not bool:
            blockers.append("invalid_" + key + "_type")
    if trace.get("fallbackUsed") is not None and type(trace["fallbackUsed"]) is not bool:
        blockers.append("invalid_fallbackUsed_type")

    if meta.get("error") is not None:
        blockers.append("error")
    if meta.get("aborted") is True:
        blockers.append("aborted")
    if trace.get("fallbackUsed") is True:
        blockers.append("fallbackUsed")
    if meta.get("timeoutPhase") or meta.get("timedOut"):
        blockers.append("timeout")
    if meta.get("livenessState") in {"abandoned", "blocked", "paused"}:
        blockers.append("nonterminal_or_failed_liveness")

    rows = value.get("payloads")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("OpenClaw CLI payloads array missing or malformed")
    if any(row.get("isError") for row in rows):
        blockers.append("error_payload")

    visible = [
        row["text"]
        for row in rows
        if isinstance(row.get("text"), str)
        and row["text"].strip()
        and not row.get("isReasoning")
    ]
    if not visible:
        blockers.append("no_final_visible_answer")

    warnings = []
    if meta.get("replayInvalid") is True:
        warnings.append("replay_unsafe_do_not_auto_retry")

    flags = {
        "error": meta.get("error"),
        "aborted": meta.get("aborted"),
        "replayInvalid": meta.get("replayInvalid"),
        "livenessState": meta.get("livenessState"),
        "timeoutPhase": meta.get("timeoutPhase"),
        "fallbackUsed": trace.get("fallbackUsed"),
    }
    return CliOutcome(
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        answer=visible[-1] if visible else None,
        visible_payloads=len(visible),
        flags=flags,
    )
