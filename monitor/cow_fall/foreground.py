"""Forbidden-zone foreground detection (the primary fall trigger).

Cows on the carousel are mostly occluded (legs/rear only), so COCO cow
detection cannot be the trigger. The reliable physical fact is different:
the ground outside the carousel is normally EMPTY. Any large persistent
foreground appearing inside that zone is out-of-place — a fallen cow or
anything else that must be confirmed. This module measures that fact with
MOG2 background subtraction plus a calibrated polygon mask.
"""
from __future__ import annotations

from dataclasses import dataclass

from monitor.cow_fall.zones import ZoneConfig


@dataclass(frozen=True)
class ForegroundConfig:
    # Fraction of the zone area that must be foreground to count as occupied.
    min_area_ratio: float = 0.02
    # MOG2 history/parameters; defaults suit a mostly-static machine view.
    history: int = 300
    detect_shadows: bool = False
    var_threshold: float = 40.0
    # Frames skipped while the background model warms up (frame 0 is all
    # foreground by construction and would immediately false-trigger).
    warmup_frames: int = 50
    # A frame whose foreground covers more than this fraction of the WHOLE
    # image is a global change (lights on/off, camera gain), not a fall:
    # report quiet while MOG2 re-learns, instead of firing for the whole
    # convergence window (history=300 at 2 fps ~ 150 s).
    global_flash_ratio: float = 0.25

    def __post_init__(self) -> None:
        if not 0.0 < self.min_area_ratio < 1.0:
            raise ValueError("min_area_ratio must be in (0, 1)")


class ZoneForeground:
    """MOG2 restricted to the forbidden zone; stateless per frame beyond
    the background model itself."""

    def __init__(self, zones: ZoneConfig, config: ForegroundConfig) -> None:
        import cv2
        import numpy as np

        if not 0.0 < config.min_area_ratio < 1.0:
            raise ValueError("min_area_ratio must be in (0, 1)")
        self._cv2 = cv2
        self._np = np
        self.zones = zones
        self.config = config
        self._subtractor = cv2.createBackgroundSubtractorMOG2(
            history=config.history,
            varThreshold=config.var_threshold,
            detectShadows=config.detect_shadows,
        )
        self._zone_mask: np.ndarray | None = None
        self._zone_pixels = 0
        self._frame_size: tuple[int, int] | None = None
        self._frames_seen = 0

    def _ensure_mask(self, width: int, height: int) -> None:
        if self._zone_mask is not None and self._frame_size == (width, height):
            return
        np = self._np
        cv2 = self._cv2
        if self.zones.forbidden_zones:
            # Calibrated ground polygons: only what a fallen body can land
            # on is watched, which excludes the robot arm and cow bodies.
            mask = np.zeros((height, width), dtype=np.uint8)
            polygons = [np.array(zone, dtype=np.int32)
                        for zone in self.zones.forbidden_zones]
            cv2.fillPoly(mask, polygons, 255)
        else:
            # Fallback: everything OUTSIDE the carousel polygon.
            mask = np.full((height, width), 255, dtype=np.uint8)
            polygon = np.array(self.zones.carousel, dtype=np.int32)
            cv2.fillPoly(mask, [polygon], 0)
        self._zone_mask = mask
        self._zone_pixels = int(mask.sum() // 255)
        self._frame_size = (width, height)

    def update(self, frame_bgr) -> tuple[float, tuple[float, float] | None]:
        """Feed one frame; return (foreground ratio inside the zone, centroid).

        The centroid anchors spatial dedup in the tracker; it is None when
        the zone is quiet this frame (ratio 0 is a valid quiet observation).
        """
        cv2 = self._cv2
        np = self._np
        height, width = frame_bgr.shape[:2]
        self._ensure_mask(width, height)
        self._frames_seen += 1
        if self._frames_seen <= self.config.warmup_frames:
            self._subtractor.apply(frame_bgr)  # keep learning, stay quiet
            return 0.0, None
        full_mask = self._subtractor.apply(frame_bgr)
        flash = float((full_mask > 200).sum()) / (width * height)
        if flash > self.config.global_flash_ratio:
            # Global illumination change: keep feeding the model so it
            # re-learns, but stay quiet — a fallen cow never lights up the
            # entire frame.
            return 0.0, None
        zone_mask = cv2.bitwise_and(full_mask, full_mask, mask=self._zone_mask)
        binary = (zone_mask > 200).astype(np.uint8)
        foreground = int(binary.sum())
        if not self._zone_pixels:
            return 0.0, None
        ratio = foreground / self._zone_pixels
        if not foreground:
            return 0.0, None
        count, _, stats, centroids = cv2.connectedComponentsWithStats(binary)
        # Largest non-background component anchors the event location.
        largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        return ratio, (float(centroids[largest][0]), float(centroids[largest][1]))

    def reset(self) -> None:
        """Re-create the background model (e.g. after camera moves)."""
        cv2 = self._cv2
        self._subtractor = cv2.createBackgroundSubtractorMOG2(
            history=self.config.history,
            varThreshold=self.config.var_threshold,
            detectShadows=self.config.detect_shadows,
        )
        # Warmup must run again: frame 0 of a fresh model is all-foreground.
        self._frames_seen = 0


def zone_occupied(ratio: float | None, config: ForegroundConfig) -> bool:
    if ratio is None:
        return False
    return ratio >= config.min_area_ratio
