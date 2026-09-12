from __future__ import annotations

from dataclasses import dataclass
import threading


@dataclass(frozen=True, slots=True)
class StopBoundary:
    request_index: int
    reason: str


class SafeStopGate:
    """Controller-side stop flag applied before the next model request.

    v0.1 semantics are deliberately cooperative at the step boundary: an
    already-running model/tool step is allowed to finish; the next model request
    is blocked. Hard cancellation of an in-flight process is a separate feature.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requested = False
        self._reason = "user_stop"
        self._reached: StopBoundary | None = None

    def request(self, reason: str = "user_stop") -> None:
        with self._lock:
            if self._reached is not None:
                return
            self._requested = True
            self._reason = reason or "user_stop"

    def before_model_request(self, request_index: int) -> StopBoundary | None:
        with self._lock:
            if self._reached is not None:
                return self._reached
            if not self._requested:
                return None
            self._reached = StopBoundary(request_index=request_index, reason=self._reason)
            return self._reached

    def reset_for_resume(self) -> None:
        """Clear the previous stop generation before a user resumes the task."""
        with self._lock:
            self._requested = False
            self._reason = "user_stop"
            self._reached = None

    @property
    def requested(self) -> bool:
        with self._lock:
            return self._requested

    @property
    def reached(self) -> StopBoundary | None:
        with self._lock:
            return self._reached
