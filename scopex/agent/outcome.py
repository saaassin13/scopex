from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


NATIVE_TOOL_LOOP_GUARD = "native_tool_loop_guard"


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


def _cli_value(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("unrecognized OpenClaw CLI JSON envelope") from exc
    if not isinstance(value, dict):
        raise ValueError("OpenClaw CLI result must be an object")
    return value


def classify_cli_runtime_guard(text: str) -> str | None:
    """Classify a framework-owned terminal guard from the CLI JSON envelope.

    This intentionally recognizes only OpenClaw terminal shapes observed or
    documented as tool-loop guards. It is not a generic error-message mapper and
    it does not decide what the Agent should do next.
    """

    try:
        value = _cli_value(text)
    except ValueError:
        return None
    meta = value.get("meta")
    if not isinstance(meta, dict):
        return None
    error = meta.get("error")
    if isinstance(error, dict):
        message = error.get("message")
    else:
        message = error
    if not isinstance(message, str):
        return None
    normalized = message.strip().lower()
    if (
        "tool-loop recovery encountered another critical loop" in normalized
        or "compaction_loop_persisted" in normalized
    ):
        return NATIVE_TOOL_LOOP_GUARD
    return None


def parse_cli_outcome(text: str) -> CliOutcome:
    value = _cli_value(text)

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
    # Native 2026.9.2 includes this terminal field even when it emits a visible
    # truncation notice. Do not confuse text in payloads with a completed turn.
    stop_reason = meta.get("stopReason")
    if stop_reason is not None and stop_reason not in ("stop", "end_turn"):
        blockers.append("nonfinal_stop_reason")

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
        "stopReason": stop_reason,
    }
    partial_text = meta.get("finalAssistantVisibleText") if stop_reason == "length" else None
    answer = partial_text if isinstance(partial_text, str) and partial_text.strip() else visible[-1] if visible else None
    if meta.get("error") is not None:
        # Error envelopes may put a framework notice in ordinary text payloads.
        # Only an explicitly identified assistant draft is safe to publish.
        draft = meta.get("finalAssistantVisibleText")
        answer = draft if isinstance(draft, str) and draft.strip() else None
    return CliOutcome(
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        answer=answer,
        visible_payloads=len(visible),
        flags=flags,
    )
