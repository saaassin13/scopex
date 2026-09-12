from __future__ import annotations

from typing import Any

from scopex.agent.trace import parse_messages, tool_target
from scopex.events.progress import EventSink, EventType


class AgentProgressObserver:
    """Turn observable OpenClaw/OpenAI transcript state into product progress."""

    def __init__(self, task_id: str, events: EventSink) -> None:
        self.task_id = task_id
        self.events = events
        self._seen_calls: set[str] = set()
        self._seen_results: set[str] = set()

    def seed(self, *, call_ids=(), result_ids=()) -> None:
        self._seen_calls.update(str(value) for value in call_ids)
        self._seen_results.update(str(value) for value in result_ids)

    def observe_request(self, request_index: int, messages: Any) -> None:
        self.events.emit(
            self.task_id,
            EventType.MODEL_REQUEST,
            request_index=request_index,
        )
        trace = parse_messages(messages)
        calls = trace.call_map
        results = trace.result_map

        for cid, call in calls.items():
            if cid in self._seen_calls:
                continue
            self._seen_calls.add(cid)
            self.events.emit(
                self.task_id,
                EventType.TOOL_CALL,
                tool_call_id=cid,
                tool=call.name,
                target=tool_target(call),
            )

        for cid, result in results.items():
            if cid in self._seen_results:
                continue
            self._seen_results.add(cid)
            call = calls.get(cid)
            self.events.emit(
                self.task_id,
                EventType.TOOL_RESULT,
                tool_call_id=cid,
                tool=call.name if call else None,
                target=tool_target(call) if call else None,
                result_chars=len(result.content),
            )

    @property
    def seen_call_ids(self) -> frozenset[str]:
        return frozenset(self._seen_calls)

    @property
    def seen_result_ids(self) -> frozenset[str]:
        return frozenset(self._seen_results)
