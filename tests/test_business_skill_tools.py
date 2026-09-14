from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def run_script(path: Path, *args: str):
    proc = subprocess.run(
        [sys.executable, str(path), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return proc


class BusinessSkillToolTests(unittest.TestCase):
    def test_nipple_stats_aggregates_per_cow_and_caps_over_detection(self):
        script = ROOT / 'skills/nipple-recognition-analysis/scripts/nipple_stats.py'
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rows = [
                {'ts': '2026-09-14T07:01:00', 'cow': 'c1', 'nipples': [1, 2]},
                {'ts': '2026-09-14T07:01:02', 'cow': 'c1', 'nipples': [1, 2, 3, 4]},
                {'ts': '2026-09-14T07:10:00', 'cow': 'c2', 'nipples': [1, 2, 3]},
                {'ts': '2026-09-14T07:20:00', 'cow': 'c3', 'nipples': [1, 2, 3, 4, 5]},
            ]
            (root / 'r.json').write_text(json.dumps(rows), encoding='utf-8')
            proc = run_script(
                script, str(root),
                '--time-field', 'ts', '--cow-field', 'cow', '--nipple-field', 'nipples',
                '--policy', 'max', '--start', '2026-09-14T07:00:00', '--end', '2026-09-14T08:00:00',
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            summary = data['summary']
            self.assertEqual(summary['total_cows'], 3)
            self.assertEqual(summary['exactly_four_cows'], 1)
            self.assertEqual(summary['over_four_cows'], 1)
            self.assertEqual(summary['raw_detected_nipples'], 12)
            self.assertEqual(summary['capped_detected_nipples'], 11)
            self.assertEqual(summary['expected_nipples'], 12)
            self.assertAlmostEqual(summary['nipple_recognition_rate'], 11 / 12, places=6)

    def test_encoder_health_does_not_bridge_invalid_sample(self):
        script = ROOT / 'skills/encoder-health/scripts/encoder_health.py'
        invalid = 2 ** 63
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / 'app.log'
            log.write_text(
                '\n'.join([
                    '2026-09-14 07:00:00:000 [INFO] Get EncoderVal, raw[100], filtered[100]',
                    '2026-09-14 07:00:00:020 [INFO] Get EncoderVal, raw[110], filtered[109]',
                    f'2026-09-14 07:00:00:040 [INFO] Get EncoderVal, raw[{invalid}], filtered[0]',
                    '2026-09-14 07:00:00:060 [INFO] Get EncoderVal, raw[90], filtered[91]',
                    '2026-09-14 07:00:00:080 [INFO] Get EncoderVal, raw[85], filtered[86]',
                    '2026-09-14 07:00:00:220 [INFO] Get EncoderVal, raw[95], filtered[95]',
                    '2026-09-14 07:00:00:240 [INFO] Get EncoderVal, raw[95], filtered[95]',
                    '2026-09-14 07:00:00:260 [INFO] Get EncoderVal, raw[95], filtered[95]',
                ]) + '\n',
                encoding='utf-8',
            )
            proc = run_script(script, str(log), '--flat-ms', '30', '--gap-min-ms', '50')
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data['summary']['invalid_samples'], 1)
            events = data['events']
            self.assertTrue(any(e['type'] == 'negative_jump' and e['raw_from'] == 90 and e['raw_to'] == 85 for e in events))
            self.assertFalse(any(e.get('raw_from') == 110 and e.get('raw_to') == 90 for e in events))
            self.assertTrue(any(e['type'] == 'sampling_gap' for e in events))
            self.assertTrue(any(e['type'] == 'flat_raw_candidate' for e in events))

    def test_log_context_is_bounded_and_preserves_raw_lines(self):
        script = ROOT / 'skills/log-context/scripts/log_context.py'
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / 'app.log'
            log.write_text(
                '\n'.join([
                    '2026-09-14 07:00:00:000 [INFO] before',
                    '2026-09-14 07:00:01:000 [WARN] ResetEncoderValOnSerialPort',
                    '2026-09-14 07:00:02:000 [INFO] after',
                ]) + '\n', encoding='utf-8')
            proc = run_script(
                script, str(log), '--center', '2026-09-14 07:00:01:000', '--window-s', '0.5',
                '--keyword', 'ResetEncoder', '--before', '1', '--after', '1', '--max-lines', '10')
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data['anchors'], 1)
            rows = data['sources'][0]['lines']
            self.assertEqual([r['line_no'] for r in rows], [1, 2, 3])
            self.assertTrue(rows[1]['anchor'])
            self.assertIn('ResetEncoderValOnSerialPort', rows[1]['raw'])

    def test_log_context_keeps_anchor_when_line_budget_is_tight(self):
        script = ROOT / 'skills/log-context/scripts/log_context.py'
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / 'app.log'
            log.write_text(
                '\n'.join([
                    '2026-09-14 07:00:00:000 [INFO] before-a',
                    '2026-09-14 07:00:00:500 [INFO] before-b',
                    '2026-09-14 07:00:01:000 [WARN] TARGET_ANCHOR',
                    '2026-09-14 07:00:01:500 [INFO] after',
                ]) + '\n', encoding='utf-8')
            proc = run_script(
                script, str(log), '--keyword', 'TARGET_ANCHOR', '--before', '2', '--after', '1', '--max-lines', '1')
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            rows = data['sources'][0]['lines']
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]['anchor'])
            self.assertIn('TARGET_ANCHOR', rows[0]['raw'])

    def test_system_health_summary_reads_historical_window(self):
        script = ROOT / 'skills/system-health/scripts/system_health_summary.py'
        with tempfile.TemporaryDirectory() as td:
            history = Path(td) / 'system_metrics.jsonl'
            rows = [
                {
                    'ts': '2026-09-14T07:00:00+08:00',
                    'cpu': {'util_percent': 20.0, 'load1': 2.0},
                    'memory': {'available_gb': 40.0, 'used_gb': 80.0},
                    'disks': [{'mount': '/', 'free_gb': 1000.0}],
                    'gpu': [{'index': '0', 'util_percent': 50.0, 'temperature_c': 60.0, 'memory_used_mib': 20000, 'power_w': 100.0}],
                    'errors': {},
                },
                {
                    'ts': '2026-09-14T07:00:30+08:00',
                    'cpu': {'util_percent': 80.0, 'load1': 8.0},
                    'memory': {'available_gb': 20.0, 'used_gb': 100.0},
                    'disks': [{'mount': '/', 'free_gb': 990.0}],
                    'gpu': [{'index': '0', 'util_percent': 90.0, 'temperature_c': 70.0, 'memory_used_mib': 24000, 'power_w': 120.0}],
                    'errors': {},
                },
            ]
            history.write_text('\n'.join(json.dumps(r) for r in rows) + '\n', encoding='utf-8')
            proc = run_script(
                script, str(history), '--start', '2026-09-14T07:00:00+08:00', '--end', '2026-09-14T08:00:00+08:00')
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data['samples'], 2)
            self.assertEqual(data['cpu_util_percent']['max'], 80.0)
            self.assertEqual(data['memory_available_gb']['worst'], 20.0)
            self.assertEqual(data['disk_free_gb']['/']['worst'], 990.0)


if __name__ == '__main__':
    unittest.main()
