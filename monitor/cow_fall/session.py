"""Monitoring session: wire capture, detection, tracker and event archives.

The session loop never blocks on event handling: firing an event snapshots
the ring buffer window and hands the event to a callback, then keeps
polling. Confirmation (VLM) work happens entirely outside this loop, which
preserves the "detection runs continuously" contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import threading
import time

from monitor.cow_fall.capture import CaptureConfig, RtspCapture
from monitor.cow_fall.detector import CowDetector, DetectorConfig
from monitor.cow_fall.foreground import ForegroundConfig, ZoneForeground, zone_occupied
from monitor.cow_fall.rules import FallObservation, evaluate_frame
from monitor.cow_fall.tracker import FallTracker, OpenEvent
from monitor.cow_fall.zones import ZoneConfig

EVENT_SCHEMA = 1


@dataclass(frozen=True)
class SessionConfig:
    capture: CaptureConfig
    zones: ZoneConfig
    detector: DetectorConfig
    foreground: ForegroundConfig = field(default_factory=ForegroundConfig)
    # Sustained-anomaly thresholds, calibrated from production replays.
    trigger_sustain_s: float = 30.0
    clear_sustain_s: float = 10.0
    merge_distance_px: float = 300.0
    # Frames persisted around a fired event, before and after the trigger.
    event_pre_s: float = 30.0
    event_post_s: float = 30.0
    poll_interval_s: float = 0.5
    output_root: Path = Path("/task-scratch/cow-fall")


@dataclass
class FallSession:
    config: SessionConfig
    detector: CowDetector
    tracker: FallTracker = field(default_factory=FallTracker)
    on_event: object = None  # callable(archived_event_dir, OpenEvent), must not block
    _thread: threading.Thread | None = None
    # Instance-local stop flag: a class-level Event() here would be shared
    # by every session in the process and one stop() would poison them all.
    _stop: threading.Event = field(default_factory=threading.Event)
    _started_at: float | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("session already started")
        self.capture = RtspCapture(self.config.capture)
        self.capture.start()
        self._started_at = time.time()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def is_running(self) -> bool:
        """True while the detection loop is alive (capture may have ended)."""
        return self._thread is not None and self._thread.is_alive()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None
        capture = getattr(self, "capture", None)
        if capture is not None:
            capture.stop()

    def _run(self) -> None:
        import traceback

        try:
            self._run_loop()
        except Exception:
            # A dead detection thread must be loud, not silent blindness.
            traceback.print_exc()
            print("[session] detection loop crashed; see traceback above",
                  flush=True)

    def _run_loop(self) -> None:
        import cv2
        import numpy as np

        foreground = ZoneForeground(self.config.zones, self.config.foreground)
        last_frame_ref = None
        while not self._stop.is_set():
            time.sleep(self.config.poll_interval_s)
            frame = self.capture.buffer.latest()
            if frame is None:
                if self.capture.stopped_at is not None:
                    print("[session] capture ended; stopping detection",
                          flush=True)
                    break
                continue
            if frame is last_frame_ref:
                continue
            last_frame_ref = frame
            image = np.frombuffer(frame.data, dtype=np.uint8)
            bgr = cv2.imdecode(image, cv2.IMREAD_COLOR)
            if bgr is None:
                continue
            height, width = bgr.shape[:2]

            # Primary trigger: persistent foreground inside the forbidden
            # zone. YOLO runs only once the zone is occupied, as auxiliary
            # labelling (cows on the carousel are mostly occluded, so cow
            # detection alone cannot be the trigger).
            ratio, centroid = foreground.update(bgr)
            anomalies: list[FallObservation] = []
            if zone_occupied(ratio, self.config.foreground) and centroid is not None:
                detections = self.detector.detect(bgr, (width, height))
                pad = 40.0
                anomalies.append(FallObservation(
                    box=(centroid[0] - pad, centroid[1] - pad,
                         centroid[0] + pad, centroid[1] + pad),
                    center=centroid,
                    fully_outside=True,
                    mostly_off=False,
                    below_platform=False,
                ))
                # Cow rules add independent observations when a cow is
                # clearly out of place (auxiliary to the zone trigger).
                anomalies.extend(evaluate_frame(detections, self.config.zones))
            fired = self.tracker.update(frame.timestamp, anomalies)
            for event in fired:
                self._archive_open(event)
            # Feed the post window: keep archiving until the event closes.
            self._archive_follow_up()

    def _archive_open(self, event: OpenEvent) -> None:
        now = time.time()
        event_dir = self.config.output_root / "events" / event.event_id
        frames_dir = event_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        start = now - self.config.event_pre_s
        for index, frame in enumerate(self.capture.buffer.window(start, now)):
            (frames_dir / f"pre-{index:05d}.jpg").write_bytes(frame.data)
        self._write_meta(event_dir, event, now, status="open")
        if callable(self.on_event):
            self.on_event(event_dir, event)

    def _archive_follow_up(self) -> None:
        """Persist frames for open events while their post window runs."""
        events_root = self.config.output_root / "events"
        if not events_root.is_dir():
            return
        now = time.time()
        for meta_path in events_root.glob("fall-*/meta.json"):
            event_dir = meta_path.parent
            frames_dir = event_dir / "frames"
            try:
                meta = json.loads(meta_path.read_text())
            except (OSError, ValueError):
                continue
            if meta.get("status") != "open":
                continue
            opened = float(meta.get("last_seen_at") or meta.get("opened_at") or now)
            deadline = opened + self.config.event_post_s
            if now > deadline + self.config.poll_interval_s:
                if meta.get("status") == "open":
                    self._mark_closed(event_dir, meta)
                continue
            existing = {p.name for p in frames_dir.glob("post-*.jpg")}
            for index, frame in enumerate(self.capture.buffer.window(
                    opened, min(now, deadline))):
                name = f"post-{index:05d}.jpg"
                if name not in existing:
                    (frames_dir / name).write_bytes(frame.data)

    def _mark_closed(self, event_dir: Path, meta: dict) -> None:
        meta["status"] = "closed"
        meta["closed_at"] = time.time()
        temporary = event_dir / ".meta.json.tmp"
        temporary.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
        temporary.replace(event_dir / "meta.json")

    def _write_meta(self, event_dir: Path, event: OpenEvent,
                    now: float, status: str) -> None:
        meta = {
            "schema": EVENT_SCHEMA,
            "event_id": event.event_id,
            "status": status,
            "rules": list(event.rules),
            "opened_at": event.opened_at,
            "last_seen_at": event.last_seen_at,
            "center": list(event.center),
            "merged_updates": event.merged_updates,
            "written_at": now,
        }
        target = event_dir / "meta.json"
        temporary = event_dir / ".meta.json.tmp"
        temporary.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
        temporary.replace(target)
