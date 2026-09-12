from __future__ import annotations

from collections.abc import Callable
from typing import Any

from scopex.agent.model_proxy import StopBeforeForward
from scopex.events.observer import AgentProgressObserver
from scopex.runtime.stop import SafeStopGate, StopBoundary


class RuntimeRequestHook:
    """Bridge one proxy request into product progress and safe-stop control."""

    def __init__(
        self,
        observer: AgentProgressObserver,
        stop_gate: SafeStopGate,
        *,
        on_safe_stop: Callable[[StopBoundary], None] | None = None,
        request_validator: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.observer = observer
        self.stop_gate = stop_gate
        self.on_safe_stop = on_safe_stop
        self.request_validator = request_validator

    def __call__(self, request_index: int, payload: dict[str, Any]) -> None:
        if self.request_validator is not None:
            self.request_validator(payload)
        self.observer.observe_request(request_index, payload.get("messages", []))
        boundary = self.stop_gate.before_model_request(request_index)
        if boundary is None:
            return
        if self.on_safe_stop is not None:
            self.on_safe_stop(boundary)
        raise StopBeforeForward(
            f"safe stop before model request {boundary.request_index}: {boundary.reason}"
        )
