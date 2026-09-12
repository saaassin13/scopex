from dataclasses import replace
from pathlib import Path
import unittest

from scopex.agent.openclaw_config import (
    ModelRequestSettings,
    OpenClawConfigSpec,
    build_openclaw_config,
)


class OpenClawConfigTests(unittest.TestCase):
    def spec(self):
        return OpenClawConfigSpec(
            model_id="qwen-local",
            proxy_base_url="http://127.0.0.1:19000/v1",
            proxy_api_key="local-token",
            workspace=Path("/tmp/workspace"),
            audit_log=Path("/tmp/openclaw.log"),
            sandbox_root=Path("/tmp/sandboxes"),
            image="scopex-sandbox:validated",
            agent_id="sx1",
            uid=1000,
            gid=1000,
            skills=("camera-diagnosis",),
        )

    def test_security_defaults_match_validated_runtime_boundary(self):
        cfg = build_openclaw_config(self.spec())
        defaults = cfg["agents"]["defaults"]
        sandbox = defaults["sandbox"]
        docker = sandbox["docker"]
        self.assertEqual(defaults["thinkingDefault"], "off")
        self.assertFalse(defaults["compaction"]["enabled"])
        self.assertEqual(sandbox["workspaceAccess"], "ro")
        self.assertEqual(docker["network"], "none")
        self.assertTrue(docker["readOnlyRoot"])
        self.assertEqual(docker["capDrop"], ["ALL"])
        self.assertEqual(docker["user"], "1000:1000")
        self.assertFalse(sandbox["browser"]["enabled"])
        self.assertFalse(cfg["tools"]["elevated"]["enabled"])
        self.assertEqual(cfg["tools"]["exec"]["host"], "sandbox")
        extra = defaults["models"]["vllm/qwen-local"]["params"]["extra_body"]
        self.assertEqual(extra["chat_template_kwargs"]["enable_thinking"], False)
        self.assertEqual(defaults["skills"], ["camera-diagnosis"])

    def test_provider_must_be_loopback(self):
        bad = replace(self.spec(), proxy_base_url="http://example.com/v1")
        with self.assertRaises(ValueError):
            build_openclaw_config(bad)

    def test_proxy_token_is_required(self):
        with self.assertRaises(ValueError):
            build_openclaw_config(replace(self.spec(), proxy_api_key=""))

    def test_thinking_cannot_be_reenabled(self):
        bad_request = ModelRequestSettings(enable_thinking=True)
        with self.assertRaises(ValueError):
            build_openclaw_config(replace(self.spec(), request=bad_request))

    def test_unapproved_tool_is_rejected(self):
        with self.assertRaises(ValueError):
            build_openclaw_config(replace(self.spec(), tools=("read", "browser")))


if __name__ == "__main__":
    unittest.main()
