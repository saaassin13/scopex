from pathlib import Path
import tempfile
import unittest
from fastapi.testclient import TestClient
from scopex.api.fastapi_app import create_app
from scopex.api.service import TaskService

class ScheduleHistoryTests(unittest.TestCase):
    def test_filter_before_pagination_preserves_other_history(self):
        with tempfile.TemporaryDirectory() as td:
            service = TaskService(audit_root=Path(td), coordinator_factory=None, finalizer_factory=None)
            for i, schedule in enumerate(['a', None, 'b', 'a', 'a']):
                service.store.write_json(f'task-{i}', 'task.json', {'id': f'task-{i}',
                    'schedule_id': schedule, 'state': 'COMPLETED', 'created_at': f'2026-09-15T00:00:0{i}+00:00'})
            client = TestClient(create_app(service))
            data = client.get('/tasks?schedule_id=a&limit=2').json()['tasks']
            self.assertEqual([r['id'] for r in data], ['task-4', 'task-3'])
            data = client.get('/tasks?schedule_id=a&limit=2&offset=2').json()['tasks']
            self.assertEqual([r['id'] for r in data], ['task-0'])
            self.assertEqual(client.get('/tasks?schedule_id=missing').json()['tasks'], [])
            self.assertEqual(len(client.get('/tasks').json()['tasks']), 5)
            self.assertEqual(client.get('/tasks?offset=-1').status_code, 400)
