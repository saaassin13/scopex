from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from scopex.api.service import TaskService


class TaskDataPackageTests(unittest.TestCase):
    def service(self, root: Path, logs: Path, camera: Path) -> TaskService:
        return TaskService(
            audit_root=root / 'tasks', work_root=root / 'work', export_root=root / 'exports',
            coordinator_factory=lambda *args: None, finalizer_factory=lambda: object(),
            data_binds=(f'{logs}:/agent-data/logs:ro', f'{camera}:/agent-data/left-camera:ro'),
            collection_max_bytes=1024 * 1024, collection_max_files=20,
        )

    @staticmethod
    def terminal_task(service: TaskService, task_id: str) -> None:
        now = datetime.now().astimezone().isoformat()
        service.store.write_json(task_id, 'task.json', {
            'id': task_id, 'state': 'COMPLETED', 'user_request': 'data', 'created_at': now,
            'started_at': now, 'finished_at': now, 'mode': 'task', 'trigger_type': 'manual', 'metadata': {},
        })

    def test_collects_analyzed_and_window_images_then_task_delete_removes_package_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs, camera = root / 'source-logs', root / 'source-camera'
            logs.mkdir()
            hour = camera / '20260915' / '18'
            hour.mkdir(parents=True)
            first = hour / '20260915-180100000.jpg'
            second = hour / '20260915-180200000.jpg'
            first.write_bytes(b'first')
            second.write_bytes(b'second')
            service = self.service(root, logs, camera)
            self.terminal_task(service, 'task-data')
            service.store.write_json('task-data', 'evidence.json', {
                'task_id': 'task-data', 'items': [{
                    'source': '/agent-data/left-camera/20260915/18/' + first.name,
                    'metadata': {'evidence_type': 'image', 'sha256': hashlib.sha256(b'first').hexdigest()},
                }],
            })
            scratch = root / 'work' / 'task-data' / 'scratch'
            scratch.mkdir(parents=True)
            (scratch / 'data-access.jsonl').write_text(json.dumps({
                'schema': 1, 'source': 'left_camera_multimodal', 'operation': 'locate', 'data_kind': 'jpg',
                'start': '2026-09-15 18:00:00', 'end': '2026-09-15 18:03:00', 'matched_count': 2,
            }) + '\n', encoding='utf-8')

            options = service.get_data_package('task-data')['options']
            self.assertEqual([row['mode'] for row in options], ['image_evidence', 'image_window'])
            result = service.build_data_package('task-data', ['image_evidence', 'image_window'])
            self.assertTrue(result['package_ready'])
            package = service.data_package_file('task-data')
            with zipfile.ZipFile(package) as archive:
                self.assertEqual(set(archive.namelist()), {
                    'images/analyzed/' + first.name, 'images/window/' + first.name,
                    'images/window/' + second.name, 'manifest.json',
                })

            deleted = service.delete_task('task-data')
            self.assertTrue(deleted['collected_business_data_deleted'])
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())

    def test_collects_only_timestamped_log_lines_inside_encoder_window(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs, camera = root / 'source-logs', root / 'source-camera'
            logs.mkdir(); camera.mkdir()
            source = logs / 'CowDisinfect-20260915-180000.log'
            source.write_text(
                '2026-09-15 18:00:00:000 before\n'
                '2026-09-15 18:01:00:000 EncoderVal [1]\n'
                '2026-09-15 18:02:59:999 Get EncoderVal, raw[1]\n'
                '2026-09-15 18:03:00:000 after\n', encoding='utf-8')
            service = self.service(root, logs, camera)
            self.terminal_task(service, 'task-log')
            scratch = root / 'work' / 'task-log' / 'scratch'
            scratch.mkdir(parents=True)
            (scratch / 'data-access.jsonl').write_text(json.dumps({
                'schema': 1, 'source': 'cowdisinfect_logs', 'operation': 'analyze', 'purpose': 'encoder_health',
                'start': '2026-09-15 18:01:00:000', 'end': '2026-09-15 18:03:00:000',
                'source_files': ['/agent-data/logs/' + source.name],
            }) + '\n', encoding='utf-8')

            service.build_data_package('task-log', ['encoder_window'])
            with zipfile.ZipFile(service.data_package_file('task-log')) as archive:
                text = archive.read('logs/window/' + source.name).decode()
            self.assertIn('18:01:00:000', text)
            self.assertIn('18:02:59:999', text)
            self.assertNotIn('18:00:00:000', text)
            self.assertNotIn('18:03:00:000', text)

    def test_partial_download_changed_missing_limit_and_failed_recollection(self):
        from scopex.api.data_packages import DataPackageError
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs, camera = root / 'logs', root / 'camera'
            logs.mkdir(); camera.mkdir()
            changed = camera / 'changed.jpg'; changed.write_bytes(b'current')
            large = camera / 'large.jpg'; large.write_bytes(b'x' * 200)
            service = self.service(root, logs, camera)
            self.terminal_task(service, 'task-partial')
            items = [{'source': '/agent-data/left-camera/' + name,
                      'metadata': {'evidence_type': 'image', 'sha256': hashlib.sha256(b'old').hexdigest()}}
                     for name in ('changed.jpg', 'missing.jpg', 'large.jpg')]
            service.store.write_json('task-partial', 'evidence.json', {'items': items})
            service.data_packages.max_bytes = 100
            result = service.build_data_package('task-partial', ['image_evidence'])
            self.assertEqual(result['missing_files'], 3)
            package = service.data_package_file('task-partial')
            with zipfile.ZipFile(package) as archive:
                data = archive.read('images/analyzed/changed.jpg')
                manifest = json.loads(archive.read('manifest.json'))
                self.assertEqual(data, b'current')
                self.assertEqual(manifest['files'][0]['sha256'], hashlib.sha256(data).hexdigest())
            previous = package.read_bytes()
            changed.unlink()
            with self.assertRaises(DataPackageError):
                service.build_data_package('task-partial', ['image_evidence'])
            self.assertEqual(package.read_bytes(), previous)
            self.assertFalse((package.parent / '.scopex-data.zip.tmp').exists())

    def test_collect_and_delete_are_exclusive_per_task(self):
        from scopex.api.data_packages import DataPackageError
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); logs = root / 'logs'; camera = root / 'camera'
            logs.mkdir(); camera.mkdir()
            service = self.service(root, logs, camera)
            self.terminal_task(service, 'task-one')
            self.terminal_task(service, 'task-two')
            with service.data_packages.operation('task-one'):
                with self.assertRaises(DataPackageError):
                    service.delete_task('task-one')
                with self.assertRaises(DataPackageError):
                    service.build_data_package('task-one', ['encoder_window'])
                self.assertTrue(service.delete_task('task-two')['deleted'])
            self.assertTrue(service.delete_task('task-one')['deleted'])

    def test_overlapping_log_windows_are_unique_and_limit_preserves_whole_lines(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); logs = root / 'logs'; camera = root / 'camera'
            logs.mkdir(); camera.mkdir()
            source = logs / 'source.log'
            lines = [f'2026-09-15 18:01:0{i}:000 sample\n' for i in range(4)]
            source.write_text(''.join(lines))
            service = self.service(root, logs, camera)
            self.terminal_task(service, 'task-overlap')
            scratch = root / 'work/task-overlap/scratch'; scratch.mkdir(parents=True)
            row = dict(schema=1, purpose='encoder_health', operation='analyze',
                       start='2026-09-15 18:01:00', end='2026-09-15 18:02:00',
                       source_files=['/agent-data/logs/source.log'])
            (scratch / 'data-access.jsonl').write_text((json.dumps(row) + '\n') * 2)
            service.data_packages.max_bytes = len(lines[0].encode()) * 2 + 1
            service.build_data_package('task-overlap', ['encoder_window'])
            with zipfile.ZipFile(service.data_package_file('task-overlap')) as archive:
                self.assertEqual(archive.read('logs/window/source.log').decode(), ''.join(lines[:2]))
                self.assertTrue(json.loads(archive.read('manifest.json'))['missing'])

    def test_encoder_script_records_exact_analyzed_inputs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / 'CowDisinfect-20260915-180000.log'
            source.write_text(
                '2026-09-15 18:01:00:000 EncoderVal [1], TurnTableSpeed [1.0 mm/s]\n'
                '2026-09-15 18:01:00:100 EncoderVal [2], TurnTableSpeed [1.0 mm/s]\n',
                encoding='utf-8')
            access = root / 'data-access.jsonl'
            script = Path(__file__).resolve().parents[1] / 'skills/encoder-health/scripts/encoder_health.py'
            proc = subprocess.run([
                sys.executable, str(script), str(source),
                '--start', '2026-09-15 18:01:00:000', '--end', '2026-09-15 18:02:00:000',
            ], capture_output=True, text=True, timeout=10, env={
                **os.environ, 'SCOPEX_DATA_ACCESS_LOG': str(access),
            })
            self.assertEqual(proc.returncode, 0, proc.stderr)
            row = json.loads(access.read_text(encoding='utf-8'))
            self.assertEqual(row['purpose'], 'encoder_health')
            self.assertEqual(row['source_files'], [str(source)])
            self.assertEqual(row['start'], '2026-09-15 18:01:00:000000')


if __name__ == '__main__':
    unittest.main()
