from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DataLocatorSamplingTests(unittest.TestCase):
    def test_truncated_image_selection_covers_full_time_window(self):
        script = ROOT / 'skills/data-locator/scripts/data_locator.py'
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            camera = root / 'camera'
            hour = camera / '20260914' / '13'
            hour.mkdir(parents=True)
            # Ten images spanning the hour; max-files=3 must not return first 3.
            stamps = [
                '130000000', '130500000', '131000000', '131500000', '132000000',
                '133000000', '134000000', '134500000', '135000000', '135900000',
            ]
            for stamp in stamps:
                (hour / f'20260914-{stamp}.jpg').write_bytes(b'jpg')

            logs = root / 'logs'
            logs.mkdir()
            catalog = root / 'catalog.json'
            catalog.write_text(json.dumps({
                'schema': 1,
                'sources': {
                    'cowdisinfect_logs': {
                        'host_path': str(logs), 'agent_path': str(logs), 'type': 'hourly_rotated_log',
                        'access': {'max_hour_buckets': 48, 'max_files_per_operation': 32, 'recursive_scan': False},
                    },
                    'left_camera_multimodal': {
                        'host_path': str(camera), 'agent_path': str(camera), 'type': 'hourly_multimodal',
                        'access': {'max_hour_buckets': 24, 'max_files_per_operation': 256, 'recursive_scan': False},
                    },
                },
            }), encoding='utf-8')

            proc = subprocess.run([
                sys.executable, str(script), '--catalog', str(catalog),
                '--source', 'left_camera_multimodal', '--kind', 'jpg',
                '--start', '2026-09-14 13:00:00', '--end', '2026-09-14 14:00:00',
                '--max-files', '3',
            ], capture_output=True, text=True, timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data['matching_count'], 10)
            self.assertTrue(data['files_truncated'])
            self.assertEqual(data['selection_mode'], 'evenly_spaced_time_sample')
            self.assertEqual(len(data['files']), 3)
            self.assertTrue(data['files'][0].endswith('20260914-130000000.jpg'))
            self.assertTrue(data['files'][-1].endswith('20260914-135900000.jpg'))
            self.assertTrue(any('1320' in path or '1330' in path for path in data['files'][1:2]))


if __name__ == '__main__':
    unittest.main()
