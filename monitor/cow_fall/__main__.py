"""Session CLI: start a cow-fall monitoring session.

RTSP credentials follow the POC convention: never in files. The stream URL
is read from stdin as {"camera-name": "rtsp://..."} JSON when the config
leaves it empty.

    echo '{"cam-main": "rtsp://..."}' | python3 -m monitor.cow_fall \
        --config /etc/scopex/cow-fall.json --duration 3600

Offline validation replays a recording through the identical pipeline:

    python3 -m monitor.cow_fall --config zones.json --video clip.mkv
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from monitor.cow_fall.capture import CaptureConfig
from monitor.cow_fall.detector import CowDetector, DetectorConfig
from monitor.cow_fall.foreground import ForegroundConfig
from monitor.cow_fall.session import FallSession, SessionConfig
from monitor.cow_fall.tracker import FallTracker
from monitor.cow_fall.zones import Point, ZoneConfig


def load_session_config(path: Path, input_url: str,
                        replay: bool = False) -> SessionConfig:
    return session_config_from_raw(json.loads(path.read_text()), input_url,
                                   replay=replay)


def session_config_from_raw(raw: dict, input_url: str,
                            replay: bool = False) -> SessionConfig:
    zone_raw = raw["zones"]
    platform_raw = zone_raw.get("platform_line")
    zones = ZoneConfig(
        carousel=[tuple(p) for p in zone_raw["carousel"]],
        forbidden_zones=tuple(
            tuple([tuple(p) for p in poly])
            for poly in zone_raw.get("forbidden_zones", ())
        ),
        platform_line=(tuple(tuple(p) for p in platform_raw)
                       if platform_raw else None),
        min_on_carousel_ratio=zone_raw.get("min_on_carousel_ratio", 0.5),
        platform_line_margin_px=zone_raw.get("platform_line_margin_px", 8.0),
        min_conf=zone_raw.get("min_conf", 0.5),
    )
    capture = CaptureConfig(
        rtsp_url=input_url,
        fps=raw.get("fps", 2.0),
        buffer_window_s=raw.get("buffer_window_s", 300.0),
        # -re reads the file at native rate so wall clock tracks video
        # time; live streams need the loss-tolerant tcp transport instead.
        extra_ffmpeg_args=("-re",) if replay else ("-rtsp_transport", "tcp"),
    )
    detector = DetectorConfig(model_path=Path(raw["model_path"]))
    return SessionConfig(
        capture=capture,
        zones=zones,
        detector=detector,
        foreground=ForegroundConfig(
            min_area_ratio=float(raw.get("min_area_ratio", 0.02)),
            warmup_frames=int(raw.get("warmup_frames", 50)),
        ),
        trigger_sustain_s=float(raw.get("trigger_sustain_s", 30.0)),
        clear_sustain_s=float(raw.get("clear_sustain_s", 10.0)),
        merge_distance_px=float(raw.get("merge_distance_px", 300.0)),
        event_pre_s=raw.get("event_pre_s", 30.0),
        event_post_s=raw.get("event_post_s", 30.0),
        output_root=Path(raw.get("output_root", "/task-scratch/cow-fall")),
    )


def read_rtsp_url(configured: str | None) -> str:
    if configured:
        return configured
    sources = json.loads(sys.stdin.read())
    if not isinstance(sources, dict) or not sources:
        raise SystemExit("expected {name: rtsp_url} JSON on stdin")
    return next(iter(sources.values()))


def probe_duration_seconds(video: Path) -> float | None:
    try:
        output = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return float(output)
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=None,
                        help="seconds; default 3600 live / video length +60 "
                             "for --video; 0 runs until interrupted")
    parser.add_argument("--rtsp-url", default=None,
                        help="override; by default read from stdin JSON")
    parser.add_argument("--video", type=Path, default=None,
                        help="replay this recording offline instead of "
                             "live RTSP (no credentials needed)")
    args = parser.parse_args(argv)

    replay = args.video is not None
    if replay:
        if not args.video.is_file():
            raise SystemExit(f"video not found: {args.video}")
        url = str(args.video)
    else:
        url = read_rtsp_url(args.rtsp_url)
    config = load_session_config(args.config, url, replay=replay)

    duration = args.duration
    if duration is None:
        if replay:
            probed = probe_duration_seconds(args.video)
            duration = (probed + 60.0) if probed else 3600.0
        else:
            duration = 3600.0
    detector = CowDetector(config.detector)
    tracker = FallTracker(
        trigger_sustain_s=config.trigger_sustain_s,
        clear_sustain_s=config.clear_sustain_s,
        merge_distance_px=config.merge_distance_px,
    )
    session = FallSession(
        config=config,
        detector=detector,
        tracker=tracker,
        on_event=lambda event_dir, event: print(
            f"[event] {event.event_id} rules={event.rules} -> {event_dir}",
            flush=True),
    )
    session.start()
    mode = f"replaying {args.video}" if replay else "live rtsp"
    print(f"session running ({mode}, duration {duration:.0f}s); "
          f"output under {config.output_root}", flush=True)

    # SIGTERM must reach session.stop() too: docker stop only sends
    # SIGTERM, and skipping cleanup would orphan the ffmpeg subprocess.
    interrupt = threading.Event()

    def _request_stop(_signum, _frame) -> None:
        interrupt.set()

    signal.signal(signal.SIGTERM, _request_stop)

    try:
        if duration > 0:
            deadline = time.monotonic() + duration
            while not interrupt.is_set() and time.monotonic() < deadline:
                if not session.is_running():
                    print("session loop ended (capture stopped); "
                          "shutting down", flush=True)
                    break
                time.sleep(1.0)
        else:
            while not interrupt.is_set() and session.is_running():
                time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        session.stop()
        print("session stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
