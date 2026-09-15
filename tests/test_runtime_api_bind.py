from __future__ import annotations

import unittest
from unittest.mock import patch

import scripts.runtime_api as runtime_api


class RuntimeApiBindTests(unittest.TestCase):
    def run_main(self, host: str, trusted: bool = False):
        args = [
            "--model", "test", "--base-url", "http://127.0.0.1:18002/v1",
            "--workspace", "/tmp/scopex-workspace", "--sandbox-image", "sandbox:test",
            "--host", host,
        ]
        if trusted:
            args.append("--allow-trusted-network-bind")
        with patch("scripts.runtime_api.sys.platform", "linux"), patch("scripts.runtime_api.os.geteuid", return_value=1000):
            return runtime_api.main(args)

    def test_non_loopback_requires_explicit_trusted_flag(self):
        with self.assertRaisesRegex(ValueError, "requires --allow-trusted-network-bind"):
            self.run_main("10.200.0.7")

    def test_trusted_bind_rejects_wildcard_and_hostname(self):
        for host in ("0.0.0.0", "scopex.local"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                self.run_main(host, trusted=True)

    def test_explicit_trusted_ip_passes_bind_validation(self):
        # Stop after bind validation without provisioning a real workspace.
        with patch("scripts.runtime_api.Path.mkdir", side_effect=RuntimeError("validated")):
            with self.assertRaisesRegex(RuntimeError, "validated"):
                self.run_main("10.200.0.7", trusted=True)
