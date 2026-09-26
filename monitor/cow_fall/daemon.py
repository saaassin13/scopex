"""Cow-fall monitoring daemon: supervise sessions behind a loopback API.

Runs as the scopex-monitor container's main process (host network). The
detection kernel is the same FallSession the CLI uses; this module adds
what a 7x24 deployment needs on top:

- session supervision with capped backoff and a frame-freshness watchdog:
  a silently dead ssh tunnel leaves ffmpeg blocked in read() without EOF,
  the session thread stays alive, and only frame age reveals the outage;
- orphan-event close (open events left behind by a crashed daemon) and
  retention pruning so event archives cannot fill the disk;
- optional automatic VLM confirmation through the ScopeX Runtime API -
  one worker thread and a cooldown window, because vLLM is
  single-concurrency and shared with business tasks;
- a 127.0.0.1-only HTTP API that the ScopeX API proxies as /monitor/*.

Config precedence when composing the session config: active calibration
zones file < this daemon's JSON < environment (RTSP URL).
"""
from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from monitor.cow_fall.__main__ import session_config_from_raw
from monitor.cow_fall.session import FallSession, SessionConfig

EVENT_ID_RE = re.compile(r"^fall-[A-Za-z0-9_-]+$")
FRAME_NAME_RE = re.compile(r"^(pre|post)-\d{5}\.jpg$")
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost"}

CONFIRM_MESSAGE_TEMPLATE = (
    "牛掉落监控事件确认（事件 {event_id}，触发时间 {opened_at}）。"
    "请读取目录 /agent-data/cow-fall/events/{event_id}/ 下的 meta.json 与 frames/ 目录，"
    "用 view_image 依次查看事件开帧附近与前、中、后各一张代表帧（共 2~4 张即可，不要多看），"
    "判断画面中是否出现牛从转盘滑落、半悬挂在平台边或倒在走道的异常。"
    "输出结论（确认异常 / 无法确认 / 明显误报）并注明依据的帧文件名。"
)


@dataclass(frozen=True)
class DaemonConfig:
    camera: str
    bind_host: str = "127.0.0.1"
    bind_port: int = 8790
    model_path: Path = Path("/opt/ScalingRobotics/scopex/models-monitor/yolov8n-coco.onnx")
    output_root: Path = Path("/opt/ScalingRobotics/scopex/data/monitor/cow-fall")
    calibrations_root: Path | None = None
    # Direct zones file for deployments without the calibration store.
    zones_fallback: Path | None = None
    auto_start: bool = False
    auto_confirm: bool = False
    scopex_api_url: str = ""
    # Offline validation: replay this file instead of a live stream.
    video: Path | None = None
    watchdog_s: float = 60.0
    backoff_s: tuple[float, ...] = (5.0, 10.0, 20.0, 60.0)
    stable_s: float = 300.0
    poll_interval_s: float = 1.0
    retention_days: int = 30
    retention_min_events: int = 50
    retention_interval_s: float = 3600.0
    confirm_cooldown_s: float = 300.0
    confirm_timeout_s: float = 15.0


