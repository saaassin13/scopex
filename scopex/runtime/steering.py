from __future__ import annotations

from dataclasses import dataclass
import threading


@dataclass(frozen=True, slots=True)
class SteeringInstruction:
    index: int
    message: str


class PendingSteeringQueue:
    """Thread-safe FIFO of user steering instructions for the current task.

    Multiple messages received before the next safe model boundary are preserved
    in order and delivered together. The model is told to prefer later
    instructions when they conflict.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[SteeringInstruction] = []
        self._next_index = 1

    def push(self, message: str) -> SteeringInstruction:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("steering message is required")
        with self._lock:
            item = SteeringInstruction(self._next_index, message.strip())
            self._next_index += 1
            self._items.append(item)
            return item

    def drain(self) -> tuple[SteeringInstruction, ...]:
        with self._lock:
            items = tuple(self._items)
            self._items.clear()
            return items

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    @property
    def pending(self) -> tuple[SteeringInstruction, ...]:
        with self._lock:
            return tuple(self._items)

    def drain_message(self) -> str | None:
        items = self.drain()
        if not items:
            return None
        lines = [
            "用户在当前调查过程中追加了以下 steering 指令。保留已取得证据；若指令冲突，以较新的指令为准："
        ]
        for offset, item in enumerate(items, 1):
            lines.append(f"{offset}. {item.message}")
        return "\n".join(lines)
