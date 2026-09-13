from pathlib import Path
import unittest

from scopex.agent.openclaw_config import OpenClawConfigSpec, build_openclaw_config


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


if __name__ == "__main__":
    unittest.main()
