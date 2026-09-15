from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest

from scopex.data_catalog import (
    load_data_catalog,
    provision_locator_catalog,
    provision_workspace_catalog,
    render_runtime_catalog_summary,
)

ROOT = Path(__file__).resolve().parents[1]


class DataCatalogTests(unittest.TestCase):
    def test_product_catalog_uses_real_cowdisinfect_paths(self):
        catalog = load_data_catalog(ROOT / 'config/data-catalog.json')
        logs = catalog['sources']['cowdisinfect_logs']
        camera = catalog['sources']['left_camera_multimodal']
        self.assertEqual(logs['host_path'], '/opt/ScalingRobotics/CowDisinfect/Log')
        self.assertEqual(logs['agent_path'], '/agent-data/logs')
        self.assertEqual(camera['host_path'], '/opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera')
        self.assertEqual(camera['agent_path'], '/agent-data/left-camera')
        self.assertFalse(logs['access']['recursive_scan'])
        self.assertFalse(camera['access']['recursive_scan'])
        summary = render_runtime_catalog_summary(catalog)
        self.assertIn('cowdisinfect_logs', summary)
        self.assertIn('left_camera_multimodal', summary)
        self.assertIn('Do not use recursive', summary)

    def test_workspace_and_locator_catalog_are_copied_not_symlinked(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td) / 'workspace'
            locator_skill = workspace / 'skills' / 'data-locator'
            locator_skill.mkdir(parents=True)
            catalog_path = ROOT / 'config/data-catalog.json'

            host_target = provision_workspace_catalog(workspace=workspace, catalog_path=catalog_path)
            locator_target = provision_locator_catalog(workspace=workspace, catalog_path=catalog_path)

            self.assertEqual(host_target, workspace / 'scopex-data-catalog.json')
            self.assertEqual(locator_target, workspace / 'skills/data-locator/references/data-catalog.json')
            for target in (host_target, locator_target):
                self.assertTrue(target.is_file())
                self.assertFalse(target.is_symlink())
                self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['schema'], 1)

            locator_script = (ROOT / 'skills/data-locator/scripts/data_locator.py').read_text(encoding='utf-8')
            self.assertIn("/workspace/skills/data-locator/references/data-catalog.json", locator_script)
            self.assertNotIn("CATALOG_DEFAULT = Path('/workspace/scopex-data-catalog.json')", locator_script)

    def test_locator_uses_file_start_intervals_and_only_target_multimodal_hour(self):
        script = ROOT / 'skills/data-locator/scripts/data_locator.py'
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs = root / 'logs'
            camera = root / 'camera'
            logs.mkdir()
            (camera / '20260914' / '13').mkdir(parents=True)
            (camera / '20260914' / '14').mkdir(parents=True)

            for name in (
                'CowDisinfect-20260914-092336.log',
                'CowDisinfect-20260914-102336.log',
                'CowDisinfect-20260914-102336.log.1',
                'CowDisinfect-20260914-112336.log',
            ):
                (logs / name).write_text('x\n', encoding='utf-8')

            hour13 = camera / '20260914' / '13'
            (hour13 / '20260914-130002161.jpg').write_bytes(b'jpg')
            (hour13 / '20260914-130002161.json').write_text('{}', encoding='utf-8')
            (hour13 / '20260914-130002161.pcd').write_text('pcd', encoding='utf-8')
            (hour13 / '20260914-135959999.jpg').write_bytes(b'jpg2')
            (camera / '20260914' / '14' / '20260914-140000001.jpg').write_bytes(b'outside')

            catalog_path = root / 'catalog.json'
            catalog_path.write_text(json.dumps({
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
                sys.executable, str(script), '--catalog', str(catalog_path),
                '--source', 'cowdisinfect_logs', '--start', '2026-09-14 11:00:00', '--end', '2026-09-14 11:10:00',
            ], capture_output=True, text=True, timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data['scopex_role'], 'locator')
            self.assertEqual(data['matching_count'], 2)
            self.assertTrue(all('102336' in path for path in data['files']))
            self.assertEqual(data['selected_log_intervals'][0]['start'], '2026-09-14 10:23:36')
            self.assertEqual(data['selected_log_intervals'][0]['end_exclusive'], '2026-09-14 11:23:36')

            proc = subprocess.run([
                sys.executable, str(script), '--catalog', str(catalog_path),
                '--source', 'left_camera_multimodal', '--kind', 'jpg',
                '--start', '2026-09-14 13:00:00', '--end', '2026-09-14 13:30:00', '--max-files', '32',
            ], capture_output=True, text=True, timeout=10, env={
                **os.environ, 'SCOPEX_DATA_ACCESS_LOG': str(root / 'data-access.jsonl'),
            })
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data['matching_count'], 1)
            self.assertTrue(data['files'][0].endswith('20260914-130002161.jpg'))
            self.assertEqual(data['scanned_hour_dirs'], [str(hour13)])
            access = json.loads((root / 'data-access.jsonl').read_text(encoding='utf-8'))
            self.assertEqual(access['source'], 'left_camera_multimodal')
            self.assertEqual(access['operation'], 'locate')
            self.assertEqual(access['start'], '2026-09-14 13:00:00')


if __name__ == '__main__':
    unittest.main()
