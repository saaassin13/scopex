from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConvergencePolicy:
    max_model_requests: int = 8
    max_tool_calls: int = 20
    max_elapsed_s: float = 300.0
    max_context_chars: int = 64_000
    max_stale_rounds: int = 3


@dataclass(frozen=True, slots=True)
class ConvergenceSnapshot:
    model_requests: int
    tool_calls: int
    elapsed_s: float
    context_chars: int
    stale_rounds: int = 0
    goal_satisfied: bool = False


@dataclass(frozen=True, slots=True)
class ConvergenceDecision:
    should_finalize: bool
    reasons: tuple[str, ...]


def evaluate(policy: ConvergencePolicy, snapshot: ConvergenceSnapshot) -> ConvergenceDecision:
    """Evaluate generic runtime stop/finalize conditions.

    This function intentionally contains no business semantics. A caller may set
    ``goal_satisfied`` after its own evidence/acceptance logic is satisfied.
    """

    reasons: list[str] = []
    if snapshot.goal_satisfied:
        reasons.append("goal_satisfied")
    if snapshot.model_requests >= policy.max_model_requests:
        reasons.append("model_request_budget")
    if snapshot.tool_calls >= policy.max_tool_calls:
        reasons.append("tool_call_budget")
    if snapshot.elapsed_s >= policy.max_elapsed_s:
        reasons.append("elapsed_budget")
    if snapshot.context_chars >= policy.max_context_chars:
        reasons.append("context_budget")
    if snapshot.stale_rounds >= policy.max_stale_rounds:
        reasons.append("no_new_evidence")
    return ConvergenceDecision(bool(reasons), tuple(reasons))
