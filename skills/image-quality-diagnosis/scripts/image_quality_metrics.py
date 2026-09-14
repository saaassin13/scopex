#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


MAX_IMAGES = 16


def metrics(path: Path) -> dict:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"cannot decode image: {path}")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    gradient_energy = np.mean(gx * gx + gy * gy)

    values = gray.astype(np.float64)
    height, width = gray.shape
    return {
        "path": str(path),
        "width": int(width),
        "height": int(height),
        "laplacian_variance": float(lap.var()),
        "gradient_energy": float(gradient_energy),
        "brightness_mean": float(values.mean()),
        "contrast_stddev": float(values.std()),
        "dark_clip_ratio": float(np.mean(values <= 5.0)),
        "bright_clip_ratio": float(np.mean(values >= 250.0)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute bounded objective image-quality metrics for explicit image paths."
    )
    parser.add_argument("images", nargs="+", type=Path)
    args = parser.parse_args()

    if len(args.images) > MAX_IMAGES:
        parser.error(f"at most {MAX_IMAGES} explicit images are allowed per call")

    rows = []
    for raw in args.images:
        path = raw.expanduser()
        if not path.is_file():
            parser.error(f"image does not exist: {path}")
        rows.append(metrics(path))

    print(json.dumps({"images": rows}, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
