from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from scopex.api.service import TaskService


class TaskCalendarDeleteTests(unittest.TestCase):
    def service(self, root: Path) -> TaskService:
        return TaskService(
            audit_root=root / 'tasks',
            work_root=root / 'work',
            export_root=root / 'exports',
            coordinator_factory=lambda *args: None,
            finalizer_factory=lambda: object(),
        )

    def write_task(self, service: TaskService, task_id: str, *, state: str, started: str, trigger='manual'):
        service.store.write_json(task_id, 'task.json', {
            'id': task_id,
            'state': state,
            'user_request': task_id,
            'created_at': started,
            'started_at': started,
            'finished_at': started if state in {'COMPLETED', 'FAILED', 'CANCELLED'} else None,
            'duration_ms': 1000,
            'mode': 'task',
            'trigger_type': trigger,
            'metadata': {},
        })

    def test_calendar_groups_runs_by_device_local_day(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            service = self.service(root)
            tz = datetime.now().astimezone().tzinfo
            day14 = datetime(2026, 9, 14, 8, 0, tzinfo=tz).isoformat()
            day15 = datetime(2026, 9, 15, 9, 0, tzinfo=tz).isoformat()
            self.write_task(service, 'task-a', state='COMPLETED', started=day14, trigger='manual')
            self.write_task(service, 'task-b', state='FAILED', started=day14, trigger='schedule')
            self.write_task(service, 'task-c', state='RUNNING', started=day15, trigger='manual')
            calendar = service.calendar_month('2026-09')
            rows = {row['date']: row for row in calendar['days']}
            self.assertEqual(rows['2026-09-14']['count'], 2)
            self.assertEqual(rows['2026-09-14']['completed'], 1)
            self.assertEqual(rows['2026-09-14']['failed'], 1)
            self.assertEqual(rows['2026-09-14']['scheduled'], 1)
            self.assertEqual(rows['2026-09-15']['running'], 1)
            self.assertEqual(len(service.list_tasks(day='2026-09-14')), 2)

    def test_delete_terminal_task_removes_only_scopex_owned_data(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            service = self.service(root)
            tz = datetime.now().astimezone().tzinfo
            started = datetime(2026, 9, 14, 8, 0, tzinfo=tz).isoformat()
            self.write_task(service, 'task-delete', state='COMPLETED', started=started)
            work = root / 'work' / 'task-delete' / 'scratch'
            work.mkdir(parents=True)
            (work / 'tmp.json').write_text('{}', encoding='utf-8')
            exports = root / 'exports'
            exports.mkdir()
            review = exports / 'scopex-review-task-delete.zip'
            review.write_bytes(b'zip')
            external = root / 'agent-data'
            external.mkdir()
            original = external / 'CowDisinfect.log'
            original.write_text('business data', encoding='utf-8')

            result = service.delete_task('task-delete')
            self.assertTrue(result['deleted'])
            self.assertFalse(result['external_business_data_deleted'])
            self.assertFalse((root / 'tasks' / 'task-delete').exists())
            self.assertFalse((root / 'work' / 'task-delete').exists())
            self.assertFalse(review.exists())
            self.assertEqual(original.read_text(encoding='utf-8'), 'business data')


if __name__ == '__main__':
    unittest.main()
