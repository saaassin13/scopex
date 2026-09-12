from pathlib import Path
import tempfile
import unittest

from scopex.agent.environment import build_openclaw_env


class OpenClawEnvironmentTests(unittest.TestCase):
    def test_environment_is_private_and_minimal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env = build_openclaw_env(
                runtime_root=root / "runtime",
                config_path=root / "runtime" / "openclaw.json",
                docker_host="unix:///var/run/docker.sock",
                base_env={"PATH": "/usr/bin:/bin", "SECRET_SHOULD_NOT_LEAK": "x"},
            )
            self.assertEqual(env["OPENCLAW_LOAD_SHELL_ENV"], "0")
            self.assertEqual(env["DOCKER_HOST"], "unix:///var/run/docker.sock")
            self.assertNotIn("SECRET_SHOULD_NOT_LEAK", env)
            self.assertTrue(Path(env["HOME"]).is_dir())
            self.assertTrue(Path(env["OPENCLAW_STATE_DIR"]).is_dir())
            self.assertTrue(Path(env["XDG_CACHE_HOME"]).is_dir())

    def test_non_unix_docker_endpoint_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                build_openclaw_env(
                    runtime_root=Path(td),
                    config_path=Path(td) / "openclaw.json",
                    docker_host="tcp://127.0.0.1:2375",
                )


if __name__ == "__main__":
    unittest.main()