def load_daemon_config(path: Path) -> DaemonConfig:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    camera = str(raw.get("camera", "")).strip()
    if not camera:
        raise ValueError("monitor.json 缺少 camera")
    bind_host = str(raw.get("bind_host", "127.0.0.1"))
    if bind_host not in _LOOPBACK_HOSTS:
        # host-network container: binding anything else would expose the
        # control API to the VPN, wider than the ScopeX API itself.
        raise ValueError("bind_host 只允许 127.0.0.1/localhost（host 网络容器安全边界）")
    backoff = tuple(float(v) for v in raw.get("backoff_s", (5.0, 10.0, 20.0, 60.0)))
    if not backoff or any(v <= 0 for v in backoff):
        raise ValueError("backoff_s 必须是非空正数序列")
    kwargs: dict = {}
    if raw.get("calibrations_root"):
        kwargs["calibrations_root"] = Path(raw["calibrations_root"])
    if raw.get("zones_fallback"):
        kwargs["zones_fallback"] = Path(raw["zones_fallback"])
    if raw.get("video"):
        kwargs["video"] = Path(raw["video"])
    return DaemonConfig(
        camera=camera,
        bind_host=bind_host,
        bind_port=int(raw.get("bind_port", 8790)),
        model_path=Path(raw["model_path"]) if raw.get("model_path") else DaemonConfig.model_path,
        output_root=Path(raw["output_root"]) if raw.get("output_root") else DaemonConfig.output_root,
        auto_start=bool(raw.get("auto_start", False)),
        auto_confirm=bool(raw.get("auto_confirm", False)),
        scopex_api_url=str(raw.get("scopex_api_url", "")),
        watchdog_s=float(raw.get("watchdog_s", 60.0)),
        backoff_s=backoff,
        stable_s=float(raw.get("stable_s", 300.0)),
        poll_interval_s=float(raw.get("poll_interval_s", 1.0)),
        retention_days=int(raw.get("retention_days", 30)),
        retention_min_events=int(raw.get("retention_min_events", 50)),
        retention_interval_s=float(raw.get("retention_interval_s", 3600.0)),
        confirm_cooldown_s=float(raw.get("confirm_cooldown_s", 300.0)),
        confirm_timeout_s=float(raw.get("confirm_timeout_s", 15.0)),
        **kwargs,
    )


def read_calibration_snapshot(cfg: DaemonConfig) -> dict | None:
    """Active calibration {source, version} as of right now, or None."""
    if cfg.calibrations_root is None:
        return None
    meta_path = cfg.calibrations_root / cfg.camera / "meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    active = meta.get("active")
    if not isinstance(active, int):
        return None
    if not (cfg.calibrations_root / cfg.camera / f"zones-v{active}.json").is_file():
        return None
    return {"camera": cfg.camera, "version": active,
            "source": str(cfg.calibrations_root / cfg.camera / f"zones-v{active}.json")}


def resolve_zones_raw(cfg: DaemonConfig) -> tuple[dict, dict | None]:
    """(zones config dict, calibration snapshot used) for the next session."""
    snapshot = read_calibration_snapshot(cfg)
    if snapshot is not None:
        raw = json.loads(Path(snapshot["source"]).read_text(encoding="utf-8"))
        return raw, snapshot
    if cfg.zones_fallback is not None:
        return json.loads(cfg.zones_fallback.read_text(encoding="utf-8")), None
    raise ValueError(
        f"相机 {cfg.camera} 没有已激活的标定版本，也未配置 zones_fallback；"
        "请先在监控标定页面完成并启用一版标定")


def compose_session_raw(cfg: DaemonConfig, zones_raw: dict) -> dict:
    """Merge zones file with daemon deployment values (daemon wins)."""
    merged = dict(zones_raw)
    merged["model_path"] = str(cfg.model_path)
    merged["output_root"] = str(cfg.output_root)
    return merged


