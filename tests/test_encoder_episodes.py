"""Unknown-input motion counterexamples, independent of field event timestamps."""
from test_encoder_motion_context import ANALYZER, series, SCRIPT
from datetime import datetime, timedelta
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


def episodes(increments):
    return ANALYZER['motion_episodes'](series(increments), 500)


class EpisodeTests(unittest.TestCase):
    def test_small_counter_reset_then_accumulation_is_not_reverse_or_glitch(self):
        rows = series([(100, 10)] * 20 + [(100, -1200)] + [(100, 100)] * 30)
        boundary = ANALYZER['counter_continuity_boundaries'](rows)[0]
        self.assertEqual(boundary['followup']['pattern'], 'restart_or_stop_compatible')
        result = ANALYZER['motion_episodes'](rows, 500)
        self.assertFalse(any(e['drawdown_counts'] >= 1200 or e['off_trend_return_counts'] for e in result))

    def test_small_counter_single_zero_dip_still_detected(self):
        rows = series([(100, 10)] * 20 + [(100, -1200), (100, 1220)] + [(100, 10)] * 30)
        self.assertTrue(any(e['off_trend_return_counts'] >= 1200
                            for e in ANALYZER['motion_episodes'](rows, 500)))

    def test_long_reverse_motion_is_not_scored_as_abnormal_drawdown(self):
        rows = series([(100, 100)] * 200 + [(100, -60)] * 120 + [(100, 0)] * 30 + [(100, 50)] * 30)
        result = ANALYZER['motion_episodes'](rows, 500)
        reverse = max(result, key=lambda e: e['drawdown_counts'])
        self.assertEqual(reverse['drawdown_counts'], 7200)
        self.assertEqual(reverse['off_trend_return_counts'], 0)
        self.assertNotIn('drawdown_counts', reverse['comparison']['features'])

    def test_gap_at_counter_boundary_remains_a_sampling_gap(self):
        rows = series([(100, 1000)] * 20 + [(10000, -20979)])
        facts, events, _, _ = ANALYZER['detect_count_events'](
            rows, flat_ms=1000, gap_factor=5, gap_min_ms=500)
        self.assertEqual(facts['sampling_gap_count'], 1)
        self.assertTrue(any(e['type'] == 'sampling_gap' and e['dt_ms'] == 10000 for e in events))

    def test_near_zero_single_point_preserves_off_trend_detection(self):
        rows = series([(100, 100)] * 200 + [(100, -20980), (100, 21180)] + [(100, 100)] * 20)
        boundary = ANALYZER['counter_continuity_boundaries'](rows)[0]
        self.assertEqual(boundary['followup']['pattern'], 'return_toward_previous_level')
        self.assertTrue(any(e['off_trend_return_counts'] > 20000
                            for e in ANALYZER['motion_episodes'](rows, 500)))

    def test_counter_followup_does_not_cross_gap_or_invalid_sample(self):
        for invalid, dt in ((True, 100), (False, 10000)):
            rows = series([(100, 1000)] * 20 + [(100, -20979), (dt, 21000)])
            rows[-1]['invalid'] = invalid
            boundary = ANALYZER['counter_continuity_boundaries'](rows)[0]
            self.assertEqual(boundary['followup']['pattern'], 'unresolved')
            self.assertEqual(boundary['followup']['following_samples'], 0)

    def test_near_zero_counter_restart_is_a_boundary_not_reverse_motion(self):
        rows = series([(100, 1000)] * 20)
        previous = rows[-1]
        reset = dict(previous, ts=previous['ts'] + timedelta(milliseconds=100),
                     ts_text=(previous['ts'] + timedelta(milliseconds=100)).strftime('%Y-%m-%d %H:%M:%S:%f'),
                     count=21, line_no=previous['line_no'] + 1)
        rows.append(reset)
        rows.extend(series([(100, 10)] * 20)[1:])
        # Keep the appended portion chronologically and near the new counter value.
        for index, row in enumerate(rows[22:], 1):
            row['ts'] = reset['ts'] + timedelta(milliseconds=100 * index)
            row['ts_text'] = row['ts'].strftime('%Y-%m-%d %H:%M:%S:%f')
            row['count'] = 21 + 10 * index
        boundaries = ANALYZER['counter_continuity_boundaries'](rows)
        self.assertEqual(len(boundaries), 1)
        self.assertEqual(boundaries[0]['count_after'], 21)
        self.assertFalse(any(e['drawdown_counts'] >= 10_000 for e in ANALYZER['motion_episodes'](rows, 500)))
        _, candidates, _, _ = ANALYZER['detect_count_events'](
            rows, flat_ms=1000, gap_factor=5, gap_min_ms=500)
        self.assertFalse(any(e.get('abs_pulse_drop', 0) >= 10_000 for e in candidates))

    def test_large_reverse_that_does_not_return_near_zero_remains_motion(self):
        rows = series([(100, 1000)] * 20 + [(100, -5000)] + [(100, 100)] * 20)
        self.assertEqual(ANALYZER['counter_continuity_boundaries'](rows), [])
        self.assertTrue(any(e['drawdown_counts'] == 5000 for e in ANALYZER['motion_episodes'](rows, 500)))

    def test_variable_cadence_constant_rate_has_no_episode(self):
        self.assertEqual(episodes([(20, 20)] * 100 + [(100, 100)] * 30 + [(20, 20)] * 100), [])

    def test_positive_off_trend_point_returns_to_track(self):
        result = episodes([(100, 10)] * 30 + [(100, 1000), (100, -980)] + [(100, 10)] * 30)
        self.assertEqual(len(result), 1)
        self.assertGreater(result[0]['off_trend_return_counts'], 900)

    def test_rebound_lobes_grouped_and_decay_visible(self):
        result = episodes([(100, 10)] * 30 + [(100, 5), (100, 2), (100, -8),
                          (100, 4), (100, -3), (100, 1), (100, -1)] + [(100, 0)] * 30)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]['lobes_decreasing'])
        self.assertTrue(result[0]['rebound_supported'])
        self.assertEqual(result[0]['negative_lobes'], [8, 3, 1])
        self.assertEqual(result[0]['after']['state'], 'stationary')
        self.assertEqual(result[0]['off_trend_return_counts'], 0)

    def test_non_decreasing_lobes_cannot_be_described_as_rebound(self):
        result = episodes([(100, 10)] * 30 + [(100, -8), (100, 4), (100, -12),
                          (100, 3), (100, -2)] + [(100, 0)] * 30)
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]['lobes_decreasing'])
        self.assertFalse(result[0]['rebound_supported'])
        self.assertNotIn('interpretation', result[0])

    def test_continuity_gap_is_not_a_reversal_or_recovery(self):
        result = episodes([(100, 10)] * 30 + [(5000, -10000)] + [(100, 10)] * 30)
        self.assertEqual(result, [])

    def test_invalid_sample_separates_processes(self):
        rows = series([(100, 10)] * 30 + [(100, -20)] * 3 + [(100, 0)] + [(100, -20)] * 3)
        rows[34]['invalid'] = True
        result = ANALYZER['motion_episodes'](rows, 500)
        self.assertEqual(len(result), 2)
        self.assertFalse(result[0]['context_complete'])

    def test_forward_rate_change_is_not_missed(self):
        result = episodes([(100, 10)] * 30 + [(100, 100)] * 30)
        self.assertTrue(any(e['rate_transition_observed'] for e in result))
        self.assertFalse(any(e['drawdown_counts'] for e in result))

    def test_full_span_envelope_keeps_peak_and_tail(self):
        result = episodes([(100, 10)] * 30 + [(100, 1000), (100, -980)] + [(100, 10)] * 30)[0]
        view = ANALYZER['episode_view'](result, 16)
        self.assertLessEqual(len(view['trace']), 16)
        self.assertEqual(view['trace'][0], result['series'][0][:2])
        self.assertEqual(view['trace'][-1], result['series'][-1][:2])
        self.assertEqual(max(r[1] for r in view['trace']), max(r[1] for r in result['series']))

    def test_peer_comparison_is_not_claimed_as_verified_normal(self):
        data = [(100, 10)] * 30
        for drop in [10, 11, 9, 10, 11, 9, 200]:
            data += [(100, -drop)] + [(100, 10)] * 40
        result = episodes(data)
        extreme = max(result, key=lambda e: e['drawdown_counts'])
        self.assertNotIn('drawdown_counts', extreme['comparison']['features'])
        self.assertIn('not_verified_normal', extreme['comparison']['reference'])

    def test_one_count_quantization_is_not_off_trend_jump(self):
        result = episodes([(100, 0)] * 30 + [(100, -1), (100, 1)] + [(100, 0)] * 30)
        self.assertFalse(any(e['off_trend_return_counts'] for e in result))

    def test_motion_report_cli_persists_process_and_omits_legacy_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            log, saved = Path(td) / 'app.log', Path(td) / 'motion.json'
            rows = series([(100, 10)] * 30 + [(100, -500)] + [(100, 10)] * 30)
            log.write_text('\n'.join(f"{r['ts_text'][:-3]} EncoderVal [{r['count']}], TurnTableSpeed [0 mm/s]" for r in rows))
            proc = subprocess.run([sys.executable, str(SCRIPT), str(log), '--motion-report', '--events-out', str(saved)],
                                  text=True, capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(proc.stdout)
            self.assertEqual(result['episode_count'], 1)
            self.assertNotIn('candidate_event_count', result['facts'])
            self.assertNotIn('top_candidates', result)
            self.assertIn('significant_reverse_event_count', result['facts'])
            self.assertIn('reported_speed_mm_s', result['facts'])
            saved_result = json.loads(saved.read_text())
            self.assertEqual(saved_result['episodes'][0]['drawdown_counts'], 500)
            self.assertNotIn('events', saved_result)
            self.assertLess(len(proc.stdout), 8000)

    def test_motion_report_preserves_high_speed_direction_change_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / 'app.log'
            changes = ([(160, 160.0)] * 30
                       + [(725, 706.0), (-354, -321.0), (547, 540.0),
                          (-500, -300.0), (706, 700.0), (-300, -250.0)]
                       + [(160, 160.0)] * 30)
            now, count, rows = datetime(2026, 9, 14, 13, 25), 2_400_000, []
            for delta, speed in changes:
                now += timedelta(milliseconds=100)
                count += delta
                rows.append(f"{now.strftime('%Y-%m-%d %H:%M:%S:%f')[:-3]} "
                            f"[INFO] EncoderVal [{count}], TurnTableSpeed [{speed} mm/s]")
            log.write_text('\n'.join(rows) + '\n', encoding='utf-8')
            proc = subprocess.run([sys.executable, str(SCRIPT), str(log), '--motion-report'],
                                  text=True, capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(proc.stdout)
            facts = result['facts']
            self.assertEqual(facts['reported_speed_mm_s']['min'], -321.0)
            self.assertEqual(facts['reported_speed_mm_s']['max'], 706.0)
            self.assertGreater(facts['significant_reverse_event_count'], 0)
            self.assertGreater(facts['positive_spike_candidate_count'], 0)
            # The process core starts at the first negative seed, so the leading
            # positive-to-negative transition remains in context rather than core.
            self.assertGreaterEqual(result['episode_summary']['max_material_direction_change_count'], 4)
            self.assertFalse(result['episodes'][0]['rebound_supported'])
            self.assertEqual(result['episode_summary']['expectedness_from_encoder_data'],
                             'unresolved_without_command_or_verified_operating_context')

    def test_saved_episode_query_needs_no_source_logs(self):
        result = episodes([(100, 10)] * 30 + [(100, -10)] + [(100, 10)] * 30)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'events.json'
            path.write_text(json.dumps({'episodes': result, 'events': []}))
            proc = subprocess.run([sys.executable, str(SCRIPT), '--inspect-events', str(path), '--episode', 'M1'],
                                  text=True, capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)['id'], 'M1')
