from pathlib import Path
import json
import unittest

from scopex.agent.openclaw_config import OpenClawConfigSpec, build_openclaw_config
from scopex.agent.outcome import NATIVE_TOOL_LOOP_GUARD, classify_cli_runtime_guard


class OpenClawLoopDetectionTests(unittest.TestCase):
    def test_native_loop_detection_is_enabled_for_local_agent(self):
        cfg = build_openclaw_config(
            OpenClawConfigSpec(
                model_id="local-model",
                proxy_base_url="http://127.0.0.1:18000/v1",
                proxy_api_key="local-token",
                workspace=Path("/tmp/workspace"),
                audit_log=Path("/tmp/openclaw.log"),
                sandbox_root=Path("/tmp/sandboxes"),
                image="scopex-test:latest",
                agent_id="sx1",
                uid=1000,
                gid=1000,
            )
        )
        self.assertEqual(
            cfg["tools"]["loopDetection"],
            {"enabled": True},
        )

    def test_second_critical_loop_terminal_is_classified_as_runtime_guard(self):
        envelope = {
            "payloads": [
                {
                    "text": "OpenClaw stopped this run because tool-loop recovery encountered another critical loop.",
                    "isError": True,
                }
            ],
            "meta": {
                "replayInvalid": True,
                "livenessState": "abandoned",
                "error": {
                    "kind": "incomplete_turn",
                    "message": (
                        "OpenClaw stopped this run because tool-loop recovery "
                        "encountered another critical loop. No blocked tool action was executed."
                    ),
                },
                "executionTrace": {"fallbackUsed": False},
            },
        }
        self.assertEqual(
            classify_cli_runtime_guard(json.dumps(envelope)),
            NATIVE_TOOL_LOOP_GUARD,
        )

    def test_unrelated_incomplete_turn_is_not_misclassified(self):
        envelope = {
            "payloads": [{"text": "provider failed", "isError": True}],
            "meta": {
                "replayInvalid": True,
                "livenessState": "abandoned",
                "error": {
                    "kind": "incomplete_turn",
                    "message": "provider connection closed",
                },
                "executionTrace": {"fallbackUsed": False},
            },
        }
        self.assertIsNone(classify_cli_runtime_guard(json.dumps(envelope)))


if __name__ == "__main__":
    unittest.main()
