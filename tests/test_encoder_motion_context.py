"""Raw movement counterexamples: shape candidates are not mechanical fault labels."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/encoder-health/scripts/encoder_health.py"
ANALYZER = runpy.run_path(str(SCRIPT))


def series(increments, initial=1000):
    now, count, rows = datetime(2026, 9, 15), initial, []
    for dt, delta in [(0, 0)] + increments:
        now += timedelta(milliseconds=dt)
        count += delta
        rows.append((now.strftime("%Y-%m-%d %H:%M:%S:%f")[:-3], count))
    return rows


def raw_text(rows):
    return "".join(f"{t} [TRACE] Get EncoderVal, raw[{count}], filtered[0]\n" for t, count in rows)


def analyze(increments, profile=None, initial=1000):
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "raw.log"
        log.write_text(raw_text(series(increments, initial)), encoding="utf-8")
        return ANALYZER["scan"]([log], profile=profile)


def cli(*args):
    proc = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, timeout=30)
    return proc, json.loads(proc.stdout)


class RawMotionTests(unittest.TestCase):
    def test_constant_rate_cadence_change_is_not_a_spike(self):
        s = analyze([(20, 20)] * 30 + [(100, 100)] * 30 + [(20, 20)] * 30)
        self.assertEqual(s["candidates"], [])
        self.assertEqual(s["facts"]["rate_counts_s"]["min"], 1000)
        self.assertEqual(s["facts"]["rate_counts_s"]["max"], 1000)

    def test_forward_and_reverse_are_symmetric(self):
        increments = [(20, 20)] * 30 + [(20, 200), (20, -160)] + [(20, 20)] * 30
        a, b = analyze(increments), analyze([(dt, -dc) for dt, dc in increments])
        self.assertEqual([c["kinds"] for c in a["candidates"]], [c["kinds"] for c in b["candidates"]])
        self.assertTrue(a["candidates"])
        for s in (a, b):
            r = ANALYZER["inspect"](s, s["candidates"][0]["id"])
            self.assertEqual(r["status"], "unresolved")
            self.assertFalse(r["stop_recommended"])

    def test_sustained_reverse_is_allowed_even_through_zero(self):
        s = analyze([(100, -60)] * 120 + [(100, 0)] * 30 + [(100, 50)] * 30)
        for c in s["candidates"]:
            self.assertFalse(ANALYZER["inspect"](s, c["id"])["stop_recommended"])
        self.assertLess(s["samples"][40]["count"], 0)

    def test_stops_and_rebound_are_not_confirmed_faults(self):
        s = analyze([(100, 10)] * 30 + [(100, 5), (100, 2), (100, -8),
                    (100, 4), (100, -3), (100, 1), (100, -1)] + [(100, 0)] * 30)
        for c in s["candidates"]:
            r = ANALYZER["inspect"](s, c["id"])
            self.assertFalse(r["stop_recommended"])
        self.assertNotEqual(ANALYZER["report"](s)["status"], "confirmed_raw_anomaly")

    def test_stationary_one_count_jitter_does_not_seed_candidates(self):
        s = analyze([(100, 0)] * 30 + [(100, -1), (100, 1)] * 100 + [(100, 0)] * 30)
        self.assertEqual(s["candidates"], [])

    def test_isolated_positive_and_negative_departures_are_visible(self):
        for delta in (1000, -1000):
            s = analyze([(100, 10)] * 30 + [(100, delta), (100, 20 - delta)] + [(100, 10)] * 30)
            self.assertTrue(any("local_rate_deviation" in c["kinds"] for c in s["candidates"]))

    def test_near_zero_is_not_silently_removed_as_reset(self):
        s = analyze([(100, 10)] * 30 + [(100, -1300), (100, 1320)] + [(100, 10)] * 30)
        self.assertTrue(any("local_rate_deviation" in c["kinds"] for c in s["candidates"]))
        self.assertEqual(s["facts"]["delta_counts"]["min"], -1300)

    def test_persistent_rate_change_is_an_observation(self):
        s = analyze([(100, 10)] * 30 + [(100, 100)] * 30)
        self.assertTrue(any("rate_transition" in c["kinds"] for c in s["candidates"]))
        for c in s["candidates"]:
            self.assertFalse(ANALYZER["inspect"](s, c["id"])["stop_recommended"])

    def test_later_independent_spike_remains_visible(self):
        s = analyze([(100, 10)] * 30 + [(100, -100), (100, 120)]
                    + [(100, 10)] * 30 + [(100, 500)] + [(100, 10)] * 30)
        self.assertGreaterEqual(len(s["candidates"]), 2)

    def test_multi_sample_offset_is_not_limited_to_three_points(self):
        s = analyze([(100, 10)] * 30 + [(100, 1000)] + [(100, 10)] * 5
                    + [(100, -980)] + [(100, 10)] * 30)
        self.assertTrue(any("local_rate_deviation" in c["kinds"] for c in s["candidates"]))

    def test_zero_point_shift_preserves_candidates_without_profile(self):
        steps = [(100, 10)] * 30 + [(100, -300), (100, 320)] + [(100, 10)] * 30
        a, b = analyze(steps, initial=1000), analyze(steps, initial=2**62)
        self.assertEqual(a["candidates"], b["candidates"])
        self.assertEqual(a["facts"]["delta_counts"], b["facts"]["delta_counts"])

    def test_equivalent_file_order_and_rotation_preserve_counts(self):
        with tempfile.TemporaryDirectory() as td:
            paths = [Path(td) / name for name in ("a.log", "b.log")]
            rows = series([(100, 10)] * 30)
            paths[0].write_text(raw_text(rows[:15]))
            paths[1].write_text(raw_text(rows[15:]))
            a, b = ANALYZER["scan"](paths), ANALYZER["scan"](paths[::-1])
            self.assertEqual(a["analysis_id"], b["analysis_id"])
            self.assertEqual(a["facts"], b["facts"])
            self.assertEqual(a["facts"]["samples_in_window"], 31)
