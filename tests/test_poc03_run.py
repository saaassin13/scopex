import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("poc03_run", ROOT / "scripts" / "poc03_run.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

SOURCE = "\n".join([
    "2026-09-11 07:59:21:638 [mainwindow.cpp:4106] [ERROR] MarkDetectedTargetBoundboxOnImg exception, LeftKneeBoundbox is invalid, Left[0.00], Top[369.00], Right[95.00], Bottom[485.00] ",
    "2026-09-11 07:59:21:638 [mainwindow.cpp:4124] [ERROR] MarkDetectedTargetBoundboxOnImg exception, LeftLegBoundbox is invalid, Left[0.00], Top[363.00], Right[114.00], Bottom[1215.00]",
    "2026-09-11 07:59:21:639 [mainwindow.cpp:4916] [ERROR] Cal StartFollowPt failed, left knee and left leg is not detected",
    "2026-09-11 07:59:21:639 [mainwindow.cpp:4479] [ERROR] CalLeftCamStartFollowPt failed",
    "2026-09-11 07:59:21:847 [mainwindow.cpp:4469] [INFO] CalLeftCamStartFollowPt succeeded, leftKnee2D[40.00, 428.00], rightKnee2D[366.00, 440.00]",
])


def passing_answer():
    lines = SOURCE.splitlines()
    return json.dumps({
        "direct_trigger": "当帧左膝和左腿未被有效检测，StartFollowPt 无法计算。",
        "persistence": "transient",
        "recovery": {"observed": True, "raw_line": lines[4]},
        "evidence": [
            {"role": "upstream", "raw_line": lines[0]},
            {"role": "upstream", "raw_line": lines[1]},
            {"role": "trigger", "raw_line": lines[2]},
            {"role": "failure", "raw_line": lines[3]},
            {"role": "recovery", "raw_line": lines[4]},
        ],
        "facts": ["日志记录了失败，之后记录了成功。"],
        "inferences": ["在该日志窗口内表现为单次检测异常后恢复。"],
        "unknowns": ["日志不足以确定最初检测无效的更深层原因。"],
        "conclusion": "当前窗口支持瞬时检测失败，不支持持续硬件故障结论。",
        "confidence": "high",
    }, ensure_ascii=False)


class Poc03Tests(unittest.TestCase):
    def test_task_does_not_leak_fixture_answer(self):
        self.assertNotIn("07:59:21:847", MOD.TASK)
        self.assertNotIn("left knee and left leg is not detected", MOD.TASK)
        self.assertNotIn("CalLeftCamStartFollowPt succeeded", MOD.TASK)

    def test_grade_passes_grounded_transient_diagnosis(self):
        trace = {"skill_read": True, "knowledge_read": True,
                 "recovery_seen_in_tool_result": True}
        grade = MOD.grade_answer(passing_answer(), SOURCE, trace)
        self.assertTrue(grade["passed"], grade)

    def test_grade_fails_without_recovery_in_tool_evidence(self):
        trace = {"skill_read": True, "knowledge_read": True,
                 "recovery_seen_in_tool_result": False}
        grade = MOD.grade_answer(passing_answer(), SOURCE, trace)
        self.assertFalse(grade["passed"])
        self.assertFalse(grade["checks"]["recovery_actually_seen_by_agent"])

    def test_grade_rejects_unsupported_root_cause(self):
        obj = json.loads(passing_answer())
        obj["conclusion"] = "因为网络故障导致左腿检测失败。"
        trace = {"skill_read": True, "knowledge_read": True,
                 "recovery_seen_in_tool_result": True}
        grade = MOD.grade_answer(json.dumps(obj, ensure_ascii=False), SOURCE, trace)
        self.assertFalse(grade["passed"])
        self.assertFalse(grade["checks"]["no_unsupported_root_cause"])

    def test_collect_trace_proves_skill_knowledge_and_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            payload = {
                "messages": [
                    {"role": "assistant", "tool_calls": [
                        {"id": "a", "function": {"name": "read", "arguments": json.dumps({"path": "/agent/skills/cow-disinfect-diagnosis/SKILL.md"})}},
                        {"id": "b", "function": {"name": "read", "arguments": json.dumps({"path": "/agent/skills/cow-disinfect-diagnosis/references/cow-disinfect-log.md"})}},
                        {"id": "c", "function": {"name": "exec", "arguments": json.dumps({"command": "example"})}},
                    ]},
                    {"role": "tool", "tool_call_id": "a", "content": MOD.SKILL_MARKER},
                    {"role": "tool", "tool_call_id": "b", "content": MOD.KNOWLEDGE_MARKER},
                    {"role": "tool", "tool_call_id": "c", "content": SOURCE.splitlines()[-1]},
                ]
            }
            (out / "wire-01-request.json").write_text(json.dumps(payload), encoding="utf-8")
            trace = MOD.collect_trace(out)
            self.assertTrue(trace["skill_read"])
            self.assertTrue(trace["knowledge_read"])
            self.assertTrue(trace["recovery_seen_in_tool_result"])


if __name__ == "__main__":
    unittest.main()
