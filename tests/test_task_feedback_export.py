from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scopex.api.service import TaskService


class UnusedFactory:
    def __call__(self, *args, **kwargs):
        raise AssertionError('not used')


class TaskFeedbackExportTests(unittest.TestCase):
    def test_evaluation_persists_and_export_is_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            service = TaskService(
                audit_root=Path(td) / 'tasks',
                coordinator_factory=UnusedFactory(),
                finalizer_factory=lambda: object(),
            )
            task_id = 'task-review'
            service.store.write_json(task_id, 'task.json', {
                'id': task_id,
                'state': 'COMPLETED',
                'mode': 'task',
                'trigger_type': 'manual',
                'metadata': {'agent_id': 'agent-1'},
            })
            service.store.write_json(task_id, 'result.json', {'valid': True})
            service.store.write_json(task_id, 'evidence.json', {'task_id': task_id, 'items': [{'ref': 'E1'}]})
            # Simulate a large external source next to audit only by name; export allowlist must ignore it.
            (service.store.task_dir(task_id) / 'raw-business-image.jpg').write_bytes(b'x' * 64)

            saved = service.set_evaluation(
                task_id,
                rating='down',
                tags=['hard_to_read', 'incomplete'],
                note='结果需要更容易阅读',
            )
            self.assertEqual(saved['rating'], 'down')
            self.assertEqual(service.get_evaluation(task_id)['note'], '结果需要更容易阅读')

            bundle = service.export_review_bundle(task_id)
            self.assertTrue(bundle.is_file())
            with zipfile.ZipFile(bundle) as archive:
                names = set(archive.namelist())
                self.assertIn('manifest.json', names)
                self.assertIn('task.json', names)
                self.assertIn('result.json', names)
                self.assertIn('evidence.json', names)
                self.assertIn('evaluation.json', names)
                self.assertNotIn('raw-business-image.jpg', names)
                manifest = json.loads(archive.read('manifest.json'))
                self.assertFalse(manifest['raw_external_business_files_included'])
                self.assertIn('Model', manifest['review_instruction'])
                self.assertIn('Skill', manifest['review_instruction'])
                self.assertIn('scopex_commit', manifest['runtime_context'])


if __name__ == '__main__':
    unittest.main()
