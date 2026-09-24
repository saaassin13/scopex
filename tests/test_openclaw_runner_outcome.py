import os
from pathlib import Path
import tempfile
import textwrap
import unittest

from scopex.agent.openclaw import OpenClawCommandBuilder
from scopex.agent.openclaw_runner import OpenClawTurnRunner
from scopex.agent.outcome import parse_cli_outcome


class OpenClawRunnerOutcomeTests(unittest.TestCase):
    def make_cli(self, root: Path, body: str) -> Path:
        path = root / "fake-openclaw"
        path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_runner_captures_stdout_and_outcome(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cli = self.make_cli(
                root,
                textwrap.dedent(
                    """
                    import json
                    print(json.dumps({
                        "meta": {"executionTrace": {"fallbackUsed": False}},
                        "payloads": [{"text": "final answer", "isReasoning": False}]
                    }))
                    """
                ),
            )
            runner = OpenClawTurnRunner(
                OpenClawCommandBuilder(cli),
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                cwd=root,
            )
            result = runner.run(
                session_key="agent:sx:test",
                message="diagnose",
                timeout_s=5,
                audit_dir=root / "audit",
            )
            self.assertEqual(result.returncode, 0)
            self.assertIsNone(result.stop_reason)
            outcome = parse_cli_outcome(result.stdout_path.read_text(encoding="utf-8"))
            self.assertTrue(outcome.completed)
            self.assertEqual(outcome.answer, "final answer")

    def test_replay_invalid_warns_but_does_not_fake_retry(self):
        text = '''{
          "meta": {"replayInvalid": true, "executionTrace": {"fallbackUsed": false}},
          "payloads": [{"text": "answer", "isReasoning": false}]
        }'''
        outcome = parse_cli_outcome(text)
        self.assertTrue(outcome.completed)
        self.assertIn("replay_unsafe_do_not_auto_retry", outcome.warnings)

    def test_error_payload_blocks_completion(self):
        text = '''{
          "meta": {"executionTrace": {"fallbackUsed": false}},
          "payloads": [{"text": "looks visible", "isReasoning": false, "isError": true}]
        }'''
        outcome = parse_cli_outcome(text)
        self.assertFalse(outcome.completed)
        self.assertIn("error_payload", outcome.blockers)

    def test_trusted_context_error_is_classified_without_treating_text_as_answer(self):
        text = '''{
          "meta": {
            "error": {
              "code": "context_length_exceeded",
              "message": "maximum context length is 32768 tokens"
            },
            "executionTrace": {"fallbackUsed": false}
          },
          "payloads": [{"text": "maximum context length", "isReasoning": false}]
        }'''
        outcome = parse_cli_outcome(text)
        self.assertFalse(outcome.completed)
        self.assertIsNone(outcome.answer)
        self.assertIn("context_overflow", outcome.blockers)
        self.assertEqual(outcome.flags["context_reason"], "context_overflow")

    def test_business_text_mentioning_context_does_not_classify_without_error_envelope(self):
        text = '''{
          "meta": {"executionTrace": {"fallbackUsed": false}},
          "payloads": [{"text": "上下文长度需要说明，但任务已完成。", "isReasoning": false}]
        }'''
        outcome = parse_cli_outcome(text)
        self.assertTrue(outcome.completed)
        self.assertIsNone(outcome.flags["context_reason"])


if __name__ == "__main__":
    unittest.main()
