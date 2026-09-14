from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from scopex.api.schedules import ScheduleService
from scopex.api.service import TaskBusyError


class StubTasks:
    def __init__(self):
        self.busy = False
        self.calls = []

    def create_task(self, message, **kwargs):
        if self.busy:
            raise TaskBusyError('busy')
        self.calls.append((message, kwargs))
        return {'id': 'task-scheduled'}


class ScheduleServiceTests(unittest.TestCase):
    def test_schedule_triggers_ordinary_task_with_metadata_without_shifting_cadence(self):
        with tempfile.TemporaryDirectory() as td:
            tasks = StubTasks()
            service = ScheduleService(Path(td), tasks)
            row = service.create(
                name='encoder', message='检查过去30分钟编码器', kind='interval', interval_minutes=30,
            )
            original_next = row['next_run_at']
            run = service.run_now(row['id'])
            self.assertEqual(run['status'], 'TRIGGERED')
            self.assertTrue(run['manual_run_now'])
            self.assertEqual(run['task_id'], 'task-scheduled')
            message, kwargs = tasks.calls[-1]
            self.assertEqual(message, '检查过去30分钟编码器')
            self.assertEqual(kwargs['mode'], 'task')
            self.assertEqual(kwargs['trigger_type'], 'schedule')
            self.assertEqual(kwargs['schedule_id'], row['id'])
            self.assertTrue(kwargs['scheduled_for'])
            self.assertEqual(service.get(row['id'])['next_run_at'], original_next)

    def test_busy_schedule_is_skipped_not_queued(self):
        with tempfile.TemporaryDirectory() as td:
            tasks = StubTasks()
            tasks.busy = True
            service = ScheduleService(Path(td), tasks)
            row = service.create(
                name='images', message='检查过去30分钟图片', kind='interval', interval_minutes=30,
            )
            original_next = row['next_run_at']
            run = service.run_now(row['id'])
            self.assertEqual(run['status'], 'SKIPPED_BUSY')
            self.assertIsNone(run['task_id'])
            self.assertEqual(tasks.calls, [])
            self.assertEqual(service.get(row['id'])['next_run_at'], original_next)

    def test_offline_interval_misses_advance_without_creating_historical_tasks(self):
        with tempfile.TemporaryDirectory() as td:
            tasks = StubTasks()
            service = ScheduleService(Path(td), tasks)
            row = service.create(
                name='encoder', message='检查过去30分钟编码器', kind='interval', interval_minutes=30,
            )
            with service._lock:
                service._items[row['id']]['next_run_at'] = '2026-09-14T10:00:00+08:00'
                service._reconcile_offline_misses_locked(datetime.fromisoformat('2026-09-14T11:10:00+08:00'))
            updated = service.get(row['id'])
            self.assertEqual(tasks.calls, [])
            self.assertEqual(updated['missed_count'], 3)
            self.assertEqual(updated['last_missed_at'], '2026-09-14T11:00:00+08:00')
            self.assertEqual(updated['next_run_at'], '2026-09-14T11:30:00+08:00')
            self.assertEqual(updated['last_status'], 'MISSED_OFFLINE')

    def test_offline_once_schedule_expires_without_task(self):
        with tempfile.TemporaryDirectory() as td:
            tasks = StubTasks()
            service = ScheduleService(Path(td), tasks)
            service._items['schedule-once'] = {
                'id': 'schedule-once',
                'name': 'once',
                'message': 'x',
                'kind': 'once',
                'interval_minutes': None,
                'daily_time': None,
                'run_at': '2026-09-14T10:00:00+08:00',
                'enabled': True,
                'created_at': '2026-09-14T09:00:00+08:00',
                'updated_at': '2026-09-14T09:00:00+08:00',
                'next_run_at': '2026-09-14T10:00:00+08:00',
                'last_run_at': None,
                'last_status': None,
                'last_task_id': None,
                'missed_count': 0,
                'last_missed_at': None,
            }
            with service._lock:
                service._reconcile_offline_misses_locked(datetime.fromisoformat('2026-09-14T11:00:00+08:00'))
            updated = service.get('schedule-once')
            self.assertFalse(updated['enabled'])
            self.assertIsNone(updated['next_run_at'])
            self.assertEqual(updated['missed_count'], 1)
            self.assertEqual(updated['last_status'], 'MISSED_OFFLINE')
            self.assertEqual(tasks.calls, [])

    def test_daily_and_once_validation(self):
        with tempfile.TemporaryDirectory() as td:
            service = ScheduleService(Path(td), StubTasks())
            daily = service.create(name='disk', message='检查当前磁盘', kind='daily', daily_time='08:00')
            self.assertEqual(daily['daily_time'], '08:00')
            with self.assertRaises(ValueError):
                service.create(name='bad', message='x', kind='daily', daily_time='25:00')


if __name__ == '__main__':
    unittest.main()
