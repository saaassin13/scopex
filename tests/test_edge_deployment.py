from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class EdgeDeploymentTests(unittest.TestCase):
    def test_first_start_detects_vpn_and_preserves_external_data_root(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            edge_root = base / "scopex"
            app = edge_root / "app"
            shutil.copytree(ROOT / "deploy" / "edge", app / "deploy" / "edge")
            (app / "frontend" / "dist").mkdir(parents=True)
            (app / "frontend" / "dist" / "index.html").write_text("ok")
            (edge_root / "model" / "Qwen3.8-27B-NVFP4").mkdir(parents=True)
            (edge_root / "model" / "Qwen3.8-27B-NVFP4" / "config.json").write_text("{}")
            logs, camera = base / "logs", base / "camera"
            logs.mkdir(); camera.mkdir()

            config = (app / "deploy" / "edge" / "edge.env.example").read_text()
            config = config.replace("/opt/ScalingRobotics/scopex", str(edge_root))
            config = config.replace("/opt/ScalingRobotics/CowDisinfect/Log", str(logs))
            config = config.replace("/opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera", str(camera))
            (app / "deploy" / "edge" / "edge.env.example").write_text(config)

            fake = base / "bin"; fake.mkdir()
            calls = base / "docker.calls"
            scripts = {
                "docker": f"#!/bin/sh\necho \"$*\" >> {calls}\nexit 0\n",
                "ip": "#!/bin/sh\necho '7: wg0    inet 10.200.0.77/24 scope global wg0'\n",
                "curl": "#!/bin/sh\nexit 0\n",
                "stat": "#!/bin/sh\necho 988\n",
            }
            for name, content in scripts.items():
                path = fake / name; path.write_text(content); path.chmod(0o755)
            env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}", "SCOPEX_ROOT": str(edge_root)}
            run = subprocess.run([str(app / "deploy" / "edge" / "start.sh")], env=env,
                                 text=True, capture_output=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("http://10.200.0.77:8787", run.stdout)
            self.assertTrue((edge_root / "config" / "edge.env").is_file())
            self.assertEqual((edge_root / "config" / "edge.env").stat().st_mode & 0o777, 0o600)
            self.assertIn("SCOPEX_IMAGE_LIMIT=12", (edge_root / "config" / "edge.env").read_text())
            runtime_config = edge_root / "config" / "edge.runtime.env"
            self.assertEqual(runtime_config.stat().st_mode & 0o777, 0o600)
            self.assertIn("SCOPEX_VPN_IP=10.200.0.77", runtime_config.read_text())
            self.assertTrue((edge_root / "data" / "runtime-api").is_dir())
            self.assertIn("compose", calls.read_text())

    def test_runtime_uses_the_device_docker_client(self):
        compose = (ROOT / "deploy" / "edge" / "compose.yaml").read_text()
        self.assertIn("/usr/bin/docker:/usr/local/bin/docker:ro", compose)
        self.assertIn("      - --enable-compaction", compose)

if __name__ == "__main__":
    unittest.main()
