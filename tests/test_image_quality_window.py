from pathlib import Path
import runpy
import tempfile
import unittest

TOOL = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'skills/image-quality-diagnosis/scripts/image_quality_window.py'))


class ImageQualityWindowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        hour = self.root / '20260928' / '08'
        hour.mkdir(parents=True)
        self.paths = []
        for second in range(10):
            path = hour / f'20260928-0800{second:02d}000.jpg'
            path.write_bytes(b'original-placeholder-not-a-visual-classifier-test')
            self.paths.append(path)

    def observation(self, i, verdict='degraded'):
        return {'path': str(self.paths[i]), 'verdict': verdict,
                'visually_reviewed': True, 'reason': '用于聚合回归的人工标签'}

    def run_window(self, observations, start='2026-09-28 08:00:00', end='2026-09-28 08:30:00'):
        return TOOL['summarize']({'camera': 'left_camera_multimodal', 'start': start,
                                  'end': end, 'observations': observations}, self.root)

    def test_three_consecutive_images_trigger_and_long_run_is_one_event(self):
        for length in (3, 5):
            result = self.run_window([self.observation(i) for i in range(length)])
            self.assertEqual(result['status'], 'abnormal')
            self.assertEqual(len(result['events']), 1)
            self.assertEqual(result['events'][0]['consecutive_count'], length)
            self.assertEqual(result['events'][0]['max_saved_frame_gap_seconds'], 1)

    def test_nonadjacent_samples_and_reopens_cannot_make_a_triple(self):
        for rows in ([self.observation(i) for i in (0, 2, 4)], [self.observation(0)] * 3):
            result = self.run_window(rows)
            self.assertEqual(result['status'], 'insufficient_evidence')
            self.assertEqual(result['events'], [])
        self.assertEqual(self.run_window([self.observation(0)] * 3)['reviewed_frames'], 1)

    def test_clear_and_uncertain_break_a_run_and_separate_events(self):
        for verdict in ('clear', 'uncertain'):
            result = self.run_window([self.observation(0), self.observation(1, verdict), self.observation(2)])
            self.assertEqual(result['events'], [])
        rows = [self.observation(i, 'clear' if i == 3 else 'degraded') for i in range(7)]
        self.assertEqual(len(self.run_window(rows)['events']), 2)

    def test_unknown_and_missing_never_become_clear(self):
        result = self.run_window([self.observation(0, 'uncertain')])
        self.assertEqual(result['unknown_frames'], 10)
        self.assertEqual(result['status'], 'insufficient_evidence')
        for path in self.paths:
            path.unlink()
        self.assertEqual(self.run_window([])['status'], 'no_images')

    def test_all_reviewed_without_triple_has_narrow_verdict(self):
        result = self.run_window([self.observation(i, 'clear' if i % 3 == 2 else 'degraded') for i in range(10)])
        self.assertEqual(result['status'], 'criterion_not_met')
        self.assertEqual(result['unknown_frames'], 0)
        self.assertNotIn('normal', result.values())
        self.assertNotIn('blurred_duration', result)

    def test_conflicts_metrics_mixed_cameras_and_outside_window_rejected(self):
        bad_rows = ([self.observation(0), self.observation(0, 'clear')],
                    [{**self.observation(0), 'visually_reviewed': False}],
                    [{**self.observation(0), 'camera': 'another'}],
                    [{**self.observation(0), 'reason': ''}])
        for rows in bad_rows:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.run_window(rows)
        with self.assertRaises(ValueError):
            self.run_window([self.observation(0)], start='2026-09-28 08:30:00', end='2026-09-28 09:00:00')

    def test_neighbors_cross_hour_and_windows_are_half_open(self):
        hour = self.root / '20260928' / '07'
        hour.mkdir()
        previous = hour / '20260928-075959000.jpg'
        previous.write_bytes(b'previous-original')
        result = TOOL['neighbors'](self.root, '2026-09-28 07:45:00', '2026-09-28 08:15:00', self.paths[0])
        self.assertIn(str(previous.resolve()), result['consecutive_originals'])
        row = {**self.observation(0), 'path': str(previous)}
        result = self.run_window([row, self.observation(0), self.observation(1)],
                                 start='2026-09-28 07:45:00', end='2026-09-28 08:15:00')
        self.assertEqual(result['status'], 'abnormal')
        self.assertEqual(self.run_window([])['saved_frames'], 10)
