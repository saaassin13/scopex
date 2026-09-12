import unittest

from scopex.agent.trace import parse_messages, tool_target
from scopex.events.observer import AgentProgressObserver
from scopex.events.progress import EventType, InMemoryEventSink
from scopex.runtime.stop import SafeStopGate


class AgentRuntimeBridgeTests(unittest.TestCase):
    def sample_messages(self):
        return [
            {"role": "user", "content": "inspect"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "read",
                            "arguments": '{"path":"/agent/system.log"}',
                        },
                    },
                    {
                        "id": "c2",
                        "type": "function",
                        "function": {
                            "name": "exec",
                            "arguments": '{"command":"python3 /tmp/check.py"}',
                        },
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "system evidence"},
        ]

    def test_trace_parser_links_calls_and_results(self):
        trace = parse_messages(self.sample_messages())
        self.assertEqual(set(trace.call_map), {"c1", "c2"})
        self.assertEqual(set(trace.result_map), {"c1"})
        self.assertEqual(trace.completed_call_ids, frozenset({"c1"}))
        self.assertEqual(tool_target(trace.call_map["c1"]), "/agent/system.log")
        # Shell command bodies are intentionally not surfaced as UI targets.
        self.assertIsNone(tool_target(trace.call_map["c2"]))

    def test_progress_observer_emits_only_new_tool_events(self):
        sink = InMemoryEventSink()
        observer = AgentProgressObserver("t1", sink)
        messages = self.sample_messages()
        observer.observe_request(1, messages)
        observer.observe_request(2, messages)
        types = [event.type for event in sink.events]
        self.assertEqual(types.count(EventType.MODEL_REQUEST), 2)
        self.assertEqual(types.count(EventType.TOOL_CALL), 2)
        self.assertEqual(types.count(EventType.TOOL_RESULT), 1)
        read_call = next(
            event for event in sink.events
            if event.type is EventType.TOOL_CALL and event.data.get("tool") == "read"
        )
        self.assertEqual(read_call.data["target"], "/agent/system.log")

    def test_progress_seed_prevents_reemitting_old_session_history(self):
        sink = InMemoryEventSink()
        observer = AgentProgressObserver("t1", sink)
        observer.seed(call_ids=["c1"], result_ids=["c1"])
        observer.observe_request(1, self.sample_messages())
        calls = [e for e in sink.events if e.type is EventType.TOOL_CALL]
        results = [e for e in sink.events if e.type is EventType.TOOL_RESULT]
        self.assertEqual([e.data["tool_call_id"] for e in calls], ["c2"])
        self.assertEqual(results, [])

    def test_safe_stop_gate_blocks_next_model_boundary(self):
        gate = SafeStopGate()
        self.assertIsNone(gate.before_model_request(1))
        gate.request("user_clicked_stop")
        boundary = gate.before_model_request(2)
        self.assertIsNotNone(boundary)
        self.assertEqual(boundary.request_index, 2)
        self.assertEqual(boundary.reason, "user_clicked_stop")
        self.assertIs(gate.before_model_request(3), boundary)


if __name__ == "__main__":
    unittest.main()
