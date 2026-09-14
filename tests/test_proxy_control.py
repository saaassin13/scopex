import unittest

from scopex.agent.model_proxy import RequestRejected, StopBeforeForward
from scopex.agent.proxy_control import RuntimeRequestHook
from scopex.agent.request_policy import OpenClawRequestPolicy
from scopex.events.observer import AgentProgressObserver
from scopex.events.progress import EventType, InMemoryEventSink
from scopex.runtime.stop import SafeStopGate


def payload():
    return {
        "model": "qwen-local",
        "messages": [],
        "max_tokens": 2048,
        "chat_template_kwargs": {"enable_thinking": False},
        "tools": [
            {"type": "function", "function": {"name": name, "parameters": {}}}
            for name in ("read", "exec", "process")
        ],
        "tool_choice": "auto",
    }


class ProxyControlTests(unittest.TestCase):
    def test_request_policy_accepts_validated_wire(self):
        OpenClawRequestPolicy("qwen-local", 2048).validate(payload())

    def test_request_policy_rejects_thinking_or_capability_expansion(self):
        bad = payload()
        bad["chat_template_kwargs"] = {"enable_thinking": True}
        with self.assertRaises(RequestRejected):
            OpenClawRequestPolicy("qwen-local", 2048).validate(bad)

        # OpenClaw utility requests (for example compaction) may reduce or omit
        # the normal tool surface. Capability reduction is allowed.
        reduced = payload()
        reduced["tools"] = reduced["tools"][:-1]
        OpenClawRequestPolicy("qwen-local", 2048).validate(reduced)

        omitted = payload()
        omitted.pop("tools")
        OpenClawRequestPolicy("qwen-local", 2048).validate(omitted)

        # Capability expansion outside the configured allowlist is still a hard
        # policy violation.
        bad = payload()
        bad["tools"].append(
            {"type": "function", "function": {"name": "write", "parameters": {}}}
        )
        with self.assertRaises(RequestRejected):
            OpenClawRequestPolicy("qwen-local", 2048).validate(bad)

    def test_runtime_hook_emits_progress_and_stops_before_forward(self):
        sink = InMemoryEventSink()
        observer = AgentProgressObserver("t1", sink)
        stop = SafeStopGate()
        reached = []
        hook = RuntimeRequestHook(
            observer,
            stop,
            on_safe_stop=reached.append,
            request_validator=OpenClawRequestPolicy("qwen-local", 2048).validate,
        )

        hook(1, payload())
        self.assertEqual([e.type for e in sink.events], [EventType.MODEL_REQUEST])

        stop.request("user stop")
        with self.assertRaises(StopBeforeForward):
            hook(2, payload())
        self.assertEqual(len(reached), 1)
        self.assertEqual(reached[0].request_index, 2)
        self.assertEqual([e.type for e in sink.events].count(EventType.MODEL_REQUEST), 2)


if __name__ == "__main__":
    unittest.main()
