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
    def test_nipple_stats_uses_final_2d_frame_and_caps_each_cow_at_four(self):
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
            artifacts.mkdir()
            (artifacts / '20260914-070000100.json').write_text(
                json.dumps({
                    'ImgTimeStamp': '20260914-070000100',
                    'DisinfectTrack': {
                        'CowNipplePosInCamSys': {
                            'Pt1st': {'IsValid': True},
                            'Pt2nd': {'IsValid': True},
                            'Pt3rd': {'IsValid': True},
                            'Pt4th': {'IsValid': True},
                        }
                    },
                    'Markers': {'Rect': [{'Text': '1'}, {'Text': '2'}]},
                }),
                encoding='utf-8',
            )
            (artifacts / '20260914-070000100.jpg').write_bytes(b'not-an-image-needed-for-this-test')
            (artifacts / '20260914-070001000.json').write_text(
                json.dumps({'ImgTimeStamp': '20260914-070001000', 'Markers': {'Rect': [
                    {'Text': '1'}, {'Text': '2'}, {'Text': '3'}, {'Text': '4'}
                ]}}),
                encoding='utf-8',
            )

            proc = run_script(
                script,
                str(log),
                '--artifact-dir', str(artifacts),
                '--start', '2026-09-14 07:00:00:000',
                '--end', '2026-09-14 08:00:00:000',
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
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
            cows = {row['cow_occured_count']: row for row in data['cows']}
            self.assertEqual(cows[10]['selected_2d_nipple_count'], 2)
            self.assertEqual(cows[11]['selected_2d_nipple_count'], 4)
            self.assertIsNone(cows[12]['selected_2d_nipple_count'])
            self.assertFalse(data['semantics']['three_d_nipple_validity_used'])
            self.assertTrue(cows[10]['artifact_2d_count_matches_log'])
            self.assertTrue(cows[11]['artifact_2d_count_matches_log'])

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

    def test_system_health_skill_forbids_history_and_sandbox_fallback(self):
        text = (ROOT / 'skills/system-health/SKILL.md').read_text(encoding='utf-8')
        self.assertIn('does **not** continuously collect', text)
        self.assertIn('never fall back to sandbox metrics', text)
        self.assertIn('historical resource analysis', text)


if __name__ == '__main__':
    unittest.main()
