import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "poc03_finalize", ROOT / "scripts" / "poc03_finalize.py"
)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

SOURCE = "\n".join([
    "2026-09-11 07:59:21:638 [mainwindow.cpp:4106] [ERROR] MarkDetectedTargetBoundboxOnImg exception, LeftKneeBoundbox is invalid, Left[0.00], Top[369.00], Right[95.00], Bottom[485.00]",
    "2026-09-11 07:59:21:638 [mainwindow.cpp:4124] [ERROR] MarkDetectedTargetBoundboxOnImg exception, LeftLegBoundbox is invalid, Left[0.00], Top[363.00], Right[114.00], Bottom[1215.00]",
    "2026-09-11 07:59:21:639 [mainwindow.cpp:4916] [ERROR] Cal StartFollowPt failed, left knee and left leg is not detected",
    "2026-09-11 07:59:21:639 [mainwindow.cpp:4479] [ERROR] CalLeftCamStartFollowPt failed",
    "2026-09-11 07:59:21:847 [mainwindow.cpp:4469] [INFO] CalLeftCamStartFollowPt succeeded, leftKnee2D[40.00, 428.00], rightKnee2D[366.00, 440.00]",
])


class Poc03FinalizeTests(unittest.TestCase):
    def test_observed_source_lines_handle_grep_prefix(self):
        results = {
            "a": "4:" + SOURCE.splitlines()[3] + "\n",
            "b": SOURCE.splitlines()[4] + "\n",
        }
        self.assertEqual(MOD.observed_line_indices(SOURCE, results), [3, 4])

    def test_catalog_keeps_trigger_failure_and_recovery(self):
        results = {"a": SOURCE}
        catalog = MOD.build_catalog(
            SOURCE, results, "CalLeftCamStartFollowPt failed", max_evidence=5
        )
        raw = [row["raw_line"] for row in catalog]
        self.assertTrue(any("Cal StartFollowPt failed" in line for line in raw))
        self.assertTrue(any("CalLeftCamStartFollowPt failed" in line for line in raw))
        self.assertTrue(any("CalLeftCamStartFollowPt succeeded" in line for line in raw))
        self.assertEqual([row["ref"] for row in catalog], [f"E{i}" for i in range(1, 6)])

    def test_catalog_reserves_recovery_when_errors_crowd_top_n(self):
        rows = [
            f"2026-09-11 07:59:21:{500+i:03d} [x.cpp:{1000+i}] [ERROR] noisy failure invalid exception {i}"
            for i in range(20)
        ]
        rows.append(
            "2026-09-11 07:59:21:700 [mainwindow.cpp:4479] [ERROR] CalLeftCamStartFollowPt failed"
        )
        rows.append(
            "2026-09-11 07:59:21:847 [mainwindow.cpp:4469] [INFO] CalLeftCamStartFollowPt succeeded"
        )
        source = "\n".join(rows)
        catalog = MOD.build_catalog(
            source, {"all": source}, "CalLeftCamStartFollowPt failed", max_evidence=12
        )
        recovery = [
            row for row in catalog if "CalLeftCamStartFollowPt succeeded" in row["raw_line"]
        ]
        self.assertEqual(len(catalog), 12)
        self.assertEqual(len(recovery), 1)
        self.assertIn("post_focus_recovery", recovery[0]["selection_reason"])

    def test_validate_rejects_unknown_evidence_ref(self):
        catalog = [
            {"ref": "E1", "source_line": 1, "raw_line": SOURCE.splitlines()[0]},
        ]
        obj = {
            "direct_trigger": "x",
            "persistence": "transient",
            "recovery": {"observed": True, "evidence_ref": "E9"},
            "evidence": [{"role": "trigger", "ref": "E9"}],
            "facts": [],
            "inferences": [],
            "unknowns": ["x"],
            "conclusion": "x",
            "confidence": "high",
        }
        errors = MOD.validate_compact(obj, catalog)
        self.assertIn("recovery.evidence_ref", errors)
        self.assertIn("evidence.item", errors)

    def test_validator_does_not_grade_semantic_role_coverage(self):
        lines = SOURCE.splitlines()
        catalog = [
            {"ref": "E1", "source_line": 3, "raw_line": lines[2]},
            {"ref": "E2", "source_line": 4, "raw_line": lines[3]},
            {"ref": "E3", "source_line": 5, "raw_line": lines[4]},
        ]
        obj = {
            "direct_trigger": "x",
            "persistence": "transient",
            "recovery": {"observed": True, "evidence_ref": "E3"},
            "evidence": [
                {"role": "failure", "ref": "E1"},
                {"role": "failure", "ref": "E2"},
            ],
            "facts": ["x"],
            "inferences": ["x"],
            "unknowns": ["x"],
            "conclusion": "x",
            "confidence": "high",
        }
        self.assertEqual(MOD.validate_compact(obj, catalog), [])

    def test_expand_restores_exact_raw_lines(self):
        lines = SOURCE.splitlines()
        catalog = [
            {"ref": "E1", "source_line": 3, "raw_line": lines[2]},
            {"ref": "E2", "source_line": 4, "raw_line": lines[3]},
            {"ref": "E3", "source_line": 5, "raw_line": lines[4]},
        ]
        compact = {
            "direct_trigger": "左膝和左腿未检测到",
            "persistence": "transient",
            "recovery": {"observed": True, "evidence_ref": "E3"},
            "evidence": [
                {"role": "trigger", "ref": "E1"},
                {"role": "failure", "ref": "E2"},
                {"role": "recovery", "ref": "E3"},
            ],
            "facts": ["失败后恢复"],
            "inferences": ["当前窗口为瞬时异常"],
            "unknowns": ["深层原因未知"],
            "conclusion": "瞬时检测失败后恢复",
            "confidence": "high",
        }
        self.assertEqual(MOD.validate_compact(compact, catalog), [])
        expanded = MOD.expand_compact(compact, catalog)
        self.assertEqual(expanded["recovery"]["raw_line"], lines[4])
        self.assertEqual(expanded["evidence"][1]["raw_line"], lines[3])

    def test_parse_compact_accepts_json_fence(self):
        value = {"a": 1}
        text = "```json\n" + json.dumps(value) + "\n```"
        self.assertEqual(MOD.parse_compact(text), value)


if __name__ == "__main__":
    unittest.main()
