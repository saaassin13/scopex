from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request

from monitor.cow_fall.capture import Frame
from monitor.cow_fall.daemon import (
    ConfirmWorker,
    CowFallDaemon,
    DaemonConfig,
    EventStore,
    SessionSupervisor,
    compose_session_raw,
    load_daemon_config,
    make_http_handler,
    resolve_zones_raw,
    _DaemonHTTPServer,
)


def wait_for(predicate, timeout_s: float = 5.0, interval_s: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return predicate()


def fast_config(**overrides) -> DaemonConfig:
    values = dict(
        camera="camera-96",
        watchdog_s=0.3,
        backoff_s=(0.05, 0.1),
        stable_s=0.4,
        poll_interval_s=0.02,
        retention_interval_s=3600.0,
    )
    values.update(overrides)
    return DaemonConfig(**values)


class FakeBuffer:
    """Latest frame goes stale after `stale_after` (None = always fresh)."""

    def __init__(self, stale_after: float | None) -> None:
        self.stale_after = stale_after
        self.frozen_ts: float | None = None

    def latest(self) -> Frame | None:
        now = time.time()
        if self.stale_after is None or now < self.stale_after:
            return Frame(timestamp=now, data=b"jpeg")
        if self.frozen_ts is None:
            self.frozen_ts = now
        return Frame(timestamp=self.frozen_ts, data=b"jpeg")


class FakeSession:
    """Programmable stand-in: dies after alive_s, optional stale frames."""

    instances: list["FakeSession"] = []

    def __init__(self, config, on_event=None, *, alive_s=10.0,
                 stale_after: float | None = None) -> None:
        self.config = config
        self.on_event = on_event
        self.alive_s = alive_s
        self.running = False
        self.started_at = time.time()
        self.stopped = False
        self.capture = mock.Mock(buffer=FakeBuffer(stale_after))
        FakeSession.instances.append(self)

    def start(self) -> None:
        self.running = True

    def is_running(self) -> bool:
        return self.running and time.time() - self.started_at < self.alive_s

    def stop(self) -> None:
        self.running = False
        self.stopped = True


class DaemonConfigTests(unittest.TestCase):
    def test_bind_host_must_be_loopback(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "monitor.json"
            for bad in ("0.0.0.0", "10.200.0.2", ""):
                path.write_text(json.dumps({"camera": "camera-96",
                                            "bind_host": bad}))
                with self.assertRaises(ValueError):
                    load_daemon_config(path)

    def test_rejects_empty_or_negative_backoff(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "monitor.json"
            path.write_text(json.dumps({"camera": "c", "backoff_s": []}))
            with self.assertRaises(ValueError):
                load_daemon_config(path)
            path.write_text(json.dumps({"camera": "c", "backoff_s": [1, -2]}))
            with self.assertRaises(ValueError):
                load_daemon_config(path)

    def test_missing_camera_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "monitor.json"
            path.write_text(json.dumps({}))
            with self.assertRaises(ValueError):
                load_daemon_config(path)


class ComposeTests(unittest.TestCase):
    def _calibration_dir(self, td: str, version: int = 2) -> Path:
        root = Path(td) / "calibrations"
        camera = root / "camera-96"
        camera.mkdir(parents=True)
        zones = {"fps": 2.0, "zones": {"carousel": [[0, 0], [9, 0], [9, 9]],
                                       "forbidden_zones": [[[0, 9], [9, 9], [4, 5]]]}}
        (camera / f"zones-v{version}.json").write_text(json.dumps(zones))
        (camera / "meta.json").write_text(json.dumps({"active": version, "versions": []}))
        return root

    def test_active_calibration_is_preferred_over_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._calibration_dir(td)
            fallback = Path(td) / "manual.json"
            fallback.write_text(json.dumps({"zones": {"carousel": [[1, 1]]}}))
            cfg = fast_config(calibrations_root=root, zones_fallback=fallback)
            raw, snapshot = resolve_zones_raw(cfg)
            self.assertEqual(snapshot["version"], 2)
            self.assertIn("forbidden_zones", raw["zones"])

    def test_fallback_used_without_calibration_store(self):
        with tempfile.TemporaryDirectory() as td:
            fallback = Path(td) / "manual.json"
            fallback.write_text(json.dumps({"fps": 1.0, "zones": {}}))
            cfg = fast_config(zones_fallback=fallback)
            raw, snapshot = resolve_zones_raw(cfg)
            self.assertIsNone(snapshot)
            self.assertEqual(raw["fps"], 1.0)

    def test_no_zones_at_all_is_an_error(self):
        cfg = fast_config()
        with self.assertRaises(ValueError):
            resolve_zones_raw(cfg)

    def test_daemon_overrides_deployment_values(self):
        zones = {"model_path": "/old.onnx", "output_root": "/old/root",
                 "zones": {"carousel": [[0, 0], [9, 0], [9, 9]]}}
        cfg = fast_config()
        merged = compose_session_raw(cfg, zones)
        self.assertEqual(merged["model_path"], str(cfg.model_path))
        self.assertEqual(merged["output_root"], str(cfg.output_root))
        # zones content passes through untouched
        self.assertEqual(merged["zones"]["carousel"][0], [0, 0])


class EventStoreTests(unittest.TestCase):
    def _make_event(self, root: Path, event_id: str, status: str = "closed",
                    opened_at: float = 0.0, frames: int = 2) -> None:
        event_dir = root / "events" / event_id
        (event_dir / "frames").mkdir(parents=True, exist_ok=True)
        for index in range(frames):
            (event_dir / "frames" / f"pre-{index:05d}.jpg").write_bytes(b"j")
        meta = {"schema": 1, "event_id": event_id, "status": status,
                "opened_at": opened_at, "last_seen_at": opened_at,
                "rules": ["zone_foreground"]}
        (event_dir / "meta.json").write_text(json.dumps(meta))

    def test_list_events_sorted_newest_first_with_frame_count(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make_event(root, "fall-b", opened_at=200.0, frames=3)
            self._make_event(root, "fall-a", opened_at=100.0, frames=1)
            store = EventStore(root)
            events = store.list_events()
            self.assertEqual([e["event_id"] for e in events], ["fall-b", "fall-a"])
            self.assertEqual(events[0]["frame_count"], 3)
            self.assertEqual(store.list_events(limit=1)[0]["event_id"], "fall-b")

    def test_close_orphans_only_closes_past_window(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            now = time.time()
            self._make_event(root, "fall-old", status="open", opened_at=now - 500)
            self._make_event(root, "fall-recent", status="open", opened_at=now - 5)
            self._make_event(root, "fall-closed", status="closed", opened_at=now - 500)
            store = EventStore(root)
            closed = store.close_orphans(post_s=30.0)
            self.assertEqual(closed, 1)
            self.assertEqual(store.read_meta("fall-old")["status"], "closed")
            self.assertEqual(store.read_meta("fall-recent")["status"], "open")

    def test_prune_respects_minimum_keep(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            now = time.time()
            for index in range(5):
                self._make_event(root, f"fall-{index}", opened_at=now - (index + 1) * 86400.0)
            store = EventStore(root)
            removed = store.prune(retention_days=1, min_events=3)
            self.assertEqual(removed, 2)  # keeps the newest 3
            self.assertTrue((root / "events" / "fall-0").is_dir())  # newest kept
            self.assertFalse((root / "events" / "fall-4").is_dir())  # oldest pruned

    def test_frame_path_rejects_traversal_and_bad_names(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make_event(root, "fall-x", frames=1)
            store = EventStore(root)
            self.assertTrue(store.frame_path("fall-x", "pre-00000.jpg").is_file())
            for event_id, name in (("fall-x", "../../meta.json"),
                                   ("../escape", "pre-00000.jpg"),
                                   ("fall-x", "post-abc.jpg"),
                                   ("fall-x", "other-00000.jpg"),
                                   ("fall%2Fx", "pre-00000.jpg")):
                with self.assertRaises((ValueError, FileNotFoundError)):
                    store.frame_path(event_id, name)

    def test_update_meta_merges_confirm_fields(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make_event(root, "fall-x")
            store = EventStore(root)
            store.update_meta("fall-x", {"confirm_task_id": "task-1"})
            self.assertEqual(store.read_meta("fall-x")["confirm_task_id"], "task-1")
            self.assertEqual(store.read_meta("fall-x")["status"], "closed")


class ConfirmWorkerTests(unittest.TestCase):
    def _store_with_event(self, td: str) -> EventStore:
        store = EventStore(Path(td))
        event_dir = store.events_dir() / "fall-x"
        (event_dir / "frames").mkdir(parents=True)
        (event_dir / "meta.json").write_text(json.dumps({
            "schema": 1, "event_id": "fall-x", "status": "closed",
            "opened_at": 100.0, "rules": []}))
        return store

    def _mock_runs_response(self, payload: dict) -> mock.MagicMock:
        response = mock.MagicMock()
        response.read.return_value = json.dumps(payload).encode("utf-8")
        response.__enter__.return_value = response
        return response

    def test_success_records_task_id(self):
        with tempfile.TemporaryDirectory() as td:
            store = self._store_with_event(td)
            worker = ConfirmWorker("http://10.0.0.1:8787", store)
            with mock.patch("monitor.cow_fall.daemon.urllib.request.urlopen",
                            return_value=self._mock_runs_response({"id": "task-abc"})) as post:
                worker._process("fall-x", manual=False)
            self.assertEqual(store.read_meta("fall-x")["confirm_task_id"], "task-abc")
            request = post.call_args.args[0]
            body = json.loads(request.data)
            self.assertIn("/agent-data/cow-fall/events/fall-x/", body["message"])
            self.assertEqual(request.full_url, "http://10.0.0.1:8787/runs")

    def test_already_confirmed_events_are_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            store = self._store_with_event(td)
            store.update_meta("fall-x", {"confirm_task_id": "task-old"})
            worker = ConfirmWorker("http://x", store)
            with mock.patch("monitor.cow_fall.daemon.urllib.request.urlopen") as post:
                worker._process("fall-x", manual=False)
            post.assert_not_called()
            self.assertEqual(store.read_meta("fall-x")["confirm_task_id"], "task-old")

    def test_cooldown_skips_automatic_but_not_manual(self):
        with tempfile.TemporaryDirectory() as td:
            store = self._store_with_event(td)
            worker = ConfirmWorker("http://x", store, cooldown_s=300.0)
            worker._last_sent_at = time.time()
            with mock.patch("monitor.cow_fall.daemon.urllib.request.urlopen") as post:
                worker._process("fall-x", manual=False)
            post.assert_not_called()
            self.assertIn("冷却", store.read_meta("fall-x")["confirm_error"])
            store.update_meta("fall-x", {"confirm_error": None})
            with mock.patch("monitor.cow_fall.daemon.urllib.request.urlopen",
                            return_value=self._mock_runs_response({"id": "task-2"})):
                worker._process("fall-x", manual=True)  # manual bypasses cooldown
            self.assertEqual(store.read_meta("fall-x")["confirm_task_id"], "task-2")

    def test_request_failure_is_recorded_for_manual_retry(self):
        with tempfile.TemporaryDirectory() as td:
            store = self._store_with_event(td)
            worker = ConfirmWorker("http://x", store)
            with mock.patch("monitor.cow_fall.daemon.urllib.request.urlopen",
                            side_effect=OSError("connection refused")):
                worker._process("fall-x", manual=True)
            self.assertIsNone(store.read_meta("fall-x").get("confirm_task_id"))
            self.assertIn("connection refused", store.read_meta("fall-x")["confirm_error"])

    def test_missing_api_url_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as td:
            store = self._store_with_event(td)
            worker = ConfirmWorker("", store)
            worker._process("fall-x", manual=True)
            self.assertIn("scopex_api_url", store.read_meta("fall-x")["confirm_error"])


class SessionSupervisorTests(unittest.TestCase):
    def setUp(self):
        FakeSession.instances = []

    def _supervisor(self, cfg: DaemonConfig, rtsp_url: str = "rtsp://localhost:8554/cam",
                    **factory_kwargs) -> SessionSupervisor:
        zones = {"zones": {"carousel": [[0, 0], [9, 0], [9, 9]]},
                 "model_path": "/m.onnx", "output_root": "/tmp/out"}

        def factory(config, on_event):
            return FakeSession(config, on_event, **factory_kwargs)

        return SessionSupervisor(cfg, rtsp_url, session_factory=factory,
                                 _zones_override=zones)

    def test_restarts_with_backoff_when_session_dies(self):
        cfg = fast_config()
        supervisor = self._supervisor(cfg, alive_s=0.15)
        supervisor.request_start()
        supervisor.start()
        try:
            self.assertTrue(wait_for(lambda: len(FakeSession.instances) >= 3, timeout_s=6))
            self.assertGreaterEqual(supervisor.status()["restart_count"], 2)
            self.assertTrue(all(s.stopped or s.is_running() for s in FakeSession.instances),
                            "a session must be either running or cleanly stopped")
        finally:
            supervisor.shutdown()

    def test_watchdog_restarts_on_stale_frames(self):
        # Session never dies but frames freeze: the silent-tunnel scenario.
        cfg = fast_config(watchdog_s=0.3)
        supervisor = self._supervisor(cfg, alive_s=30.0, stale_after=time.time() + 0.2)
        supervisor.request_start()
        supervisor.start()
        try:
            self.assertTrue(wait_for(lambda: len(FakeSession.instances) >= 2, timeout_s=6),
                            "watchdog must restart on stale frames while session lives")
            status = supervisor.status()
            self.assertGreaterEqual(status["restart_count"], 1)
        finally:
            supervisor.shutdown()

    def test_user_stop_blocks_restart_and_start_resumes(self):
        cfg = fast_config()
        supervisor = self._supervisor(cfg, alive_s=0.1)
        supervisor.request_start()
        supervisor.start()
        try:
            self.assertTrue(wait_for(lambda: len(FakeSession.instances) >= 1))
            supervisor.request_stop()
            count_after_stop = len(FakeSession.instances)
            time.sleep(0.4)
            self.assertEqual(len(FakeSession.instances), count_after_stop)
            self.assertEqual(supervisor.status()["state"], "stopped")
            supervisor.request_start()
            self.assertTrue(wait_for(lambda: len(FakeSession.instances) > count_after_stop))
        finally:
            supervisor.shutdown()
        self.assertTrue(all(s.stopped for s in FakeSession.instances))

    def test_stable_session_resets_backoff(self):
        cfg = fast_config(backoff_s=(0.05, 5.0), stable_s=0.3)
        # First session dies fast (backoff escalates to 5s); after a
        # restart the second lives long enough to reset the ladder.
        lifetimes = iter([0.05, 30.0])

        def factory(config, on_event):
            return FakeSession(config, on_event, alive_s=next(lifetimes))

        zones = {"zones": {"carousel": [[0, 0], [9, 0], [9, 9]]}}
        supervisor = SessionSupervisor(cfg, "rtsp://x", session_factory=factory,
                                        _zones_override=zones)
        supervisor.request_start()
        supervisor.start()
        try:
            self.assertTrue(wait_for(lambda: len(FakeSession.instances) >= 2, timeout_s=4))
            time.sleep(0.35)  # second session outlives stable_s
            self.assertTrue(FakeSession.instances[-1].is_running())
            self.assertEqual(supervisor._backoff_index, 0)
        finally:
            supervisor.shutdown()

    def test_missing_source_reports_error(self):
        cfg = fast_config()
        zones = {"zones": {"carousel": [[0, 0], [9, 0], [9, 9]]}}
        supervisor = SessionSupervisor(cfg, None, _zones_override=zones)
        supervisor.request_start()
        supervisor.start()
        try:
            self.assertTrue(wait_for(lambda: supervisor.status()["last_error"] is not None))
            self.assertIn("视频源", supervisor.status()["last_error"])
            self.assertEqual(FakeSession.instances, [])
            self.assertIsNone(supervisor.status()["source"])
        finally:
            supervisor.shutdown()


class DaemonHttpTests(unittest.TestCase):
    def setUp(self):
        FakeSession.instances = []
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.cfg = fast_config(output_root=self.root)
        self.daemon = CowFallDaemon(self.cfg, None)
        event_dir = self.root / "events" / "fall-x" / "frames"
        event_dir.mkdir(parents=True)
        (event_dir / "pre-00000.jpg").write_bytes(b"fake-jpeg")
        (self.root / "events" / "fall-x" / "meta.json").write_text(json.dumps({
            "schema": 1, "event_id": "fall-x", "status": "closed",
            "opened_at": 123.0, "last_seen_at": 123.0, "rules": ["zone_foreground"]}))
        self.server = _DaemonHTTPServer(("127.0.0.1", 0), make_http_handler(self.daemon))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def _get(self, path: str) -> tuple[bytes, dict]:
        with urllib.request.urlopen(self.base + path, timeout=5) as response:
            return response.read(), dict(response.headers)

    def test_status_reports_initial_stopped_state(self):
        body, _ = self._get("/status")
        payload = json.loads(body)
        self.assertFalse(payload["ok"] is False)
        self.assertEqual(payload["state"], "stopped")  # auto_start defaults off
        self.assertEqual(payload["camera"], "camera-96")
        self.assertFalse(payload["auto_confirm"])
        self.assertIsNone(payload["source"])

    def test_events_listing(self):
        body, _ = self._get("/events")
        payload = json.loads(body)
        self.assertEqual(payload["events"][0]["event_id"], "fall-x")
        self.assertEqual(payload["events"][0]["frame_count"], 1)

    def test_frame_served_with_immutable_cache(self):
        body, headers = self._get("/events/fall-x/frames/pre-00000.jpg")
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        self.assertEqual(headers["Cache-Control"],
                         "private, max-age=31536000, immutable")
        self.assertEqual(body, b"fake-jpeg")

    def test_traversal_and_unknown_routes_rejected(self):
        for path in ("/events/fall-x/frames/..%2fmeta.json",
                     "/events/../meta.json/frames/pre-00000.jpg",
                     "/nope"):
            with self.assertRaises(urllib.error.HTTPError):
                self._get(path)

    def test_start_stop_round_trip_and_confirm_queue(self):
        self.daemon.supervisor.request_start = mock.Mock(
            wraps=self.daemon.supervisor.request_start)
        request = urllib.request.Request(self.base + "/start", data=b"", method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 202)
            json.loads(response.read())
        self.daemon.supervisor.request_start.assert_called()
        self.daemon.confirmer.submit = mock.Mock()
        request = urllib.request.Request(self.base + "/events/fall-x/confirm",
                                         data=b"", method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 202)
        self.daemon.confirmer.submit.assert_called_once_with("fall-x", manual=True)
        request = urllib.request.Request(self.base + "/stop", data=b"", method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read())
        self.assertTrue(payload["user_stopped"])


if __name__ == "__main__":
    unittest.main()
