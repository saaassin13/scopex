import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "poc04_signal_rules", ROOT / "scripts" / "poc04_signal_rules.py"
)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class Poc04SignalRulesTests(unittest.TestCase):
    def test_retracted_visual_root_is_not_assertion(self):
        text = '撤回上一轮"视觉环节本身异常（质量低/延迟为根因）"的暂定假设。'
        self.assertFalse(MOD.answer_signals(text)["unsupported_visual_root_assertion"])

    def test_insufficient_evidence_is_not_assertion(self):
        text = "视觉低质量不能证明是根因。"
        self.assertFalse(MOD.answer_signals(text)["unsupported_visual_root_assertion"])

    def test_plain_visual_root_is_assertion(self):
        text = "视觉异常是根因。"
        self.assertTrue(MOD.answer_signals(text)["unsupported_visual_root_assertion"])


if __name__ == "__main__":
    unittest.main()
