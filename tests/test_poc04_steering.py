import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "poc04_steering", ROOT / "scripts" / "poc04_steering.py"
)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def assistant_call(cid, name, args):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": cid,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        }],
    }


def tool_result(cid, text):
    return {"role": "tool", "tool_call_id": cid, "content": text}


class Poc04SteeringTests(unittest.TestCase):
    def test_completed_calls_pairs_tool_results(self):
        messages = [
            assistant_call("a", "read", {"path": "/agent/app.log"}),
            tool_result("a", "app evidence"),
            assistant_call("b", "exec", {"command": "grep ERROR /agent/app.log"}),
            tool_result("b", "more app evidence"),
            assistant_call("c", "read", {"path": "/agent/robot.log"}),
        ]
        rows = MOD.completed_calls(messages)
        self.assertEqual([row["id"] for row in rows], ["a", "b"])
        self.assertTrue(MOD.call_touches(rows[0], "app.log"))
        self.assertFalse(MOD.call_touches(rows[0], "robot.log"))

    def test_history_check_proves_same_session_evidence(self):
        phase1 = {"completed_ids": ["a", "b"]}
        phase2_first = {
            "messages": [
                {"role": "user", "content": MOD.ORIGINAL_TASK},
                assistant_call("a", "read", {"path": "/agent/app.log"}),
                tool_result("a", "app evidence"),
                {"role": "user", "content": MOD.STEERING_TASK},
            ]
        }
        check = MOD.history_check(phase1, phase2_first)
        self.assertTrue(check["original_user_present"])
        self.assertTrue(check["steering_user_present"])
        self.assertTrue(check["preserved_tool_results"])
        self.assertEqual(check["preserved_tool_result_ids"], ["a"])

    def test_grade_passes_real_direction_change(self):
        phase1 = {
            "call_ids": ["a", "b"],
            "completed_ids": ["a", "b"],
            "calls": [
                {"id": "a", "name": "read", "arguments": {"path": "/agent/app.log"},
                 "completed": True, "result": "target pose unavailable"},
                {"id": "b", "name": "exec", "arguments": {"command": "grep ERROR /agent/app.log"},
                 "completed": True, "result": "task execution failed"},
            ],
        }
        phase2 = {
            "calls": phase1["calls"] + [
                {"id": "c", "name": "read", "arguments": {"path": "/agent/robot.log"},
                 "completed": True, "result": "joint_fault_code=0 emergency_stop=false"},
                {"id": "d", "name": "read", "arguments": {"path": "/agent/system.log"},
                 "completed": True, "result": "inference-worker exited status=137"},
            ]
        }
        continuity = {
            "original_user_present": True,
            "steering_user_present": True,
            "preserved_tool_results": True,
        }
        boundary = {"reached": True, "completed_tool_results": 2}
        result = MOD.grade(
            phase1, phase2, continuity, boundary, 0, "综合结论"
        )
        self.assertTrue(result["passed"], result["errors"])

    def test_grade_rejects_new_app_tool_after_steer(self):
        phase1 = {
            "call_ids": ["a", "b"],
            "completed_ids": ["a", "b"],
            "calls": [
                {"id": "a", "name": "read", "arguments": {"path": "/agent/app.log"},
                 "completed": True, "result": "x"},
                {"id": "b", "name": "exec", "arguments": {"command": "grep ERROR /agent/app.log"},
                 "completed": True, "result": "y"},
            ],
        }
        phase2 = {
            "calls": phase1["calls"] + [
                {"id": "c", "name": "read", "arguments": {"path": "/agent/app.log"},
                 "completed": True, "result": "old direction"},
                {"id": "d", "name": "read", "arguments": {"path": "/agent/robot.log"},
                 "completed": True, "result": "controller heartbeat=ok"},
                {"id": "e", "name": "read", "arguments": {"path": "/agent/system.log"},
                 "completed": True, "result": "inference-worker health=ready"},
            ]
        }
        continuity = {
            "original_user_present": True,
            "steering_user_present": True,
            "preserved_tool_results": True,
        }
        boundary = {"reached": True, "completed_tool_results": 2}
        result = MOD.grade(phase1, phase2, continuity, boundary, 0, "done")
        self.assertFalse(result["passed"])
        self.assertIn("post_steer_no_new_app_tool", result["errors"])


if __name__ == "__main__":
    unittest.main()
