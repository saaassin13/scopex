"""Unknown-input motion counterexamples, independent of field event timestamps."""
from test_encoder_motion_context import ANALYZER, series, SCRIPT
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


def episodes(increments):
    return ANALYZER['motion_episodes'](series(increments), 500)


class EpisodeTests(unittest.TestCase):
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
        self.assertEqual(result[0]['negative_lobes'], [8, 3, 1])
        self.assertEqual(result[0]['after']['state'], 'stationary')
        self.assertEqual(result[0]['off_trend_return_counts'], 0)

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
        self.assertGreater(extreme['comparison']['features']['drawdown_counts']['robust_deviation'], 8)
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
            self.assertEqual(json.loads(saved.read_text())['episodes'][0]['drawdown_counts'], 500)
            self.assertLess(len(proc.stdout), 8000)

    def test_saved_episode_query_needs_no_source_logs(self):
        result = episodes([(100, 10)] * 30 + [(100, -10)] + [(100, 10)] * 30)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'events.json'
            path.write_text(json.dumps({'episodes': result, 'events': []}))
            proc = subprocess.run([sys.executable, str(SCRIPT), '--inspect-events', str(path), '--episode', 'M1'],
                                  text=True, capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)['id'], 'M1')
