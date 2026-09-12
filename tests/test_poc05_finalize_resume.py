import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "poc05_finalize_resume", ROOT / "scripts" / "poc05_finalize_resume.py"
)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class Poc05FinalizeResumeTests(unittest.TestCase):
    def test_mechanics_ok_accepts_only_finalization_failures(self):
        stored = {
            "grade": {
                "checks": {
                    "progress_visible_before_stop": True,
                    "resume_completed": False,
                    "final_visible_answer": False,
                    "task_completed_progress_event": False,
                },
                "errors": [
                    "resume_completed",
                    "final_visible_answer",
                    "task_completed_progress_event",
                ],
            }
        }
        ok, errors = MOD.mechanics_ok(stored)
        self.assertTrue(ok, errors)

    def test_mechanics_ok_rejects_real_resume_failure(self):
        stored = {
            "grade": {
                "checks": {
                    "post_resume_system_tool": False,
                    "resume_completed": False,
                },
                "errors": ["post_resume_system_tool", "resume_completed"],
            }
        }
        ok, errors = MOD.mechanics_ok(stored)
        self.assertFalse(ok)
        self.assertIn("post_resume_system_tool", errors)

    def test_final_signals_accepts_status137_without_oom_claim(self):
        text = (
            "事实：system.log 记录 inference-worker exited status=137；"
            "robot 侧 joint_fault_code=0、heartbeat=ok，当前窗口未见机器人异常。"
            "status=137 的直接触发者未知，不能确认 OOM。"
        )
        signals = MOD.final_signals(text)
        self.assertTrue(signals["system_status_137"])
        self.assertTrue(signals["robot_window_healthy"])
        self.assertFalse(signals["unsupported_oom_assertion"])

    def test_final_signals_rejects_oom_as_fact(self):
        text = (
            "system.log 记录 status=137，robot 正常。"
            "GPU OOM 导致 inference-worker 被杀，是本次失败根因。"
        )
        signals = MOD.final_signals(text)
        self.assertTrue(signals["unsupported_oom_assertion"])


if __name__ == "__main__":
    unittest.main()
