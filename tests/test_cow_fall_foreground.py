import unittest

import numpy as np

from monitor.cow_fall.foreground import (
    ForegroundConfig,
    ZoneForeground,
    zone_occupied,
)
from monitor.cow_fall.zones import ZoneConfig

# Carousel occupies the upper half; the forbidden zone is the lower half.
CAROUSEL = ((0.0, 0.0), (100.0, 0.0), (100.0, 60.0), (0.0, 60.0))
W, H = 100, 120


def zones() -> ZoneConfig:
    return ZoneConfig(carousel=CAROUSEL)


def blank() -> np.ndarray:
    return np.full((H, W, 3), 100, dtype=np.uint8)


class ZoneForegroundTests(unittest.TestCase):
    def test_static_scene_converges_to_quiet_zone(self):
        fg = ZoneForeground(zones(), ForegroundConfig())
        last = None
        for _ in range(60):
            last = fg.update(blank())
        # Same static frame: ratio must be effectively zero after warmup.
        self.assertLess(last[0], 0.01)

    def test_large_new_object_in_zone_occupies(self):
        fg = ZoneForeground(zones(), ForegroundConfig())
        for _ in range(60):
            fg.update(blank())
        frame = blank()
        frame[70:110, 30:70] = 20  # dark block on the forbidden ground
        ratio, centroid = fg.update(frame)
        self.assertGreaterEqual(ratio, ForegroundConfig().min_area_ratio)
        self.assertTrue(zone_occupied(ratio, ForegroundConfig()))
        # Centroid lands inside the injected block (y 70..110).
        self.assertGreaterEqual(centroid[1], 70)
        self.assertLessEqual(centroid[1], 110)

    def test_object_on_carousel_is_not_zone_occupation(self):
        fg = ZoneForeground(zones(), ForegroundConfig())
        for _ in range(60):
            fg.update(blank())
        frame = blank()
        frame[10:50, 30:70] = 20  # same block, but on the carousel
        ratio, _ = fg.update(frame)
        self.assertFalse(zone_occupied(ratio, ForegroundConfig()))

    def test_explicit_forbidden_zone_ignores_motion_elsewhere(self):
        # Calibrated ground strip in the lower quarter of the frame.
        forbidden = (((0.0, 90.0), (100.0, 90.0), (100.0, 120.0), (0.0, 120.0)),)
        config = ZoneConfig(carousel=CAROUSEL, forbidden_zones=forbidden)
        fg = ZoneForeground(config, ForegroundConfig())
        for _ in range(60):
            fg.update(blank())
        frame = blank()
        frame[10:50, 30:70] = 20  # motion on the carousel: ignored
        ratio, centroid = fg.update(frame)
        self.assertEqual((ratio, centroid), (0.0, None))
        frame = blank()
        frame[95:115, 30:70] = 20  # same-size block inside the strip
        ratio, centroid = fg.update(frame)
        # The block covers 800 of the strip's 3000 pixels.
        self.assertGreater(ratio, 0.2)
        self.assertIsNotNone(centroid)

    def test_config_rejects_bad_ratio(self):
        with self.assertRaises(ValueError):
            ForegroundConfig(min_area_ratio=0.0)


if __name__ == "__main__":
    unittest.main()
