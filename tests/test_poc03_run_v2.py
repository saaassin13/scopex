import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "poc03_run_v2", ROOT / "scripts" / "poc03_run_v2.py"
)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class DeferredGateTests(unittest.TestCase):
    def test_first_call_is_deferred_second_checks_once(self):
        calls = []

        def gate():
            calls.append("checked")

        wrapped = MOD.defer_first_gate(gate)
        wrapped()
        self.assertEqual(calls, [])
        wrapped()
        self.assertEqual(calls, ["checked"])
        wrapped()
        wrapped()
        self.assertEqual(calls, ["checked"])
        self.assertEqual(wrapped._scopex_gate_state["calls"], 4)

    def test_failure_is_cached_and_not_reexecuted(self):
        calls = []
        error = RuntimeError("sandbox missing")

        def gate():
            calls.append("checked")
            raise error

        wrapped = MOD.defer_first_gate(gate)
        wrapped()
        with self.assertRaisesRegex(RuntimeError, "sandbox missing"):
            wrapped()
        with self.assertRaisesRegex(RuntimeError, "sandbox missing"):
            wrapped()
        self.assertEqual(calls, ["checked"])
        self.assertIs(wrapped._scopex_gate_state["error"], error)


if __name__ == "__main__":
    unittest.main()
