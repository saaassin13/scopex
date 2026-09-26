import unittest

from monitor.cow_fall.rules import Detection, evaluate_frame
from monitor.cow_fall.tracker import FallTracker
from monitor.cow_fall.zones import (
    ZoneConfig,
    box_center,
    box_overlap_ratio,
    point_in_polygon,
    signed_distance_below_line,
)

# Picture layout used across the tests: carousel occupies the upper band as a
# rectangle; the ground below y=300 is where a fallen cow must never be.
CAROUSEL = ((0.0, 0.0), (1000.0, 0.0), (1000.0, 300.0), (0.0, 300.0))
PLATFORM_LINE = ((0.0, 300.0), (1000.0, 300.0))


def zones(**overrides) -> ZoneConfig:
    values = dict(
        carousel=CAROUSEL,
        platform_line=PLATFORM_LINE,
        min_on_carousel_ratio=0.5,
        min_conf=0.5,
    )
    values.update(overrides)
    return ZoneConfig(**values)


class ZoneGeometryTests(unittest.TestCase):
    def test_point_in_polygon_basic(self):
        self.assertTrue(point_in_polygon((500, 150), CAROUSEL))
        self.assertFalse(point_in_polygon((500, 400), CAROUSEL))

    def test_box_overlap_ratio_full_and_partial(self):
        fully_inside = (100, 100, 200, 200)
        self.assertGreater(box_overlap_ratio(fully_inside, CAROUSEL), 0.99)
        # Half of the box dangles past the platform line.
        half_out = (100, 200, 200, 400)
        ratio = box_overlap_ratio(half_out, CAROUSEL)
        self.assertAlmostEqual(ratio, 0.5, delta=0.02)

    def test_signed_distance_below_line(self):
        self.assertGreater(signed_distance_below_line((500, 360), PLATFORM_LINE), 0)
        self.assertLess(signed_distance_below_line((500, 240), PLATFORM_LINE), 0)
        self.assertAlmostEqual(
            signed_distance_below_line((500, 300), PLATFORM_LINE), 0.0, places=6)

    def test_config_rejects_degenerate_input(self):
        with self.assertRaises(ValueError):
            ZoneConfig(carousel=((0, 0), (1, 1)))
        with self.assertRaises(ValueError):
            ZoneConfig(carousel=CAROUSEL, min_on_carousel_ratio=0.0)


class RuleEvaluationTests(unittest.TestCase):
    def test_normal_cow_inside_band_is_not_anomaly(self):
        # Rear half of a standing cow, safely inside the carousel band.
        normal = Detection(box=(400, 80, 560, 290), conf=0.9)
        self.assertEqual(evaluate_frame([normal], zones()), [])

    def test_fallen_cow_on_ground_fires_position_rule(self):
        fallen = Detection(box=(300, 320, 700, 460), conf=0.85)
        result = evaluate_frame([fallen], zones())
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].fully_outside)
        self.assertTrue(result[0].mostly_off)
        self.assertIn("R1_box_center_outside", result[0].rule_ids)

    def test_hanging_cow_fires_platform_rule(self):
        # Center still inside the band but the rear dangles far below the
        # platform line: an oriented-line hit with margin.
        hanging = Detection(box=(400, 150, 560, 380), conf=0.8)
        result = evaluate_frame([hanging], zones())
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].below_platform)
        self.assertFalse(result[0].fully_outside)

    def test_low_confidence_and_other_labels_are_ignored(self):
        rows = [
            Detection(box=(300, 320, 700, 460), conf=0.3),
            Detection(box=(300, 320, 700, 460), conf=0.9, label="person"),
        ]
        self.assertEqual(evaluate_frame(rows, zones()), [])


class FallTrackerTests(unittest.TestCase):
    def _obs(self, x=500.0, y=380.0):
        from monitor.cow_fall.rules import FallObservation
        return FallObservation(
            box=(x - 100, y - 60, x + 100, y + 60),
            center=(x, y),
            fully_outside=True,
            mostly_off=True,
            below_platform=False,
        )

    def test_sustained_anomaly_fires_once_not_per_frame(self):
        tracker = FallTracker(trigger_sustain_s=4.0, clear_sustain_s=10.0)
        fired = []
        for t in range(0, 30):
            fired.extend(tracker.update(float(t), [self._obs()]))
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0].event_id, "fall-1")
        # Still open and merging: no second event for the same spot.
        self.assertEqual(len(tracker.open_events), 1)
        self.assertGreater(tracker.open_events["fall-1"].merged_updates, 5)

    def test_momentary_observation_never_fires(self):
        tracker = FallTracker(trigger_sustain_s=4.0)
        self.assertEqual(tracker.update(0.0, [self._obs()]), [])
        self.assertEqual(tracker.update(1.0, []), [])
        for t in range(2, 20):
            self.assertEqual(tracker.update(float(t), []), [])
        self.assertEqual(tracker.open_events, {})

    def test_event_closes_after_anomaly_clears_and_can_refire(self):
        tracker = FallTracker(trigger_sustain_s=2.0, clear_sustain_s=5.0)
        for t in range(0, 5):
            tracker.update(float(t), [self._obs()])
        self.assertEqual(len(tracker.open_events), 1)
        # Anomaly disappears: event stays open during the clear window...
        for t in range(5, 9):
            tracker.update(float(t), [])
        self.assertEqual(len(tracker.open_events), 1)
        # ...then closes.
        tracker.update(11.0, [])
        self.assertEqual(tracker.open_events, {})
        # A later anomaly at the same spot is a new event.
        fired = []
        for t in range(20, 30):
            fired.extend(tracker.update(float(t), [self._obs()]))
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0].event_id, "fall-2")

    def test_spatially_separate_anomaly_opens_second_event(self):
        tracker = FallTracker(trigger_sustain_s=2.0, clear_sustain_s=10.0,
                              merge_distance_px=300.0)
        for t in range(0, 5):
            tracker.update(float(t), [self._obs(x=200)])
        self.assertEqual(len(tracker.open_events), 1)
        # A different cow falls far away while the first is still open.
        fired = []
        for t in range(5, 15):
            fired.extend(tracker.update(float(t), [self._obs(x=200), self._obs(x=900)]))
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0].event_id, "fall-2")
        self.assertEqual(len(tracker.open_events), 2)

    def test_nearby_observation_merges_into_open_event(self):
        tracker = FallTracker(trigger_sustain_s=2.0, merge_distance_px=300.0)
        for t in range(0, 5):
            tracker.update(float(t), [self._obs(x=500)])
        # Slight drift of the same lying cow must not open a second event.
        for t in range(5, 15):
            tracker.update(float(t), [self._obs(x=560)])
        self.assertEqual(len(tracker.open_events), 1)


if __name__ == "__main__":
    unittest.main()
