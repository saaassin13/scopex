from __future__ import annotations

from collections.abc import Callable
from typing import Any
import json
import shlex
from scopex.agent.trace import parse_messages

from scopex.agent.model_proxy import StopBeforeForward, LocalTerminalAnswer
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
        self.no_data: dict[str, Any] | None = None

    def __call__(self, request_index: int, payload: dict[str, Any]) -> None:
        if self.request_validator is not None:
            self.request_validator(payload)
        self.observer.observe_request(request_index, payload.get("messages", []))
        boundary = self.stop_gate.before_model_request(request_index)
        if boundary is None:
            if self.no_data is None:
                trace = parse_messages(payload.get('messages', []))
                for result in trace.results:
                    call = trace.call_map.get(result.tool_call_id)
                    if call is None or call.name != 'exec':
                        continue
                    try:
                        args = shlex.split(call.arguments.get('command', ''))
                        data = json.loads(result.content)
                    except (ValueError, TypeError):
                        continue
                    if (len(args) < 2 or args[0] != 'python3'
                            or args[1] != '/workspace/skills/data-locator/scripts/data_locator.py'
                            or not isinstance(data, dict) or data.get('scopex_role') != 'locator'
                            or data.get('status') != 'no_data' or data.get('matching_count') != 0
                            or data.get('files') != []):
                        continue
                    window = data.get('window')
                    if not isinstance(window, dict):
                        continue
                    expected = {'--source': data.get('source'), '--start': window.get('start'), '--end': window.get('end')}
                    if not all(isinstance(v, str) and args.count(k) == 1 and
                               args.index(k) + 1 < len(args) and args[args.index(k) + 1] == v
                               for k, v in expected.items()):
                        continue
                    # Reject shell wrappers/compound commands; only the known locator contract.
                    allowed = {'--source', '--start', '--end', '--kind', '--max-files'}
                    tail = args[2:]
                    if len(tail) % 2 or any(tail[i] not in allowed for i in range(0, len(tail), 2)):
                        continue
                    self.no_data = {'source': data['source'], 'window': window, 'tool_call_id': call.id}
                    break
            if self.no_data is not None:
                window = self.no_data['window']
                raise LocalTerminalAnswer(
                    f"指定时间范围 {window['start']} 至 {window['end']} 内，"
                    f"数据源 {self.no_data['source']} 未找到匹配数据。本次检查已结束，"
                    "未改用其他时间的数据，也未作正常或异常判断。"
                )
            return
        if self.on_safe_stop is not None:
            self.on_safe_stop(boundary)
        raise StopBeforeForward(
            f"safe stop before model request {boundary.request_index}: {boundary.reason}"
        )
