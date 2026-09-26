"""Static picture geometry for cow-fall detection.

All coordinates are picture pixels in the detection frame. Zones are
calibrated once from a reference frame; nothing here depends on the stream.
Pure standard library so the rule layer stays testable without cv2.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

Point = tuple[float, float]
Box = tuple[float, float, float, float]  # x1, y1, x2, y2 (x2 > x1, y2 > y1)
Polygon = Sequence[Point]


def point_in_polygon(point: Point, polygon: Polygon) -> bool:
    """Even-odd ray casting; robust for convex and concave outlines."""
    x, y = point
    inside = False
    count = len(polygon)
    j = count - 1
    for i in range(count):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        crosses = (yi > y) != (yj > y)
        if crosses:
            intersect_x = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < intersect_x:
                inside = not inside
        j = i
    return inside


def box_center(box: Box) -> Point:
    x1, y1, x2, y2 = box
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def box_bottom_center(box: Box) -> Point:
    x1, _y1, x2, y2 = box
    return ((x1 + x2) / 2.0, y2)


def signed_distance_below_line(point: Point, line: tuple[Point, Point]) -> float:
    """Perpendicular distance from the point to the line, positive "below".

    The calibration orients the platform line left-to-right along the carousel
    edge; positive means the camera-side "below" where a dropped body hangs.
    """
    (x1, y1), (x2, y2) = line
    dx, dy = x2 - x1, y2 - y1
    length = (dx * dx + dy * dy) ** 0.5
    if length == 0:
        raise ValueError("platform line endpoints must differ")
    cross = dx * (point[1] - y1) - dy * (point[0] - x1)
    return cross / length


def point_below_line(point: Point, line: tuple[Point, Point]) -> bool:
    return signed_distance_below_line(point, line) > 0


def box_overlap_ratio(box: Box, polygon: Polygon, samples: int = 24) -> float:
    """Fraction of the box area that lies inside the polygon.

    Sampled on a uniform grid; 24x24 gives ~0.2% resolution which is far
    below any threshold we would calibrate, at zero geometry dependencies.
    """
    x1, y1, x2, y2 = box
    if x2 <= x1 or y2 <= y1:
        return 0.0
    step_x = (x2 - x1) / (samples + 1)
    step_y = (y2 - y1) / (samples + 1)
    hits = 0
    total = samples * samples
    for row in range(1, samples + 1):
        y = y1 + step_y * row
        for col in range(1, samples + 1):
            x = x1 + step_x * col
            if point_in_polygon((x, y), polygon):
                hits += 1
    return hits / total


@dataclass(frozen=True)
class ZoneConfig:
    """Calibrated picture geometry for one camera."""

    carousel: Polygon
    # Explicit forbidden ground zones (where a fallen body lands). Empty
    # means "everything outside the carousel", which is only correct for
    # views with no other moving structure; real cameras need calibrated
    # polygons that exclude the robot arm and cow bodies above the deck.
    forbidden_zones: tuple[Polygon, ...] = ()
    # Platform edge line, oriented so "below" is where a fallen body hangs.
    platform_line: tuple[Point, Point] | None = None
    # R2 fires when less than this fraction of the box stays on the carousel.
    min_on_carousel_ratio: float = 0.5
    # A bottom edge must sink this many pixels past the line to count, which
    # tolerates detection jitter around the platform edge.
    platform_line_margin_px: float = 8.0
    # Detector confidence floor; calibrated with the picture.
    min_conf: float = 0.5

    def __post_init__(self) -> None:
        if len(self.carousel) < 3:
            raise ValueError("carousel polygon needs at least 3 vertices")
        for zone in self.forbidden_zones:
            if len(zone) < 3:
                raise ValueError(
                    "forbidden zone polygons need at least 3 vertices")
        if not 0.0 < self.min_on_carousel_ratio <= 1.0:
            raise ValueError("min_on_carousel_ratio must be in (0, 1]")
        if self.platform_line is not None and self.platform_line[0] == self.platform_line[1]:
            raise ValueError("platform line endpoints must differ")
