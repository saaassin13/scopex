"""Per-frame rule evaluation: turn YOLO detections into fall observations.

The detector only answers "where are cows". This module answers "is any cow
somewhere it can never be": fully outside the carousel polygon, mostly off
it, or hanging below the platform line. Sustained-anomaly decisions live in
the tracker, not here.
"""
from __future__ import annotations

from dataclasses import dataclass

from monitor.cow_fall.zones import (
    Box,
    Point,
    ZoneConfig,
    box_bottom_center,
    box_center,
    box_overlap_ratio,
    point_in_polygon,
    signed_distance_below_line,
)


@dataclass(frozen=True)
class Detection:
    """One detector output row for a single frame."""

    box: Box
    conf: float
    label: str = "cow"


@dataclass(frozen=True)
class FallObservation:
    """Rule outcome for one anomalous detection in one frame."""

    box: Box
    center: Point
    fully_outside: bool
    mostly_off: bool
    below_platform: bool

    @property
    def rule_ids(self) -> tuple[str, ...]:
        fired = []
        if self.fully_outside:
            fired.append("R1_box_center_outside")
        if self.mostly_off:
            fired.append("R2_mostly_off_carousel")
        if self.below_platform:
            fired.append("R2_below_platform_line")
        return tuple(fired)


def evaluate_frame(detections: list[Detection], zones: ZoneConfig) -> list[FallObservation]:
    """Return the anomalous subset of detections with the rules each fired.

    A normal cow is visible only as its rear half inside the carousel band,
    so any cow mostly or fully off the carousel, or hanging under the
    platform line, is out-of-place regardless of pose.
    """
    anomalies: list[FallObservation] = []
    for det in detections:
        if det.label != "cow" or det.conf < zones.min_conf:
            continue
        center = box_center(det.box)
        on_ratio = box_overlap_ratio(det.box, zones.carousel)
        mostly_off = on_ratio < zones.min_on_carousel_ratio
        below_platform = False
        if zones.platform_line is not None:
            bottom = box_bottom_center(det.box)
            drop = signed_distance_below_line(bottom, zones.platform_line)
            below_platform = drop > zones.platform_line_margin_px
        fully_outside = not point_in_polygon(center, zones.carousel)
        if fully_outside or mostly_off or below_platform:
            anomalies.append(FallObservation(
                box=det.box,
                center=center,
                fully_outside=fully_outside,
                mostly_off=mostly_off,
                below_platform=below_platform,
            ))
    return anomalies
