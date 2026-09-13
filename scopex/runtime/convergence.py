from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConvergencePolicy:
    """Product-level convergence policy only.

    Hard budgets such as model-request count, task timeout, exec timeout and
    model context window are enforced by the OpenClaw/ModelProxy runtime that
    actually owns those resources. ScopeX convergence must not duplicate those
    limits with a second set of approximate counters.
    """

    max_stale_rounds: int = 3


@dataclass(frozen=True, slots=True)
class ConvergenceSnapshot:
    # The operational metrics remain available for audit/measurement. They are
    # intentionally not interpreted as hard stop budgets here.
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
    """Evaluate only product-level convergence signals.

    OpenClaw remains responsible for the investigation path. ScopeX may stop a
    task because the goal is already satisfied or because repeated rounds are
    no longer producing new claim-grade information. Resource budgets are
    enforced closer to the resource owner and are handled separately from this
    convergence decision.
    """

    reasons: list[str] = []
    if snapshot.goal_satisfied:
        reasons.append("goal_satisfied")
    if snapshot.stale_rounds >= policy.max_stale_rounds:
        reasons.append("no_new_evidence")
    return ConvergenceDecision(bool(reasons), tuple(reasons))
