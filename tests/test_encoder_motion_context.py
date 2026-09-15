"""Operating-pattern counterexamples; these are not physical fault labels."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/encoder-health/scripts/encoder_health.py'
ANALYZER = runpy.run_path(str(SCRIPT))


def series(increments):
    t, count = datetime(2026, 9, 15), 1000
    rows = []
    for dt, delta in [(0, 0)] + increments:
        t += timedelta(milliseconds=dt)
        count += delta
        rows.append({'ts': t, 'ts_text': t.strftime('%Y-%m-%d %H:%M:%S:%f'),
                     'count': count, 'source': 'synthetic.log', 'line_no': len(rows) + 1})
    return rows


def detect(increments):
    return ANALYZER['detect_count_events'](series(increments), flat_ms=1000,
                                         gap_factor=5, gap_min_ms=500)


class EncoderMotionTests(unittest.TestCase):
    def test_constant_velocity_is_not_a_spike_when_sampling_interval_changes(self):
        facts, events, _, _ = detect([(20, 20)] * 25 + [(80, 80)] + [(20, 20)] * 25)
        self.assertEqual(facts['positive_spike_candidate_count'], 0)
        self.assertEqual(events, [])

    def test_actual_rate_jump_is_still_visible(self):
        facts, events, _, _ = detect([(20, 20)] * 25 + [(20, 200)] + [(20, 20)] * 25)
        self.assertEqual(facts['positive_spike_candidate_count'], 1)
        event = events[0]
        self.assertEqual(event['rate_counts_s'], 10000)
        self.assertEqual(event['motion_before']['rate_counts_s_median'], 1000)

    def test_stop_rebound_keeps_stationary_context_instead_of_claiming_fault(self):
        _, events, _, _ = detect([(20, 20)] * 25 + [(20, 10), (20, 4), (20, 0), (20, -8)] + [(20, 0)] * 100)
        reverse = next(e for e in events if e['type'].startswith('reverse'))
        self.assertEqual(reverse['motion_after']['stationary_fraction'], 1)
        self.assertEqual(reverse['motion_after']['rate_counts_s_median'], 0)
        self.assertNotIn('anomaly_confirmed', reverse)
        selected = ANALYZER['select_events'](events, 6)
        self.assertFalse(any(e['type'] == 'flat_count_candidate' for e in selected))

    def test_recovery_checks_elapsed_time_instead_of_three_samples(self):
        # Recovery occurs 400ms later at either cadence, with >3 fine samples.
        for dt in (20, 100):
            with self.subTest(dt=dt):
                _, events, _, _ = detect([(20, 20)] * 25 + [(20, -100)] +
                                         [(dt, 0)] * (400 // dt - 1) + [(dt, 100)])
                event = next(e for e in events if e['type'].startswith('reverse'))
                self.assertTrue(event['recovered'])
                self.assertEqual(event['recovery_observed_ms'], 400)
                self.assertEqual(event['recovery_pulses'], 100)

    def test_recovery_does_not_hide_an_independent_later_spike(self):
        facts, events, _, _ = detect([(20, 20)] * 25 + [(20, -50), (20, 50), (20, 20), (20, 300)] + [(20, 20)] * 25)
        self.assertEqual(facts['positive_spike_candidate_count'], 1)
        self.assertEqual(next(e for e in events if e['type'] == 'positive_spike_candidate')['pulse_delta'], 300)

    def test_stops_cannot_displace_extreme_candidates(self):
        events = [{'type': 'flat_count_candidate', 'duration_ms': 2000} for _ in range(12)]
        largest = {'type': 'reverse_interval_candidate', 'abs_pulse_drop': 1863, 'candidate_threshold_pulses': 294}
        events += [largest, {'type': 'reverse_interval_candidate', 'abs_pulse_drop': 100, 'candidate_threshold_pulses': 1}]
        selected = ANALYZER['select_events'](events, 1)
        self.assertEqual(selected, [largest])

    def test_saved_event_query_filters_window_without_logs(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'events.json'
            path.write_text(json.dumps({'events': [
                {'type': 'positive_spike_candidate', 'at': '2026-09-15 13:00:00:000', 'pulse_delta': 500},
                {'type': 'positive_spike_candidate', 'at': '2026-09-15 14:00:00:000', 'pulse_delta': 900},
            ]}))
            proc = subprocess.run([sys.executable, str(SCRIPT), '--inspect-events', str(path),
                                   '--start', '2026-09-15 13:00:00:000', '--end', '2026-09-15 14:00:00:000'],
                                  capture_output=True, text=True, timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(proc.stdout)
            self.assertEqual(result['top_candidates'][0]['pulse_delta'], 500)
            self.assertEqual(result['candidate_events_total'], 1)

