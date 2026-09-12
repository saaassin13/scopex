import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "poc05_control_regrade", ROOT / "scripts" / "poc05_control_regrade.py"
)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class Poc05ControlRegradeTests(unittest.TestCase):
    def test_finalizer_mechanics_accepts_completed_no_tool_fresh_context(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "request.json").write_text(json.dumps({
                "messages": [
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "u"},
                ]
            }), encoding="utf-8")
            path = d / "result.json"
            obj = {
                "finalizer": {"done_seen": True, "finish_reasons": ["stop"]},
                "final_answer": "answer",
            }
            checks = MOD.finalizer_mechanics(obj, path)
            self.assertTrue(all(checks.values()), checks)

    def test_finalizer_mechanics_rejects_tools(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "request.json").write_text(json.dumps({
                "messages": [{"role": "user", "content": "u"}],
                "tools": [{"type": "function"}],
            }), encoding="utf-8")
            obj = {
                "finalizer": {"done_seen": True, "finish_reasons": ["stop"]},
                "final_answer": "answer",
            }
            checks = MOD.finalizer_mechanics(obj, d / "result.json")
            self.assertFalse(checks["fresh_context_has_no_tools"])

    def test_completed_finalizer_can_have_quality_failure(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td)
            d = run / "finalize-strict-1"
            d.mkdir()
            (d / "request.json").write_text(json.dumps({
                "messages": [
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "u"},
                ]
            }), encoding="utf-8")
            (d / "result.json").write_text(json.dumps({
                "finalizer": {"done_seen": True, "finish_reasons": ["stop"]},
                "final_answer": "answer",
                "passed": False,
            }), encoding="utf-8")
            chosen = MOD.choose_completed_finalizer(run)
            self.assertIsNotNone(chosen)
            _path, obj, checks = chosen
            self.assertFalse(obj["passed"])
            self.assertTrue(all(checks.values()))


if __name__ == "__main__":
    unittest.main()
