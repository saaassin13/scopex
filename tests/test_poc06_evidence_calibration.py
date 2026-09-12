import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "poc06_evidence_calibration", ROOT / "scripts" / "poc06_evidence_calibration.py"
)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def catalog():
    return [
        {"ref": "E1", "source": "app.log", "raw_line":
         "2026-09-12 10:15:00.722 [ERROR] task execution failed reason=target_pose_unavailable cycle=781"},
        {"ref": "E2", "source": "system.log", "raw_line":
         "2026-09-12 10:15:00.180 [ERROR] inference-worker exited status=137"},
        {"ref": "E3", "source": "robot.log", "raw_line":
         "2026-09-12 10:15:00.800 [INFO] joint_fault_code=0 emergency_stop=false"},
        {"ref": "E4", "source": "robot.log", "raw_line":
         "2026-09-12 10:15:01.500 [INFO] controller heartbeat=ok"},
    ]


def good_obj():
    return {
        "claims": [
            {
                "id": "C1", "kind": "fact", "topic": "inference-worker 退出",
                "evidence_refs": ["E2"], "confidence": "high", "scope": "event",
                "relation": "observed",
            },
            {
                "id": "C2", "kind": "fact", "topic": "应用任务失败",
                "evidence_refs": ["E1"], "confidence": "high", "scope": "event",
                "relation": "observed",
            },
            {
                "id": "C3", "kind": "inference", "topic": "退出事件与应用失败的时间关系",
                "evidence_refs": ["E2", "E1"], "confidence": "medium", "scope": "time_window",
                "relation": "temporal_association",
            },
            {
                "id": "C4", "kind": "fact", "topic": "机器人当前窗口状态",
                "evidence_refs": ["E3", "E4"], "confidence": "high", "scope": "time_window",
                "relation": "observed",
            },
            {
                "id": "C5", "kind": "unknown", "topic": "status=137 的具体触发机制",
                "evidence_refs": ["E2"], "confidence": "unknown", "scope": "event",
                "relation": "unknown",
            },
        ],
        "summary_claim_ids": ["C1", "C2", "C3", "C4", "C5"],
    }


class Poc06EvidenceCalibrationTests(unittest.TestCase):
    def test_extract_log_lines_removes_display_prefix(self):
        text = (
            "1:2026-09-12 10:15:00.180 [ERROR] inference-worker exited status=137\n"
            "noise\n"
            "2:2026-09-12 10:15:00.930 [INFO] inference-worker started pid=4412\n"
        )
        self.assertEqual(MOD.extract_log_lines(text), [
            "2026-09-12 10:15:00.180 [ERROR] inference-worker exited status=137",
            "2026-09-12 10:15:00.930 [INFO] inference-worker started pid=4412",
        ])

    def test_generic_validator_accepts_calibrated_structure(self):
        self.assertEqual(MOD.validate_claims(good_obj(), catalog()), [])

    def test_generic_validator_accepts_high_confidence_temporal_association(self):
        obj = good_obj()
        obj["claims"][2]["confidence"] = "high"
        self.assertEqual(MOD.validate_claims(obj, catalog()), [])

    def test_generic_validator_rejects_fact_without_evidence(self):
        obj = good_obj()
        obj["claims"][0]["evidence_refs"] = []
        errors = MOD.validate_claims(obj, catalog())
        self.assertIn("claims[0].fact_requires_evidence", errors)

    def test_generic_validator_rejects_temporal_inference_with_one_ref(self):
        obj = good_obj()
        obj["claims"][2]["evidence_refs"] = ["E2"]
        errors = MOD.validate_claims(obj, catalog())
        self.assertIn("claims[2].temporal_requires_two_refs", errors)

    def test_generic_validator_rejects_unknown_with_high_confidence(self):
        obj = good_obj()
        obj["claims"][4]["confidence"] = "high"
        errors = MOD.validate_claims(obj, catalog())
        self.assertIn("claims[4].unknown_confidence", errors)

    def test_validator_does_not_crash_on_non_string_evidence_ref(self):
        obj = good_obj()
        obj["claims"][0]["evidence_refs"] = [{"bad": "shape"}]
        errors = MOD.validate_claims(obj, catalog())
        self.assertIn("claims[0].evidence_refs", errors)
        self.assertIn("claims[0].fact_requires_evidence", errors)

    def test_validator_does_not_crash_on_non_string_summary_id(self):
        obj = good_obj()
        obj["summary_claim_ids"] = [{"bad": "shape"}]
        errors = MOD.validate_claims(obj, catalog())
        self.assertIn("summary_claim_ids", errors)

    def test_poc06_grader_accepts_fact_temporal_unknown_split(self):
        grade = MOD.grade_poc06(good_obj(), catalog())
        self.assertTrue(grade["passed"], grade["errors"])

    def test_poc06_grader_rejects_global_robot_claim(self):
        obj = good_obj()
        obj["claims"][3]["scope"] = "global"
        grade = MOD.grade_poc06(obj, catalog())
        self.assertFalse(grade["passed"])
        self.assertIn("robot_claim_limited_to_time_window", grade["errors"])
        self.assertIn("no_global_claims", grade["errors"])

    def test_poc06_grader_rejects_causal_hypothesis_for_fixture(self):
        obj = good_obj()
        obj["claims"].append({
            "id": "C6", "kind": "inference", "topic": "OOM 导致进程退出",
            "evidence_refs": ["E2"], "confidence": "low", "scope": "event",
            "relation": "causal_hypothesis",
        })
        obj["summary_claim_ids"].append("C6")
        self.assertEqual(MOD.validate_claims(obj, catalog()), [])
        grade = MOD.grade_poc06(obj, catalog())
        self.assertFalse(grade["passed"])
        self.assertIn("no_causal_hypothesis_for_this_fixture", grade["errors"])

    def test_renderer_uses_structural_epistemic_labels_and_exact_evidence(self):
        text = MOD.render_claims(good_obj(), catalog())
        self.assertIn("事实｜单事件", text)
        self.assertIn("推断｜时间关联｜medium", text)
        self.assertIn("该结构不表示已证明因果", text)
        self.assertIn("未知｜单事件：status=137 的具体触发机制", text)
        self.assertIn("E2 [system.log] 2026-09-12 10:15:00.180", text)

    def test_fact_topic_cannot_smuggle_causal_wording_into_rendered_fact(self):
        obj = good_obj()
        obj["claims"][0]["topic"] = "GPU OOM 导致 inference-worker 退出"
        self.assertEqual(MOD.validate_claims(obj, catalog()), [])
        text = MOD.render_claims(obj, catalog())
        self.assertNotIn("GPU OOM 导致", text)
        self.assertIn("inference-worker exited status=137", text)


if __name__ == "__main__":
    unittest.main()
