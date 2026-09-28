from pathlib import Path
import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from scopex.api.fastapi_app import create_app
from scopex.api.schedules import ScheduleService
from scopex.api.service import TaskService


class ScheduleEditAndImagesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.photos = self.root / 'photos'
        self.photos.mkdir()
        self.tasks = TaskService(audit_root=self.root / 'tasks', work_root=self.root / 'work',
                                 data_binds=(f'{self.photos}:/agent-data/left-camera:ro',),
                                 coordinator_factory=None, finalizer_factory=None)
        self.schedules = ScheduleService(self.root / 'schedules', self.tasks)
        self.client = TestClient(create_app(self.tasks, schedules=self.schedules))
        self.payload = dict(name='相机检查', message='连续三张脏污报异常', kind='daily', daily_time='09:15', assessment_enabled=False)

    def test_edit_requires_disabled_preserves_identity_history_and_reloads(self):
        row = self.schedules.create(name='旧名称', message='旧说明', kind='interval', interval_minutes=30)
        url = f'/schedules/{row["id"]}'
        self.assertEqual(self.client.patch(url, json=self.payload).status_code, 409)
        self.assertEqual(self.schedules.get(row['id']), row)
        self.schedules.set_enabled(row['id'], False)
        self.schedules._items[row['id']].update(last_task_id='task-old', last_status='TRIGGERED', missed_count=2)
        original = self.schedules.get(row['id'])
        self.schedules._append_run({'schedule_id': row['id'], 'task_id': 'task-old'})
        history = self.schedules.runs_path.read_bytes()
        response = self.client.patch(url, json=self.payload)
        self.assertEqual(response.status_code, 200)
        edited = response.json()
        self.assertFalse(edited['enabled'])
        self.assertIsNone(edited['next_run_at'])
        self.assertIsNone(edited['interval_minutes'])
        for key in ('id', 'created_at', 'last_task_id', 'last_status', 'missed_count'):
            self.assertEqual(edited[key], original[key])
        self.assertEqual(self.schedules.runs_path.read_bytes(), history)
        self.assertEqual(ScheduleService(self.schedules.root, self.tasks).get(row['id']), edited)
        enabled = self.schedules.set_enabled(row['id'], True)
        self.assertEqual(enabled['daily_time'], '09:15')
        self.assertIsNotNone(enabled['next_run_at'])

    def test_invalid_edits_are_atomic_and_expired_once_can_be_rescheduled(self):
        row = self.schedules.create(name='x', message='y', kind='interval', interval_minutes=30, enabled=False)
        url = f'/schedules/{row["id"]}'
        for payload in ({**self.payload, 'daily_time': '25:00'}, {**self.payload, 'name': '  '},
                        {**self.payload, 'kind': 'once', 'run_at': '2020-01-01T00:00:00'},
                        {**self.payload, 'enabled': True}):
            self.assertEqual(self.client.patch(url, json=payload).status_code, 400)
            self.assertEqual(self.schedules.get(row['id']), row)
        future = (datetime.now().astimezone() + timedelta(days=1)).isoformat()
        self.assertEqual(self.client.patch(url, json={**self.payload, 'kind': 'once', 'run_at': future}).status_code, 200)
        self.assertEqual(self.client.patch('/schedules/missing', json=self.payload).status_code, 404)

    def test_stale_due_trigger_does_not_run_after_disable_and_edit(self):
        row = self.schedules.create(name='x', message='y', kind='interval', interval_minutes=30)
        self.schedules.set_enabled(row['id'], False)
        self.schedules.update(row['id'], **self.payload)
        result = self.schedules._trigger(row['id'], row['next_run_at'], advance_schedule=True)
        self.assertEqual(result['status'], 'SKIPPED_DISABLED')
        self.assertEqual(self.tasks.list_tasks(), [])
        self.assertFalse(self.schedules.runs_path.exists())

    def test_photos_only_from_task_evidence_deduplicate_and_verify_digest(self):
        task_id = 'task-images'
        self.tasks.store.write_json(task_id, 'task.json', {'id': task_id, 'state': 'COMPLETED'})
        data = b'\xff\xd8\xffsynthetic-test'
        target = self.photos / '20260928-122758046.jpg'
        target.write_bytes(data)
        def item(source):
            return {'source': source, 'metadata': {'evidence_type': 'image', 'sha256': hashlib.sha256(data).hexdigest()}}
        source = '/agent-data/left-camera/' + target.name
        evidence = {'items': [item(source), item(source), item('/etc/secret.jpg'),
                              item('/agent-data/left-camera/../../secret.jpg')]}
        self.tasks.store.write_json(task_id, 'evidence.json', evidence)
        url = f'/tasks/{task_id}/images'
        rows = self.client.get(url).json()['images']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['filename'], target.name)
        response = self.client.get(url + '/0')
        self.assertEqual(response.content, data)
        self.assertEqual(response.headers['content-type'], 'image/jpeg')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(self.client.get(url + '/1').status_code, 400)
        target.write_bytes(data + b'changed')
        self.assertEqual(self.client.get(url + '/0').status_code, 400)
        target.unlink()
        self.assertEqual(self.client.get(url + '/0').status_code, 400)
        outside = self.root / 'outside.jpg'
        outside.write_bytes(data)
        target.symlink_to(outside)
        self.assertEqual(self.client.get(url).json()['images'], [])
        self.assertEqual(self.client.get('/tasks/missing/images').status_code, 404)
