"""Offline foreground calibration scan over a recording.

Full-speed scan (no -re): decodes every frame, feeds the zone foreground
detector and reports ratio/centroid time series plus run-length analysis.
The key output for calibration is the longest continuous occupied span,
which must stay below trigger_sustain_s for a false-positive-free scene.

    python3 scripts/scan_foreground.py clip.mkv \
        --config docs/monitoring/zones-camera-96-v1.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor.cow_fall.capture import split_jpeg_stream
from monitor.cow_fall.foreground import (
    ForegroundConfig,
    ZoneForeground,
    zone_occupied,
)
from monitor.cow_fall.zones import ZoneConfig


def decode_frames(video: Path, fps: float):
    process = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
         "-i", str(video), "-vf", f"fps={fps}",
         "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    partial = b""
    try:
        while True:
            chunk = process.stdout.read1(65536)
            if not chunk:
                break
            images, partial = split_jpeg_stream(chunk, partial)
            yield from images
    finally:
        # Abandoned generators must not leak the ffmpeg subprocess.
        process.terminate()
        process.wait()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--config", type=Path, required=True,
                        help="session config JSON (reads the zones block)")
    parser.add_argument("--report-above", type=float, default=0.015,
                        help="print frames whose ratio exceeds this")
    parser.add_argument("--warmup", type=int, default=None,
                        help="override warmup frames (default from config)")
    args = parser.parse_args(argv)

    import cv2
    import numpy as np

    raw = json.loads(args.config.read_text())
    zone_raw = raw["zones"]
    zones = ZoneConfig(
        carousel=[tuple(p) for p in zone_raw["carousel"]],
        forbidden_zones=tuple(
            tuple([tuple(p) for p in poly])
            for poly in zone_raw.get("forbidden_zones", ())
        ),
    )
    fg_config = ForegroundConfig(
        min_area_ratio=raw.get("min_area_ratio", 0.02),
        warmup_frames=args.warmup if args.warmup is not None
        else raw.get("warmup_frames", 50),
    )
    fps = raw.get("fps", 2.0)
    fg = ZoneForeground(zones, fg_config)

    frame_idx = 0
    occupied_rows = []          # (t, ratio, centroid) while occupied
    runs: list[tuple[float, float]] = []  # (start_t, end_t) occupied spans
    run_start = None
    ratios_seen = []
    for image in decode_frames(args.video, fps):
        bgr = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
        if bgr is None:
            continue
        frame_idx += 1
        t = frame_idx / fps
        ratio, centroid = fg.update(bgr)
        if centroid is not None and ratio > 0:
            ratios_seen.append(ratio)
        occupied = zone_occupied(ratio, fg_config) and centroid is not None
        if occupied:
            occupied_rows.append((t, ratio, centroid))
            if run_start is None:
                run_start = t
        elif run_start is not None:
            runs.append((run_start, t))
            run_start = None
    if run_start is not None:
        runs.append((run_start, frame_idx / fps))

    print(f"frames={frame_idx} ({frame_idx / fps:.0f}s @ {fps}fps)")
    if ratios_seen:
        arr = np.array(ratios_seen)
        print(f"ratio: max={arr.max():.4f} mean={arr.mean():.4f}")
    print(f"occupied frames: {len(occupied_rows)} "
          f"({len(occupied_rows) / max(frame_idx, 1):.1%})")
    if runs:
        longest = max(end - start for start, end in runs)
        print(f"occupied spans: {len(runs)}, longest={longest:.1f}s")
        for start, end in runs:
            print(f"  {start:7.1f}s -> {end:7.1f}s ({end - start:4.1f}s)")
    else:
        print("occupied spans: none")
    if occupied_rows:
        print(f"frames above report line ({args.report_above}):")
        for t, ratio, centroid in occupied_rows:
            if ratio >= args.report_above:
                print(f"  t={t:7.1f}s ratio={ratio:.4f} "
                      f"centroid=({centroid[0]:.0f},{centroid[1]:.0f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
