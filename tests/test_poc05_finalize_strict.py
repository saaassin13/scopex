import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "poc05_finalize_strict", ROOT / "scripts" / "poc05_finalize_strict.py"
)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class Poc05StrictFinalizerTests(unittest.TestCase):
    def test_accepts_chinese_status137_with_unknown_cause(self):
        text = (
            "事实：inference-worker 以状态 137 退出；"
            "机器人在当前日志窗口未见异常，joint_fault_code=0。"
            "137 的具体触发机制仍未知，现有证据不能确认 OOM。"
        )
        signals = MOD.final_signals(text)
        self.assertTrue(signals["system_status_137"])
        self.assertTrue(signals["robot_window_healthy"])
        self.assertTrue(signals["status137_cause_unknown"])
        self.assertFalse(signals["unsupported_oom_assertion"])
        self.assertFalse(signals["unsupported_unproven_cause_ranking"])
        self.assertFalse(signals["unsupported_robot_exclusion"])

    def test_rejects_unproven_resource_ranking(self):
        text = (
            "inference-worker 状态 137 退出。"
            "更可能是资源竞争或内部错误导致进程被强制终止。"
            "机器人当前窗口未见异常。137 触发机制未知。"
        )
        signals = MOD.final_signals(text)
        self.assertTrue(signals["unsupported_unproven_cause_ranking"])

    def test_rejects_oom_as_cause(self):
        text = (
            "inference-worker status=137。GPU OOM 导致进程退出，是根因。"
            "机器人当前窗口未见异常。"
        )
        signals = MOD.final_signals(text)
        self.assertTrue(signals["unsupported_oom_assertion"])
        self.assertTrue(signals["unsupported_unproven_cause_ranking"])

    def test_rejects_robot_global_exclusion(self):
        text = (
            "inference-worker 状态 137 退出，具体触发机制未知。"
            "机器人完全健康，可以排除机器人原因。"
        )
        signals = MOD.final_signals(text)
        self.assertTrue(signals["unsupported_robot_exclusion"])


if __name__ == "__main__":
    unittest.main()