class EventStore:
    """Read/patch event archives under output_root/events."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def events_dir(self) -> Path:
        return self.root / "events"

    def list_events(self, limit: int = 50) -> list[dict]:
        events = []
        root = self.events_dir()
        if not root.is_dir():
            return []
        for meta_path in root.glob("fall-*/meta.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            meta["frame_count"] = len(list(meta_path.parent.glob("frames/*.jpg")))
            meta["pre_count"] = len(list(meta_path.parent.glob("frames/pre-*.jpg")))
            meta["post_count"] = len(list(meta_path.parent.glob("frames/post-*.jpg")))
            events.append(meta)
        events.sort(key=lambda m: float(m.get("opened_at") or 0.0), reverse=True)
        return events[:limit]

    def event_dir(self, event_id: str) -> Path:
        if not EVENT_ID_RE.fullmatch(event_id):
            raise ValueError("无效的事件编号")
        return self.events_dir() / event_id

    def frame_path(self, event_id: str, name: str) -> Path:
        if not FRAME_NAME_RE.fullmatch(name):
            raise ValueError("无效的帧文件名")
        path = self.event_dir(event_id) / "frames" / name
        if not path.is_file():
            raise FileNotFoundError(name)
        return path

    def read_meta(self, event_id: str) -> dict:
        path = self.event_dir(event_id) / "meta.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def update_meta(self, event_id: str, patch: dict) -> dict:
        """Read-modify-write with the same atomic replace as the session."""
        event_dir = self.event_dir(event_id)
        path = event_dir / "meta.json"
        meta = json.loads(path.read_text(encoding="utf-8"))
        meta.update(patch)
        temporary = event_dir / ".meta.json.tmp"
        temporary.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(path)
        return meta

    def close_orphans(self, post_s: float, grace_s: float = 120.0) -> int:
        """Close open events whose post window passed while no session ran."""
        now = time.time()
        closed = 0
        root = self.events_dir()
        if not root.is_dir():
            return 0
        for meta_path in root.glob("fall-*/meta.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if meta.get("status") != "open":
                continue
            opened = float(meta.get("last_seen_at") or meta.get("opened_at") or now)
            if now > opened + post_s + grace_s:
                self.update_meta(meta["event_id"], {"status": "closed",
                                                    "closed_at": now,
                                                    "closed_by": "daemon-orphan-sweep"})
                closed += 1
        return closed

    def prune(self, retention_days: int, min_events: int) -> int:
        """Drop events older than retention_days, keeping the newest min_events."""
        root = self.events_dir()
        if not root.is_dir():
            return 0
        events = self.list_events(limit=10**9)
        if len(events) <= min_events:
            return 0
        cutoff = time.time() - retention_days * 86400.0
        removed = 0
        for meta in events[min_events:]:
            if float(meta.get("opened_at") or 0.0) < cutoff:
                shutil.rmtree(self.event_dir(meta["event_id"]), ignore_errors=True)
                removed += 1
        return removed


class ConfirmWorker:
    """Serial VLM-confirmation worker: one in flight, cooldown between sends.

    Manual submissions (from the page) bypass the cooldown but still queue
    behind anything in flight.
    """

    def __init__(self, api_url: str, store: EventStore,
                 cooldown_s: float = 300.0, timeout_s: float = 15.0) -> None:
        self.api_url = api_url.rstrip("/")
        self.store = store
        self.cooldown_s = cooldown_s
        self.timeout_s = timeout_s
        self._queue: queue.Queue[tuple[str, bool]] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_sent_at = 0.0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="cow-fall-confirm", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def submit(self, event_id: str, manual: bool = False) -> None:
        self._queue.put((event_id, manual))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                event_id, manual = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._process(event_id, manual)
            except Exception as exc:  # never let one bad event kill the worker
                print(f"[confirm] {event_id} failed: {exc}", flush=True)

    def _process(self, event_id: str, manual: bool) -> None:
        try:
            meta = self.store.read_meta(event_id)
        except (OSError, ValueError) as exc:
            print(f"[confirm] {event_id}: cannot read meta ({exc})", flush=True)
            return
        if meta.get("confirm_task_id"):
            return  # already confirmed once; the page can still link out
        if not self.api_url:
            self.store.update_meta(event_id, {"confirm_error": "未配置 scopex_api_url"})
            return
        now = time.time()
        if not manual and now - self._last_sent_at < self.cooldown_s:
            self.store.update_meta(event_id, {
                "confirm_error": "冷却窗口内跳过自动确认，可稍后手动重发"})
            return
        opened_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(meta.get("opened_at") or now)))
        message = CONFIRM_MESSAGE_TEMPLATE.format(event_id=event_id, opened_at=opened_at)
        body = json.dumps({"message": message}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.api_url}/runs", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
            task_id = payload.get("id") or payload.get("task_id")
            if not task_id:
                raise ValueError(f"unexpected /runs response: {payload!r}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.store.update_meta(event_id, {"confirm_error": str(exc)[:300],
                                              "confirm_requested_at": now})
            print(f"[confirm] {event_id} request failed: {exc}", flush=True)
            return
        self._last_sent_at = now
        self.store.update_meta(event_id, {"confirm_task_id": task_id,
                                          "confirm_error": None,
                                          "confirm_requested_at": now})
        print(f"[confirm] {event_id} -> {task_id}", flush=True)


class SessionSupervisor:
    """Own at most one FallSession; restart with capped backoff.

    user_stopped is process-local: it blocks restarts until the next
    POST /start (or daemon restart, which follows auto_start again).
    """

    def __init__(self, cfg: DaemonConfig, rtsp_url: str | None,
                 on_event=None, session_factory=None,
                 _zones_override: dict | None = None) -> None:
        self.cfg = cfg
        self.rtsp_url = rtsp_url
        self._on_event = on_event
        self._session_factory = session_factory or self._build_session
        # Test hook: fixed zones raw instead of resolving the calibration.
        self._zones_override = _zones_override
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._session: FallSession | None = None
        self._session_started_at: float | None = None
        self._user_stopped = not cfg.auto_start
        self._next_restart_at: float | None = None
        self._backoff_index = 0
        self._restart_count = 0
        self._last_error: str | None = None
        self._running_calibration: dict | None = None

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="cow-fall-supervisor", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15)
            self._thread = None

    def request_start(self) -> None:
        with self._lock:
            self._user_stopped = False
            self._next_restart_at = None

    def request_stop(self) -> None:
        with self._lock:
            self._user_stopped = True
            self._next_restart_at = None

    # -- introspection --------------------------------------------------

    def frame_age_s(self) -> float | None:
        session = self._session
        if session is None:
            return None
        capture = getattr(session, "capture", None)
        latest = capture.buffer.latest() if capture is not None else None
        if latest is not None:
            return max(0.0, time.time() - latest.timestamp)
        # No frame at all yet: only counts as stale once the watchdog
        # window has passed since launch (RTSP handshake is not instant).
        if self._session_started_at is not None:
            age = time.time() - self._session_started_at
            if age > self.cfg.watchdog_s:
                return age
        return None

    def status(self) -> dict:
        with self._lock:
            user_stopped = self._user_stopped
            next_restart = self._next_restart_at
        session_alive = self._session is not None and self._session.is_running()
        if session_alive:
            state = "running"
        elif user_stopped:
            state = "stopped"
        elif next_restart is not None:
            state = "backoff"
        else:
            state = "error" if self._last_error else "starting"
        return {
            "state": state,
            "user_stopped": user_stopped,
            "frame_age_s": self.frame_age_s(),
            "restart_count": self._restart_count,
            "backoff_wait_s": max(0.0, next_restart - time.time()) if next_restart else None,
            "session_started_at": self._session_started_at,
            "source": self._source_desc(),
            "running_calibration": self._running_calibration,
            "active_calibration": read_calibration_snapshot(self.cfg),
            "auto_confirm": self.cfg.auto_confirm,
            "camera": self.cfg.camera,
            "output_root": str(self.cfg.output_root),
            "last_error": self._last_error,
        }

    # -- internals ------------------------------------------------------

    def _source_desc(self) -> str:
        if self.cfg.video is not None:
            return f"replay:{self.cfg.video}"
        if self.rtsp_url:
            host = urllib.parse.urlsplit(self.rtsp_url).hostname or ""
            return f"rtsp:{host}"  # never log credentials
        return None

    def _build_session(self, config: SessionConfig, on_event) -> FallSession:
        from monitor.cow_fall.detector import CowDetector
        from monitor.cow_fall.tracker import FallTracker
        return FallSession(config=config, detector=CowDetector(config.detector),
                           tracker=FallTracker(
                               trigger_sustain_s=config.trigger_sustain_s,
                               clear_sustain_s=config.clear_sustain_s,
                               merge_distance_px=config.merge_distance_px),
                           on_event=on_event)

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._user_stopped:
                self._stop.wait(self.cfg.poll_interval_s)
                continue
            if self._next_restart_at is not None:
                if time.time() < self._next_restart_at:
                    self._stop.wait(min(self.cfg.poll_interval_s,
                                        self._next_restart_at - time.time()))
                    continue
                self._next_restart_at = None
            self._launch_and_supervise()
            if self._stop.is_set():
                break
            # user_stopped here means the user halted a live session: park
            # at the top of the loop and wait for the next request_start.
            if not self._user_stopped:
                self._schedule_backoff()

    def _launch_and_supervise(self) -> None:
        try:
            if self._zones_override is not None:
                zones_raw, snapshot = dict(self._zones_override), None
            else:
                zones_raw, snapshot = resolve_zones_raw(self.cfg)
            raw = compose_session_raw(self.cfg, zones_raw)
            source = str(self.cfg.video) if self.cfg.video is not None else self.rtsp_url
            if not source:
                self._last_error = "未配置视频源：设置 COW_FALL_RTSP_URL 或 monitor.json 的 video"
                return
            config = session_config_from_raw(raw, source, replay=self.cfg.video is not None)
            session = self._session_factory(config, self._on_event)
        except Exception as exc:
            self._last_error = str(exc)
            print(f"[supervisor] launch failed: {exc}", flush=True)
            return  # caller schedules the backoff
        self._session = session
        self._running_calibration = snapshot
        self._session_started_at = time.time()
        self._last_error = None
        print(f"[supervisor] session started ({self._source_desc()}, "
              f"calibration v{snapshot['version'] if snapshot else 'fallback'})", flush=True)
        session.start()
        stable_reset = False
        try:
            while not self._stop.is_set() and not self._user_stopped:
                if not session.is_running():
                    print("[supervisor] session loop ended; restarting", flush=True)
                    break
                age = self.frame_age_s()
                if age is not None and age > self.cfg.watchdog_s:
                    print(f"[supervisor] frame age {age:.0f}s exceeds watchdog "
                          f"{self.cfg.watchdog_s:.0f}s (dead tunnel or stalled ffmpeg); "
                          "restarting", flush=True)
                    break
                if not stable_reset and time.time() - self._session_started_at >= self.cfg.stable_s:
                    # A session that held the stream for stable_s proves the
                    # pipeline works; next failure starts the ladder over.
                    self._backoff_index = 0
                    stable_reset = True
                self._stop.wait(self.cfg.poll_interval_s)
        finally:
            session.stop()
        self._session = None
        self._session_started_at = None

    def _schedule_backoff(self) -> None:
        with self._lock:
            if self._user_stopped:
                return
            delay = self.cfg.backoff_s[min(self._backoff_index, len(self.cfg.backoff_s) - 1)]
            self._backoff_index += 1
            self._restart_count += 1
            self._next_restart_at = time.time() + delay
        print(f"[supervisor] restart in {delay:.0f}s "
              f"(#{self._restart_count})", flush=True)


class _DaemonHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_http_handler(daemon: "CowFallDaemon") -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        # Loopback control plane; container stdout stays for lifecycle logs.
        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            pass

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib signature
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == "/status":
                self._send_json(200, {"ok": True, **daemon.supervisor.status()})
                return
            if parsed.path == "/events":
                query = urllib.parse.parse_qs(parsed.query)
                limit = min(int(query.get("limit", ["50"])[0] or 50), 500)
                self._send_json(200, {"ok": True, "events": daemon.store.list_events(limit)})
                return
            match = re.fullmatch(r"/events/([^/]+)/frames/([^/]+)", parsed.path)
            if match:
                self._send_frame(match.group(1), match.group(2))
                return
            self._send_json(404, {"ok": False, "error": "route not found"})

        def _send_frame(self, event_id: str, name: str) -> None:
            try:
                path = daemon.store.frame_path(event_id, name)
            except ValueError:
                self._send_json(400, {"ok": False, "error": "invalid event or frame name"})
                return
            except FileNotFoundError:
                self._send_json(404, {"ok": False, "error": "frame not found"})
                return
            data = path.read_bytes()
            self.send_response(200)
            # Event archives are immutable; the URL is stable forever.
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "private, max-age=31536000, immutable")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:  # noqa: N802 - stdlib signature
            parsed = urllib.parse.urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            if length > 4096:
                self._send_json(413, {"ok": False, "error": "body too large"})
                return
            if parsed.path == "/start":
                daemon.supervisor.request_start()
                self._send_json(202, {"ok": True, **daemon.supervisor.status()})
                return
            if parsed.path == "/stop":
                daemon.supervisor.request_stop()
                self._send_json(202, {"ok": True, **daemon.supervisor.status()})
                return
            match = re.fullmatch(r"/events/([^/]+)/confirm", parsed.path)
            if match:
                try:
                    daemon.store.read_meta(match.group(1))
                except (OSError, ValueError):
                    self._send_json(404, {"ok": False, "error": "event not found"})
                    return
                daemon.confirmer.submit(match.group(1), manual=True)
                self._send_json(202, {"ok": True, "queued": match.group(1)})
                return
            self._send_json(404, {"ok": False, "error": "route not found"})

    return Handler


class CowFallDaemon:
    def __init__(self, cfg: DaemonConfig, rtsp_url: str | None) -> None:
        self.cfg = cfg
        self.store = EventStore(cfg.output_root)
        # The worker always runs: auto_confirm only decides whether events
        # are queued automatically - manual confirm from the page must work
        # with the switch off.
        self.confirmer = ConfirmWorker(
            cfg.scopex_api_url, self.store,
            cooldown_s=cfg.confirm_cooldown_s, timeout_s=cfg.confirm_timeout_s)
        self.supervisor = SessionSupervisor(
            cfg, rtsp_url, on_event=self._on_event)
        self._http: _DaemonHTTPServer | None = None
        self._retention_thread: threading.Thread | None = None
        self._shutdown = threading.Event()

    def _on_event(self, event_dir: Path, event) -> None:
        # Session contract: never block the detection loop. Confirmation
        # is queued; archive writes already happened in the session.
        print(f"[event] {event.event_id} rules={event.rules}", flush=True)
        if self.cfg.auto_confirm:
            self.confirmer.submit(event.event_id, manual=False)

    def start(self) -> None:
        # Adopt events a previous process left open, then start layers.
        closed = self.store.close_orphans(self._post_window_s())
        if closed:
            print(f"[retention] closed {closed} orphan event(s)", flush=True)
        self.cfg.output_root.mkdir(parents=True, exist_ok=True)
        self.supervisor.start()
        self.confirmer.start()
        self._retention_thread = threading.Thread(
            target=self._retention_loop, name="cow-fall-retention", daemon=True)
        self._retention_thread.start()
        self._http = _DaemonHTTPServer((self.cfg.bind_host, self.cfg.bind_port),
                                       make_http_handler(self))
        print(f"[daemon] listening on {self.cfg.bind_host}:{self.cfg.bind_port}", flush=True)

    def _post_window_s(self) -> float:
        try:
            zones_raw, _ = resolve_zones_raw(self.cfg)
            return float(zones_raw.get("event_post_s", 30.0))
        except Exception:
            return 30.0

    def _retention_loop(self) -> None:
        while not self._shutdown.wait(self.cfg.retention_interval_s):
            try:
                self.store.close_orphans(self._post_window_s())
                removed = self.store.prune(self.cfg.retention_days,
                                           self.cfg.retention_min_events)
                if removed:
                    print(f"[retention] pruned {removed} event(s)", flush=True)
            except Exception as exc:
                print(f"[retention] sweep failed: {exc}", flush=True)

    def serve_forever(self) -> None:
        """Serve HTTP on a worker thread; block the caller until shutdown.

        BaseServer.shutdown() deadlocks when called from the thread running
        serve_forever (i.e. from a signal handler on the main thread), so
        the server loops here in a child thread and signals only set the
        stop event.
        """
        assert self._http is not None

        def _serve() -> None:
            try:
                self._http.serve_forever(poll_interval=0.5)
            except Exception:
                # A dead HTTP loop would leave a listening-but-mute socket
                # and a daemon that only looks alive; take the process down.
                import traceback
                traceback.print_exc()
                self.shutdown()

        server_thread = threading.Thread(target=_serve, name="cow-fall-http", daemon=True)
        server_thread.start()
        try:
            self._shutdown.wait()
        finally:
            self.shutdown()
            server_thread.join(timeout=10)

    def shutdown(self) -> None:
        if self._shutdown.is_set():
            return
        self._shutdown.set()
        if self._http is not None:
            self._http.shutdown()
        self.supervisor.shutdown()
        self.confirmer.stop()
        print("[daemon] stopped", flush=True)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)

    cfg = load_daemon_config(args.config)
    rtsp_url = os.environ.get("COW_FALL_RTSP_URL") or None
    daemon = CowFallDaemon(cfg, rtsp_url)

    def _request_stop(_signum, _frame) -> None:
        daemon.shutdown()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    daemon.start()
    daemon.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
