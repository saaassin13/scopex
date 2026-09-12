from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class AgentTurnRequest:
    task_id: str
    session_key: str
    message: str
    timeout_s: int
    work_dir: Path


@dataclass(frozen=True, slots=True)
class AgentTurnResult:
    returncode: int | None
    stop_reason: str | None
    answer: str | None
    audit_dir: Path


class AgentAdapter(Protocol):
    """Boundary between ScopeX control plane and an agent-loop engine."""

    def run_turn(self, request: AgentTurnRequest) -> AgentTurnResult: ...
