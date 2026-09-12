from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionTurnKind(str, Enum):
    USER = "USER"
    STEER = "STEER"
    STOP = "STOP"
    RESUME = "RESUME"


@dataclass(frozen=True, slots=True)
class SessionTurn:
    index: int
    kind: SessionTurnKind
    content: str
    created_at: str


@dataclass(slots=True)
class Session:
    """ScopeX-owned control history for an OpenClaw session.

    OpenClaw remains the owner of the full agent/tool transcript. ScopeX stores
    only user/control turns required to audit task steering, stop and resume.
    """

    task_id: str
    session_key: str
    turns: list[SessionTurn] = field(default_factory=list)

    def append(self, kind: SessionTurnKind, content: str = "") -> SessionTurn:
        if not isinstance(kind, SessionTurnKind):
            kind = SessionTurnKind(kind)
        if not isinstance(content, str):
            raise TypeError("session turn content must be a string")
        turn = SessionTurn(
            index=len(self.turns) + 1,
            kind=kind,
            content=content,
            created_at=utcnow(),
        )
        self.turns.append(turn)
        return turn

    def user(self, content: str) -> SessionTurn:
        return self.append(SessionTurnKind.USER, content)

    def steer(self, content: str) -> SessionTurn:
        return self.append(SessionTurnKind.STEER, content)

    def stop(self, content: str = "") -> SessionTurn:
        return self.append(SessionTurnKind.STOP, content)

    def resume(self, content: str) -> SessionTurn:
        return self.append(SessionTurnKind.RESUME, content)

    def snapshot(self) -> dict:
        return {
            "task_id": self.task_id,
            "session_key": self.session_key,
            "turns": [
                {
                    "index": turn.index,
                    "kind": turn.kind.value,
                    "content": turn.content,
                    "created_at": turn.created_at,
                }
                for turn in self.turns
            ],
        }
