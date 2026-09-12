import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "poc04_correction", ROOT / "scripts" / "poc04_correction.py"
)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class Poc04CorrectionTests(unittest.TestCase):
    def test_answer_signals_accept_revision_with_system_evidence(self):
        text = (
            "修正上一轮暂定假设：robot 侧 joint_fault_code=0，controller heartbeat=ok，"
            "未见机器人故障。system.log 显示 inference-worker exited status=137，随后重启。"
            "因此视觉低质量不能证明是根因，当前更支持推理服务中断与任务失败相关。"
        )
        signals = MOD.answer_signals(text)
        self.assertTrue(signals["robot_ok"])
        self.assertTrue(signals["system_crash"])
        self.assertTrue(signals["correction_acknowledged"])
        self.assertFalse(signals["unsupported_visual_root_assertion"])

    def test_answer_signals_reject_visual_as_proven_root(self):
        text = "视觉异常是根因。robot 状态正常，inference-worker exited status=137。"
        signals = MOD.answer_signals(text)
        self.assertTrue(signals["unsupported_visual_root_assertion"])

    def test_continuity_requires_prior_answer_and_tool_result(self):
        phase1 = {"completed_ids": ["c1"]}
        answer = "这是上一轮暂定假设"
        phase2 = {
            "messages": [
                {"role": "user", "content": MOD.ORIGINAL_MARKER + " original"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c1", "type": "function", "function": {
                        "name": "read", "arguments": '{"path":"/agent/app.log"}'
                    }}
                ]},
                {"role": "tool", "tool_call_id": "c1", "content": "app evidence"},
                {"role": "assistant", "content": answer},
                {"role": "user", "content": MOD.CORRECTION_MARKER + " correction"},
            ]
        }
        check = MOD.continuity_check(phase1, answer, phase2)
        self.assertTrue(check["original_user_present"])
        self.assertTrue(check["correction_user_present"])
        self.assertTrue(check["prior_tool_results_preserved"])
        self.assertTrue(check["prior_assistant_answer_preserved"])


if __name__ == "__main__":
    unittest.main()
