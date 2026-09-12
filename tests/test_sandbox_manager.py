from pathlib import Path
import os
import tempfile
import textwrap
import unittest

from scopex.agent.sandbox import SandboxManager


class SandboxManagerTests(unittest.TestCase):
    def test_cleanup_only_operates_on_listed_prefix_containers(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log = root / "docker.log"
            docker = root / "fake-docker"
            docker.write_text(
                "#!/usr/bin/env python3\n" + textwrap.dedent(f'''\
                import pathlib
                import sys
                log = pathlib.Path({str(log)!r})
                args = sys.argv[1:]
                with log.open("a") as f:
                    f.write(" ".join(args) + "\\n")
                if args[:2] == ["ps", "-aq"]:
                    print("abc123")
                '''),
                encoding="utf-8",
            )
            docker.chmod(0o755)
            manager = SandboxManager(
                docker_bin=str(docker),
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                container_prefix="scopex-sx1-",
            )
            result = manager.cleanup()
            self.assertEqual(result.container_ids, ("abc123",))
            self.assertEqual(result.warnings, ())
            rows = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(rows[0], "ps -aq --filter name=scopex-sx1-")
            self.assertEqual(rows[1], "stop --time 2 abc123")
            self.assertEqual(rows[2], "rm -f abc123")

    def test_broad_prefix_is_rejected(self):
        with self.assertRaises(ValueError):
            SandboxManager(docker_bin="docker", env={}, container_prefix="sx")


if __name__ == "__main__":
    unittest.main()
