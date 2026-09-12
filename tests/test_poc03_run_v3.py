import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "poc03_run_v3", ROOT / "scripts" / "poc03_run_v3.py"
)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def write_request(out, index, tool_name, with_result=True):
    messages = [{
        "role": "assistant",
        "tool_calls": [{
            "id": "call1",
            "function": {"name": tool_name, "arguments": "{}"},
        }],
    }]
    if with_result:
        messages.append({"role": "tool", "tool_call_id": "call1", "content": "ok"})
    (out / f"wire-{index:02d}-request.json").write_text(
        json.dumps({"messages": messages}), encoding="utf-8"
    )


class ToolAwareGateTests(unittest.TestCase):
    def test_read_result_does_not_require_docker_container(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            write_request(out, 1, "read")
            self.assertFalse(MOD.latest_request_requires_container(out))
            calls = []
            wrapped = MOD.gate_for_container_tools(lambda: calls.append("gate"), out)
            wrapped()
            self.assertEqual(calls, [])
            self.assertFalse(wrapped._scopex_gate_state["checked"])

    def test_exec_result_requires_and_checks_once(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            write_request(out, 1, "exec")
            calls = []
            wrapped = MOD.gate_for_container_tools(lambda: calls.append("gate"), out)
            wrapped()
            wrapped()
            self.assertEqual(calls, ["gate"])
            self.assertTrue(wrapped._scopex_gate_state["checked"])
            self.assertTrue(wrapped._scopex_gate_state["container_required_seen"])

    def test_unreturned_exec_does_not_gate_yet(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            write_request(out, 1, "exec", with_result=False)
            self.assertFalse(MOD.latest_request_requires_container(out))

    def test_read_only_good_result_can_be_promoted(self):
        result = {
            "returncode": 0,
            "stop": None,
            "errors": [],
            "wire_ok": True,
            "grade": {"passed": True},
            "trace": {"tool_calls": [
                {"name": "read", "has_return": True},
                {"name": "read", "has_return": True},
            ]},
        }
        self.assertTrue(MOD.can_promote_read_only(result))

    def test_exec_result_cannot_bypass_original_gate(self):
        result = {
            "returncode": 0,
            "stop": None,
            "errors": [],
            "wire_ok": True,
            "grade": {"passed": True},
            "trace": {"tool_calls": [{"name": "exec", "has_return": True}]},
        }
        self.assertFalse(MOD.can_promote_read_only(result))
        self.assertTrue(MOD.trace_requires_container(result["trace"]))


if __name__ == "__main__":
    unittest.main()
