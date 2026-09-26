import unittest

from monitor.cow_fall.rules import FallObservation
from monitor.cow_fall.tracker import FallTracker


def obs(x: float, y: float) -> FallObservation:
    return FallObservation(
        box=(x - 20, y - 20, x + 20, y + 20), center=(x, y),
        fully_outside=True, mostly_off=False, below_platform=False)


class FallTrackerRegressionTests(unittest.TestCase):
    def test_two_spots_relaying_never_satisfy_sustain(self):
        """Regression: spatially unrelated dwellings must not add up.

        The robot arm parks <=16s per spot; two different spots taking
        turns with no quiet frame in between used to accumulate one
        shared sustain clock and fire with wrong attribution. Here A
        dwells 15s and B 29s — neither reaches the 30s threshold alone.
        """
        tracker = FallTracker(trigger_sustain_s=30.0, clear_sustain_s=10.0)
        fired = []
        for t in range(0, 45):  # one update per second
            at = float(t)
            center = (100, 100) if t < 15 else (1500, 900)
            fired += tracker.update(at, [obs(*center)])
        self.assertEqual(fired, [])

    def test_pending_clears_when_merged_into_open_event(self):
        """Regression: merging into an open event must reset the pending
        clock, so a fresh anomaly after close starts sustain from zero."""
        tracker = FallTracker(trigger_sustain_s=30.0, clear_sustain_s=10.0)
        fired = []
        for t in range(0, 31):  # spans 30s at one spot: fires at t=30
            fired += tracker.update(float(t), [obs(100, 100)])
        self.assertEqual(len(fired), 1)
        # Keep feeding the same spot: merges into the open event.
        for t in range(31, 41):
            fired += tracker.update(float(t), [obs(100, 100)])
        self.assertEqual(len(fired), 1)
        # Anomaly disappears long enough to close the event.
        for t in range(41, 72):
            tracker.update(float(t), [])
        self.assertEqual(tracker.open_events, {})
        # New anomaly at the same spot: must NOT fire on the first frame
        # even though a stale pending clock would read 70+ seconds old.
        fired = tracker.update(72.0, [obs(105, 105)])
        self.assertEqual(fired, [])

    def test_single_spot_sustains_and_fires(self):
        tracker = FallTracker(trigger_sustain_s=30.0, clear_sustain_s=10.0)
        fired = []
        for t in range(31):
            fired += tracker.update(float(t), [obs(100, 100)])
        self.assertEqual(len(fired), 1)
        self.assertAlmostEqual(fired[0].opened_at, 0.0)
        self.assertEqual(fired[0].center, (100, 100))


if __name__ == "__main__":
    unittest.main()
