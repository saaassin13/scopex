from pathlib import Path
import hashlib
import json
import tempfile
import unittest
from fastapi.testclient import TestClient
from scopex.api.fastapi_app import create_app
from scopex.api.service import TaskService


class TaskResultWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.service = TaskService(audit_root=self.root / 'tasks', work_root=self.root / 'work',
                                   coordinator_factory=None, finalizer_factory=None)
        self.client = TestClient(create_app(self.service))

    def task(self, number, *, state='COMPLETED', trigger='manual', assessment='normal'):
        task_id = f'task-{number}'
        self.service.store.write_json(task_id, 'task.json', {
            'id': task_id, 'state': state, 'trigger_type': trigger,
            'user_request': '编码器检查' if number % 2 else '相机检查',
            'created_at': f'2026-09-28T00:00:{number:02d}+00:00',
            'metadata': {'assessment': {'version': 1, 'status': assessment, 'summary': '原任务结论',
                                       'source': 'native', 'push_decision': 'none', 'model_calls': 0}}})
        return task_id

    def test_combined_filters_apply_before_pagination(self):
        self.task(1, assessment='needs_review')
        self.task(2, state='FAILED')
        self.task(3, trigger='schedule', assessment='abnormal')
        self.task(4, assessment='needs_review')
        self.task(5, trigger='schedule', assessment='needs_review')
        url = '/tasks?query=编码器&trigger_type=schedule&attention=true&limit=1'
        self.assertEqual([r['id'] for r in self.client.get(url).json()['tasks']], ['task-5'])
        self.assertEqual([r['id'] for r in self.client.get(url + '&offset=1').json()['tasks']], ['task-3'])
        self.assertEqual(len(self.client.get('/tasks?attention=true').json()['tasks']), 5)
        self.assertEqual(self.client.get('/tasks?trigger_type=anything').status_code, 400)
        self.assertEqual(self.client.get('/tasks?query=' + 'x' * 201).status_code, 400)

    def test_preview_is_excerpt_not_a_generated_verdict_and_keeps_original(self):
        task_id = self.task(1, state='FAILED')
        text = '检查中断，尚未完成。\n已有片段不能证明正常。'
        footer = '<scopex_result>{"status":"normal"}</scopex_result>'
        original = {'report_text': text + footer, 'report_meta': {'assessment_footer': footer}}
        self.service.store.write_json(task_id, 'result.json', original)
        rows = self.client.get('/tasks?include_preview=true').json()['tasks']
        self.assertEqual(rows[0]['state'], 'FAILED')
        self.assertEqual(rows[0]['result_preview'], '检查中断，尚未完成。 已有片段不能证明正常。')
        self.assertEqual(self.service.store.read_json(task_id, 'result.json'), original)
        self.assertNotIn('result_preview', self.client.get('/tasks').json()['tasks'][0])

    def test_plot_preview_is_digest_checked_and_never_serves_other_files(self):
        task_id = self.task(1)
        scratch = self.root / 'work' / task_id / 'scratch'
        directory = scratch / 'encoder-plots'
        directory.mkdir(parents=True)
        png = b'\x89PNG\r\n\x1a\nsynthetic-test'
        target = directory / 'candidate-R1.png'
        target.write_bytes(png)
        artifact = {'filename': target.name, 'sha256': hashlib.sha256(png).hexdigest(), 'candidate_id': 'R1'}
        snapshot = scratch / 'encoder.json'
        def save():
            snapshot.write_text(json.dumps({'schema': 6, 'plots': {'output_dir': '/task-scratch/encoder-plots', 'artifacts': [artifact]}}))
        save()
        self.assertEqual(len(self.client.get(f'/tasks/{task_id}/plots').json()['plots']), 1)
        self.assertEqual(self.client.get(f'/tasks/{task_id}/plots/0').content, png)
        target.write_bytes(png + b'changed')
        self.assertEqual(self.client.get(f'/tasks/{task_id}/plots/0').status_code, 400)
        artifact['filename'] = '../../secret.png'
        save()
        self.assertEqual(self.client.get(f'/tasks/{task_id}/plots').json()['plots'], [])
        artifact['filename'] = target.name
        save()
        target.unlink()
        outside = self.root / 'private.png'
        outside.write_bytes(png)
        target.symlink_to(outside)
        self.assertEqual(self.client.get(f'/tasks/{task_id}/plots').json()['plots'], [])
        self.assertEqual(self.client.get('/tasks/task-missing/plots').status_code, 404)
