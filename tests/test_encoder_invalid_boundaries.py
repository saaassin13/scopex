from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/encoder-health/scripts/encoder_health.py'
INVALID = 2 ** 63


def samples(values: list[int], times_ms: list[int] | None = None) -> list[dict]:
    start = datetime(2026, 9, 14, 7)
    times = times_ms if times_ms is not None else [i * 20 for i in range(len(values))]
    return [
        {
            'ts': start + timedelta(milliseconds=ms),
            'ts_text': (start + timedelta(milliseconds=ms)).strftime('%Y-%m-%d %H:%M:%S:%f'),
            'count': count,
            'invalid': count >= INVALID,
            'source': 'app.log',
            'line_no': index + 1,
        }
        for index, (ms, count) in enumerate(zip(times, values))
    ]


class EncoderInvalidBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analyzer = runpy.run_path(str(SCRIPT))

    def detect(self, rows: list[dict], *, flat_ms: float = 30):
        return self.analyzer['detect_count_events'](
            rows, flat_ms=flat_ms, gap_factor=5.0, gap_min_ms=100.0,
        )

    def run_cli(self, values: list[int]):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / 'app.log'
            log.write_text(''.join(
                f"{row['ts'].strftime('%Y-%m-%d %H:%M:%S:')}{row['ts'].microsecond // 1000:03d} "
                f"[INFO] Get EncoderVal, raw[{row['count']}], filtered[{row['count']}]\n"
                for row in samples(values)
            ), encoding='utf-8')
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), str(log)], cwd=ROOT,
                capture_output=True, text=True, timeout=20,
            )
            return proc, json.loads(proc.stdout)

    def test_cli_preserves_invalid_boundary_for_both_jump_directions(self):
        for values in ([100, 110, INVALID, 90, 85], [100, 110, INVALID, 10000, 10010]):
            with self.subTest(values=values):
                proc, report = self.run_cli(values)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                facts = report['facts']
                self.assertEqual(facts['invalid_samples'], 1)
                self.assertEqual(facts['negative_steps_observed'], facts['raw_negative_steps'])
                self.assertEqual(facts['significant_reverse_event_count'], 0)
                self.assertEqual(facts['positive_spike_candidate_count'], 0)
                self.assertEqual(report['top_candidates'], [])

    def test_invalid_filtered_value_is_also_a_continuity_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / 'app.log'
            log.write_text(
                '2026-09-14 07:00:00:000 [INFO] Get EncoderVal, raw[100], filtered[100]\n'
                f'2026-09-14 07:00:00:020 [INFO] Get EncoderVal, raw[110], filtered[{INVALID}]\n'
                '2026-09-14 07:00:00:040 [INFO] Get EncoderVal, raw[120], filtered[120]\n',
                encoding='utf-8',
            )
            proc = subprocess.run([sys.executable, str(SCRIPT), str(log)], cwd=ROOT,
                                  capture_output=True, text=True, timeout=20)
            report = json.loads(proc.stdout)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(report['facts']['invalid_samples'], 1)
            self.assertEqual(report['facts']['raw_filtered_abs_diff_max'], 0)
            self.assertEqual(report['top_candidates'], [])

    def test_recovery_cannot_cross_an_invalid_sample(self):
        rows = samples([100, 110, 120, 130, 80, INVALID, 80, 100, 120, 140])
        facts, events, _, _ = self.detect(rows)
        self.assertEqual(facts['reverse_glitch_candidate_count'], 0)
        event = next(row for row in events if row['type'] == 'reverse_step_candidate')
        self.assertEqual(event['pulse_delta'], -50)
        self.assertFalse(event['recovered'])
        self.assertEqual(event['recovery_pulses'], 0)

    def test_short_flat_intervals_do_not_merge_across_invalid_sample(self):
        facts, events, _, _ = self.detect(samples([100, 100, INVALID, 100, 100]))
        self.assertEqual(facts['flat_count_candidate_count'], 0)
        self.assertFalse(any(row['type'] == 'flat_count_candidate' for row in events))

    def test_flat_detection_restarts_after_invalid_sample(self):
        facts, events, _, _ = self.detect(samples([100, 100, 100, INVALID, 100, 100, 100]))
        flats = [row for row in events if row['type'] == 'flat_count_candidate']
        self.assertEqual(facts['flat_count_candidate_count'], 2)
        self.assertEqual([row['duration_ms'] for row in flats], [40.0, 40.0])
        self.assertEqual([(row['line_start'], row['line_end']) for row in flats], [(1, 3), (5, 7)])

    def test_reverse_groups_do_not_merge_across_invalid_sample(self):
        facts, events, _, _ = self.detect(samples([500, 520, 470, INVALID, 600, 550]))
        self.assertEqual(facts['negative_steps_observed'], 2)
        self.assertEqual(facts['reverse_interval_candidate_count'], 0)
        self.assertEqual(facts['reverse_step_candidate_count'], 2)
        self.assertEqual([row['pulse_delta'] for row in events], [-50, -50])

    def test_flat_intervals_do_not_merge_across_nonpositive_time(self):
        for times in ([0, 20, 20, 40], [0, 20, 10, 30]):
            with self.subTest(times=times):
                facts, _, _, _ = self.detect(samples([100, 100, 100, 100], times))
                self.assertEqual(facts['flat_count_candidate_count'], 0)

    def test_cli_all_invalid_is_not_success(self):
        proc, report = self.run_cli([INVALID, INVALID])
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(report['facts']['invalid_samples'], 2)
        self.assertIsNone(report['facts']['first_ts'])
        self.assertIsNone(report['facts']['last_ts'])
        self.assertEqual(report['top_candidates'], [])

    def test_cli_invalid_edges_do_not_change_valid_coverage(self):
        proc, report = self.run_cli([INVALID, 100, 110, INVALID])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        facts = report['facts']
        self.assertEqual(facts['samples_in_window'], 2)
        self.assertEqual(facts['raw_filtered_samples'], 4)
        self.assertEqual(facts['invalid_samples'], 2)
        self.assertEqual(facts['first_ts'], '2026-09-14 07:00:00:020')
        self.assertEqual(facts['last_ts'], '2026-09-14 07:00:00:040')


if __name__ == '__main__':
    unittest.main()
