"""Interactive zone calibration from one reference frame.

Click the carousel polygon vertices, then the two platform-line endpoints
(left to right along the edge). Writes a zones JSON usable directly as the
session zone config. Run on a machine with a display, next to any frame
saved from the stream.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frame", type=Path, help="reference JPEG frame")
    parser.add_argument("--out", type=Path, required=True, help="zones JSON output")
    parser.add_argument("--scale", type=float, default=1.0,
                        help="coordinates are stored in full-resolution frame units")
    args = parser.parse_args(argv)

    import cv2
    import numpy as np

    image = cv2.imdecode(np.fromfile(args.frame, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        parser.error(f"cannot decode {args.frame}")
    height, width = image.shape[:2]

    points: list[tuple[int, int]] = []

    def on_mouse(event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN:
            points.append((x, y))
            draw = image.copy()
            for px, py in points:
                cv2.circle(draw, (px, py), 4, (0, 0, 255), -1)
            for a, b in zip(points, points[1:]):
                cv2.line(draw, a, b, (0, 255, 0), 2)
            if len(points) >= 3:
                cv2.line(draw, points[-1], points[0], (0, 200, 0), 1)
            cv2.imshow("calibrate", draw)

    cv2.imshow("calibrate", image)
    cv2.setMouseCallback("calibrate", on_mouse)
    print("1) Click carousel polygon vertices; press 'c' when done.")
    print("2) Then click the 2 platform-line endpoints (left to right); press 'q'.")
    while True:
        key = cv2.waitKey(50) & 0xFF
        if key == ord("c") and len(points) >= 3:
            break
        if key == ord("q"):
            cv2.destroyAllWindows()
            return 1
    carousel = list(points)
    points.clear()
    while True:
        key = cv2.waitKey(50) & 0xFF
        if key == ord("q") and len(points) == 2:
            break
    cv2.destroyAllWindows()

    scale = args.scale
    zones = {
        "schema": 1,
        "frame_size": [width, height],
        "carousel": [[x / scale, y / scale] for x, y in carousel],
        "platform_line": [[x / scale, y / scale] for x, y in points],
        "min_on_carousel_ratio": 0.5,
        "platform_line_margin_px": 8.0,
        "min_conf": 0.5,
    }
    args.out.write_text(json.dumps(zones, ensure_ascii=False, indent=1))
    print(f"wrote {args.out} ({len(carousel)} carousel vertices)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
