from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def run_script(path: Path, *args: str):
    return subprocess.run(
        [sys.executable, str(path), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )


class BusinessSkillToolTests(unittest.TestCase):
    def test_nipple_stats_uses_final_2d_frame_caps_at_four_and_keeps_details_out_of_stdout(self):
        script = ROOT / 'skills/nipple-recognition-analysis/scripts/nipple_stats.py'
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log = root / 'app.log'
            log.write_text(
                '\n'.join([
                    '2026-09-14 07:00:00:000 [INFO] Start left camera AI detect, ImgTimeStamp[20260914-070000000], CowOccuredCount[10], DetectingNumCurRound[1], CowDetectedNumber[1] !',
                    '2026-09-14 07:00:00:010 [INFO] Left camera cow [10] detecting [1] finished, Score[0.95], NippleNum[4]',
                    '2026-09-14 07:00:00:100 [INFO] Start left camera AI detect, ImgTimeStamp[20260914-070000100], CowOccuredCount[10], DetectingNumCurRound[2], CowDetectedNumber[2] !',
                    '2026-09-14 07:00:00:110 [INFO] Left camera cow [10] detecting [2] finished, Score[0.95], NippleNum[2]',
                    '2026-09-14 07:00:00:120 [INFO] New cow detecte finished, cow count [10], LastImgTimeStamp[20260914-070000100], LastImgHasXiNaiQi[0.00]',
                    '2026-09-14 07:00:01:000 [INFO] Start left camera AI detect, ImgTimeStamp[20260914-070001000], CowOccuredCount[11], DetectingNumCurRound[1], CowDetectedNumber[1] !',
                    '2026-09-14 07:00:01:010 [INFO] Left camera cow [11] detecting [1] finished, Score[0.90], NippleNum[5]',
                    '2026-09-14 07:00:01:020 [INFO] New cow detecte finished, cow count [11], LastImgTimeStamp[20260914-070001000], LastImgHasXiNaiQi[0.00]',
                    '2026-09-14 07:00:02:000 [INFO] Start left camera AI detect, ImgTimeStamp[20260914-070002000], CowOccuredCount[12], DetectingNumCurRound[1], CowDetectedNumber[1] !',
                    '2026-09-14 07:00:02:010 [INFO] Left camera cow [12] detecting [1] finished, Score[0.90], NippleNum[4]',
                ]) + '\n',
                encoding='utf-8',
            )

            artifacts = root / 'artifacts'
            hour = artifacts / '20260914' / '07'
            hour.mkdir(parents=True)
            (hour / '20260914-070000100.json').write_text(
                json.dumps({
                    'ImgTimeStamp': '20260914-070000100',
                    'DisinfectTrack': {'CowNipplePosInCamSys': {
                        'Pt1st': {'IsValid': True}, 'Pt2nd': {'IsValid': True},
                        'Pt3rd': {'IsValid': True}, 'Pt4th': {'IsValid': True},
                    }},
                    'Markers': {'Rect': [{'Text': '1'}, {'Text': '2'}]},
                }),
                encoding='utf-8',
            )
            (hour / '20260914-070000100.jpg').write_bytes(b'not-an-image-needed-for-this-test')
            (hour / '20260914-070001000.json').write_text(
                json.dumps({'ImgTimeStamp': '20260914-070001000', 'Markers': {'Rect': [
                    {'Text': '1'}, {'Text': '2'}, {'Text': '3'}, {'Text': '4'}
                ]}}), encoding='utf-8')
            details = root / 'details.json'

            proc = run_script(
                script,
                str(log),
                '--artifact-dir', str(artifacts),
                '--start', '2026-09-14 07:00:00:000',
                '--end', '2026-09-14 08:00:00:000',
                '--details-out', str(details),
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data['scopex_role'], 'business_facts')
            self.assertNotIn('cows', data)
            self.assertLess(len(proc.stdout), 5000)
            summary = data['summary']
            self.assertEqual(summary['total_cows'], 3)
            self.assertEqual(summary['cows_with_final_2d_result'], 2)
            self.assertEqual(summary['complete_four_nipple_cows'], 1)
            self.assertEqual(summary['raw_2d_detections'], 7)
            self.assertEqual(summary['capped_2d_detections'], 6)
            self.assertEqual(summary['expected_nipples'], 12)
            self.assertAlmostEqual(summary['nipple_recognition_rate'], 0.5, places=6)
            self.assertEqual(data['quality']['unfinished_cycles_in_window'], 1)
            self.assertEqual(data['quality']['over_four_2d_detections'], 1)
            cows = {row['cow_occured_count']: row for row in json.loads(details.read_text(encoding='utf-8'))['cows']}
            self.assertEqual(cows[10]['selected_2d_nipple_count'], 2)
            self.assertEqual(cows[11]['selected_2d_nipple_count'], 4)
            self.assertIsNone(cows[12]['selected_2d_nipple_count'])
            self.assertFalse(data['semantics']['three_d_nipple_validity_used'])
            self.assertTrue(cows[10]['artifact_2d_count_matches_log'])
            self.assertTrue(cows[11]['artifact_2d_count_matches_log'])

    def test_encoder_health_does_not_bridge_invalid_sample_and_keeps_small_negative_as_telemetry(self):
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
                ]) + '\n', encoding='utf-8')
            proc = run_script(script, str(log), '--flat-ms', '30', '--gap-min-ms', '50')
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data['scopex_role'], 'business_facts')
            facts = data['facts']
            self.assertEqual(facts['invalid_samples'], 1)
            self.assertEqual(facts['negative_steps_observed'], 1)
            self.assertEqual(facts['sampling_gap_count'], 1)
            self.assertEqual(facts['flat_count_candidate_count'], 1)
            self.assertLessEqual(facts['significant_reverse_event_count'], 1)
            self.assertLess(len(proc.stdout), 5000)

    def test_encoder_health_identifies_reverse_glitch_and_recovery(self):
        script = ROOT / 'skills/encoder-health/scripts/encoder_health.py'
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / 'app.log'
            rows = []
            count = 1000
            for i in range(15):
                rows.append(f'2026-09-14 07:00:00:{i*20:03d} [INFO] EncoderVal [{count}], TurnTableSpeed [50.0 mm/s]')
                count += 20
            # Normal continuation, then one isolated backstep and immediate catch-up.
            rows.append('2026-09-14 07:00:00:300 [INFO] EncoderVal [1300], TurnTableSpeed [50.0 mm/s]')
            rows.append('2026-09-14 07:00:00:320 [INFO] EncoderVal [1250], TurnTableSpeed [-10.0 mm/s]')
            rows.append('2026-09-14 07:00:00:340 [INFO] EncoderVal [1320], TurnTableSpeed [60.0 mm/s]')
            rows.append('2026-09-14 07:00:00:360 [INFO] EncoderVal [1340], TurnTableSpeed [50.0 mm/s]')
            log.write_text('\n'.join(rows) + '\n', encoding='utf-8')
            proc = run_script(script, str(log))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            facts = data['facts']
            self.assertEqual(facts['primary_stream'], 'application_encoder')
            self.assertGreaterEqual(facts['reverse_glitch_candidate_count'], 1)
            self.assertGreaterEqual(facts['anomaly_event_count'], 1)
            events = [row for row in data['top_candidates'] if row['type'] == 'reverse_glitch_candidate']
            self.assertTrue(events)
            self.assertEqual(events[0]['pulse_delta'], -50)
            self.assertTrue(events[0]['recovered'])

    def test_encoder_health_keeps_consecutive_backsteps_as_reverse_interval(self):
        script = ROOT / 'skills/encoder-health/scripts/encoder_health.py'
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / 'app.log'
            rows = []
            count = 1000
            for i in range(15):
                rows.append(f'2026-09-14 07:00:00:{i*20:03d} [INFO] EncoderVal [{count}], TurnTableSpeed [50.0 mm/s]')
                count += 20
            rows.extend([
                '2026-09-14 07:00:00:300 [INFO] EncoderVal [1300], TurnTableSpeed [50.0 mm/s]',
                '2026-09-14 07:00:00:320 [INFO] EncoderVal [1260], TurnTableSpeed [-8.0 mm/s]',
                '2026-09-14 07:00:00:340 [INFO] EncoderVal [1220], TurnTableSpeed [-8.0 mm/s]',
                '2026-09-14 07:00:00:360 [INFO] EncoderVal [1240], TurnTableSpeed [20.0 mm/s]',
            ])
            log.write_text('\n'.join(rows) + '\n', encoding='utf-8')
            proc = run_script(script, str(log))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            facts = data['facts']
            self.assertGreaterEqual(facts['reverse_interval_candidate_count'], 1)
            self.assertEqual(facts['reverse_glitch_candidate_count'], 0)

    def test_encoder_health_log_dir_analyzes_multiple_rotation_files_in_one_call(self):
        script = ROOT / 'skills/encoder-health/scripts/encoder_health.py'
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'CowDisinfect-20260914-030001.log').write_text(
                '2026-09-14 03:00:00:000 [INFO] Get EncoderVal, raw[100], filtered[100]\n'
                '2026-09-14 03:00:00:020 [INFO] Get EncoderVal, raw[110], filtered[110]\n', encoding='utf-8')
            (root / 'CowDisinfect-20260914-030001.log.1').write_text(
                '2026-09-14 03:00:00:040 [INFO] Get EncoderVal, raw[109], filtered[109]\n'
                '2026-09-14 03:00:00:060 [INFO] Get EncoderVal, raw[120], filtered[120]\n', encoding='utf-8')
            (root / 'CowDisinfect-20260914-040001.log').write_text(
                '2026-09-14 04:00:00:000 [INFO] Get EncoderVal, raw[999], filtered[999]\n', encoding='utf-8')
            proc = run_script(
                script,
                '--log-dir', str(root),
                '--start', '2026-09-14 03:00:00:000',
                '--end', '2026-09-14 04:00:00:000',
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(len(data['logs']), 2)
            self.assertEqual(data['facts']['samples_in_window'], 4)
            self.assertEqual(data['facts']['negative_steps_observed'], 1)
            self.assertEqual(data['facts']['anomaly_event_count'], 0)

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
            proc = run_script(script, str(log), '--center', '2026-09-14 07:00:01:000', '--window-s', '0.5', '--keyword', 'ResetEncoder', '--before', '1', '--after', '1', '--max-lines', '10')
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
            proc = run_script(script, str(log), '--keyword', 'TARGET_ANCHOR', '--before', '2', '--after', '1', '--max-lines', '1')
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            rows = data['sources'][0]['lines']
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]['anchor'])
            self.assertIn('TARGET_ANCHOR', rows[0]['raw'])

    def test_system_health_skill_uses_only_current_host_snapshot_and_compact_helper(self):
        text = (ROOT / 'skills/system-health/SKILL.md').read_text(encoding='utf-8')
        self.assertIn('/scopex-host/current.json', text)
        self.assertIn('does **not** continuously collect', text)
        self.assertIn('Do not fall back to sandbox-local measurements', text)
        self.assertIn('system_health.py', text)
        factory = (ROOT / 'scopex/api/factory.py').read_text(encoding='utf-8')
        self.assertIn('write_current_host_snapshot', factory)
        self.assertIn(':/scopex-host:ro', factory)

    def test_system_health_helper_outputs_one_business_fact_object(self):
        script = ROOT / 'skills/system-health/scripts/system_health.py'
        with tempfile.TemporaryDirectory() as td:
            snapshot = Path(td) / 'current.json'
            snapshot.write_text(json.dumps({
                'schema': 1, 'source': 'scopex_host_snapshot', 'captured_at': '2026-09-14T20:22:20+08:00',
                'cpu': {'util_percent': 11.22, 'count': 20, 'load1': 1.0, 'load5': 2.0, 'load15': 3.0},
                'memory': {'total_gb': 121.7, 'used_gb': 57.735, 'available_gb': 63.96, 'swap_used_gb': 0},
                'disks': [{'mount': '/', 'total_gb': 4000, 'used_gb': 1970, 'free_gb': 2030, 'used_percent': 49.25}],
                'gpu': [{'util_percent': 27.2, 'memory_used_mib': 1024, 'memory_total_mib': 4096}],
                'docker': [{}, {}], 'errors': {},
            }), encoding='utf-8')
            proc = run_script(script, '--snapshot', str(snapshot))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data['scopex_role'], 'business_facts')
            self.assertEqual(data['source'], 'system-health')
            self.assertEqual(data['facts']['cpu_util_percent'], 11.22)
            self.assertEqual(data['facts']['memory_available_gb'], 63.96)
            self.assertEqual(data['facts']['disk_root_free_gb'], 2030)
            self.assertLess(len(proc.stdout), 5000)


if __name__ == '__main__':
    unittest.main()
