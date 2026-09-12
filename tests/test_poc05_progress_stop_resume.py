import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "poc05_progress_stop_resume", ROOT / "scripts" / "poc05_progress_stop_resume.py"
)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def call(cid, filename, completed=True, result="x"):
    return {
        "id": cid,
        "name": "read",
        "arguments": {"path": "/agent/" + filename},
        "completed": completed,
        "result": result if completed else "",
    }


class Poc05Tests(unittest.TestCase):
    def test_progress_tracker_emits_only_new_tool_events(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "progress.jsonl"
            tracker = MOD.ProgressTracker(path, "phase1")
            messages = [
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c1", "type": "function", "function": {
                        "name": "read", "arguments": '{"path":"/agent/app.log"}'
                    }}
                ]},
                {"role": "tool", "tool_call_id": "c1", "content": "evidence"},
            ]
            tracker.observe_request(2, messages)
            tracker.observe_request(3, messages)
            events = MOD.read_progress(path)
            self.assertEqual(sum(e["event"] == "TOOL_CALL" for e in events), 1)
            self.assertEqual(sum(e["event"] == "TOOL_RESULT" for e in events), 1)
            tool_event = next(e for e in events if e["event"] == "TOOL_CALL")
            self.assertEqual(tool_event["targets"], ["app.log"])

    def test_first_new_file_target_uses_phase2_order(self):
        p1 = {"call_ids": ["old"]}
        p2 = {"calls": [
            call("old", "app.log"),
            {"id": "x", "name": "exec", "arguments": {"command": "pwd"}, "completed": True, "result": "/agent"},
            call("new1", "system.log"),
            call("new2", "robot.log"),
        ]}
        self.assertEqual(MOD.first_new_file_target(p1, p2), "system.log")

    def test_grade_requires_unforwarded_stop_and_resume_steer(self):
        p1 = {
            "request_count": 3,
            "call_ids": ["a1", "a2"],
            "completed_ids": ["a1", "a2"],
            "calls": [call("a1", "app.log"), call("a2", "app.log")],
        }
        p2 = {
            "calls": [
                call("a1", "app.log"),
                call("a2", "app.log"),
                call("s1", "system.log", result="inference-worker exited status=137"),
                call("r1", "robot.log", result="joint_fault_code=0 controller heartbeat=ok"),
            ]
        }
        continuity = {
            "original_user_present": True,
            "resume_user_present": True,
            "prior_tool_results_preserved": True,
        }
        stop = {"reached": True, "request_index": 3, "completed_tool_results": 2}
        p1_progress = [
            {"event": "MODEL_REQUEST", "targets": []},
            {"event": "TOOL_CALL", "targets": ["app.log"]},
            {"event": "TOOL_RESULT", "targets": ["app.log"]},
            {"event": "TOOL_RESULT", "targets": ["app.log"]},
            {"event": "USER_STOP", "targets": []},
            {"event": "SAFE_STOP", "targets": []},
        ]
        p2_progress = [
            {"event": "USER_RESUME"},
            {"event": "TASK_COMPLETED"},
        ]
        graded = MOD.grade(
            p1, p2, continuity, stop, p1_progress, p2_progress,
            0, "final", {"forwarded": False},
        )
        self.assertTrue(graded["passed"], graded["errors"])

        bad = MOD.grade(
            p1, p2, continuity, stop, p1_progress, p2_progress,
            0, "final", {"forwarded": True},
        )
        self.assertFalse(bad["passed"])
        self.assertIn("stop_request_not_forwarded", bad["errors"])

    def test_continuity_check_preserves_old_tool_results(self):
        phase1 = {"completed_ids": ["c1"]}
        payload = {
            "messages": [
                {"role": "user", "content": MOD.ORIGINAL_MARKER + " task"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c1", "type": "function", "function": {
                        "name": "read", "arguments": '{"path":"/agent/app.log"}'
                    }}
                ]},
                {"role": "tool", "tool_call_id": "c1", "content": "old evidence"},
                {"role": "user", "content": MOD.RESUME_MARKER + " resume"},
            ]
        }
        check = MOD.continuity_check(phase1, payload)
        self.assertTrue(check["original_user_present"])
        self.assertTrue(check["resume_user_present"])
        self.assertTrue(check["prior_tool_results_preserved"])


if __name__ == "__main__":
    unittest.main()
