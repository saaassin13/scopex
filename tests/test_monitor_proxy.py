from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from monitor.cow_fall.daemon import (
    CowFallDaemon,
    DaemonConfig,
    make_http_handler,
    _DaemonHTTPServer,
)
from scopex.api.monitor_proxy import register_monitor_routes


class MonitorProxyTests(unittest.TestCase):
    """Proxy against a real loopback daemon: both sides under test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.cfg = DaemonConfig(camera="camera-96", output_root=self.root,
                                retention_interval_s=3600.0)
        self.daemon = CowFallDaemon(self.cfg, None)
        event_frames = self.root / "events" / "fall-x" / "frames"
        event_frames.mkdir(parents=True)
        (event_frames / "pre-00000.jpg").write_bytes(b"jpeg-bytes")
        (self.root / "events" / "fall-x" / "meta.json").write_text(json.dumps({
            "schema": 1, "event_id": "fall-x", "status": "closed",
            "opened_at": 123.0, "last_seen_at": 123.0, "rules": ["zone_foreground"]}))
        self.server = _DaemonHTTPServer(("127.0.0.1", 0), make_http_handler(self.daemon))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        app = FastAPI()
        register_monitor_routes(app, base_url=f"http://127.0.0.1:{self.server.server_address[1]}")
        self.client = TestClient(app)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def test_status_is_proxied(self):
        response = self.client.get("/monitor/status")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["state"], "stopped")
        self.assertEqual(payload["camera"], "camera-96")

    def test_events_listing_is_proxied(self):
        payload = self.client.get("/monitor/events").json()
        self.assertEqual(payload["events"][0]["event_id"], "fall-x")

    def test_frame_bytes_and_cache_header_pass_through(self):
        response = self.client.get("/monitor/events/fall-x/frames/pre-00000.jpg")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertEqual(response.headers["cache-control"],
                         "private, max-age=31536000, immutable")
        self.assertEqual(response.content, b"jpeg-bytes")

    def test_start_stop_and_confirm_round_trip(self):
        with mock.patch.object(self.daemon.supervisor, "request_start") as start:
            self.assertEqual(self.client.post("/monitor/start").status_code, 202)
            start.assert_called_once()
        with mock.patch.object(self.daemon.confirmer, "submit") as submit:
            response = self.client.post("/monitor/events/fall-x/confirm")
            self.assertEqual(response.status_code, 202)
            submit.assert_called_once_with("fall-x", manual=True)
        self.assertEqual(self.client.post("/monitor/stop").status_code, 202)

    def test_malformed_paths_are_rejected_before_proxying(self):
        # Whether normalization happens in the client (404) or validation
        # in the proxy (400), the request must never reach the daemon.
        for status in (
            self.client.get("/monitor/events/fall-x/frames/../../etc").status_code,
            self.client.get("/monitor/events/%2e%2e/frames/pre-00000.jpg").status_code,
            self.client.post("/monitor/events/nope/confirm").status_code,
        ):
            self.assertIn(status, (400, 404))


class MonitorUnavailableTests(unittest.TestCase):
    def test_daemon_absent_answers_503_with_error_code(self):
        app = FastAPI()
        register_monitor_routes(app, base_url="http://127.0.0.1:1")  # nothing listens
        client = TestClient(app)
        response = client.get("/monitor/status")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "monitor_unavailable")
        self.assertIn("scopex-monitor", response.json()["error"]["message"])


if __name__ == "__main__":
    unittest.main()
