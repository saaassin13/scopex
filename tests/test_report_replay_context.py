"""Replay tests use synthetic audit artifacts and a mock model only."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from scripts.replay_text_report import load_report_context, load_saved, main
from scopex.finalizer.client import FinalizerResponse


class ReportReplayContextTests(unittest.TestCase):
    def artifacts(self):
        return {
            'task.json': {'id': 'fixture', 'session_key': 's', 'state': 'FAILED',
                          'user_request': 'check yesterday', 'created_at': '2026-01-02T00:00:00+00:00',
                          'last_reason': 'text_report_unavailable'},
            'evidence.json': {'task_id': 'fixture', 'session_key': 's', 'items': [
                {'ref': 'E1', 'source': 'sample.log', 'raw': 'observed=12'}]},
            'session.json': {'task_id': 'fixture', 'session_key': 's', 'turns': [
                {'kind': 'USER', 'content': 'check yesterday'},
                {'kind': 'STEER', 'content': 'only the named sample'},
                {'kind': 'RESUME', 'content': 'keep the original window'}]},
            'result.json': {'investigation_reasons': ['budget_reached', 'turn_timeout_budget'],
                            'report_text': ''},
        }

    def write(self, directory, artifacts):
        for name, value in artifacts.items():
            (directory / name).write_text(json.dumps(value), encoding='utf-8')

    def test_directory_restores_time_controls_and_timeout(self):
        artifacts = self.artifacts()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write(root, artifacts)
            task, _ = load_saved(root)
            control, reasons = load_report_context(root, task)
        self.assertIn(task['created_at'], control)
        self.assertIn('STEER: only the named sample', control)
        self.assertIn('RESUME: keep the original window', control)
        self.assertEqual(reasons, ('budget_reached', 'turn_timeout_budget'))

    def test_zip_restores_the_same_context_without_extraction(self):
        artifacts = self.artifacts()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'review.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                for name, value in artifacts.items():
                    archive.writestr(name, json.dumps(value))
            task, _ = load_saved(path)
            control, reasons = load_report_context(path, task)
            self.assertEqual([p.name for p in Path(td).iterdir()], ['review.zip'])
        self.assertIn('STEER: only the named sample', control)
        self.assertIn('turn_timeout_budget', reasons)

    def test_scheduled_anchor_precedes_export_or_replay_time(self):
        artifacts = self.artifacts()
        artifacts['task.json']['scheduled_for'] = '2026-01-01T09:00:00+00:00'
        with tempfile.TemporaryDirectory() as td:
            self.write(Path(td), artifacts)
            controls, _ = load_report_context(td, artifacts['task.json'])
        self.assertIn(artifacts['task.json']['scheduled_for'], controls)
        self.assertNotIn(artifacts['task.json']['created_at'], controls)

    def test_missing_old_context_is_explicit_not_claimed_as_success(self):
        task = self.artifacts()['task.json']
        task.pop('last_reason')
        with tempfile.TemporaryDirectory() as td:
            controls, reasons = load_report_context(td, task)
        self.assertIn('历史控制记录未提供', controls)
        self.assertEqual(reasons, ('saved_execution_outcome_unknown',))

    def test_mismatched_session_is_rejected(self):
        artifacts = self.artifacts()
        artifacts['session.json']['task_id'] = 'other'
        with tempfile.TemporaryDirectory() as td:
            self.write(Path(td), artifacts)
            with self.assertRaisesRegex(ValueError, 'session identity'):
                load_report_context(td, artifacts['task.json'])

    def test_malformed_context_is_rejected_instead_of_silently_lost(self):
        for field in ('turns', 'reasons'):
            artifacts = self.artifacts()
            if field == 'turns':
                artifacts['session.json']['turns'] = 'bad'
            else:
                artifacts['result.json']['investigation_reasons'] = 'bad'
            with self.subTest(field=field), tempfile.TemporaryDirectory() as td:
                self.write(Path(td), artifacts)
                with self.assertRaises(ValueError):
                    load_report_context(td, artifacts['task.json'])

    def test_cli_passes_context_once_and_leaves_history_unchanged(self):
        client = Mock(api_key='')
        client.complete.return_value = FinalizerResponse(
            '调查已超时，只能说明已有样本。', 0.01, 0.02, 0.03, ('stop',), True, None)
        with tempfile.TemporaryDirectory() as td:
            saved = Path(td) / 'saved'
            saved.mkdir()
            self.write(saved, self.artifacts())
            before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in saved.iterdir()}
            out = Path(td) / 'new-report'
            with patch('scripts.replay_text_report.StreamingFinalizerClient', return_value=client), \
                    contextlib.redirect_stdout(io.StringIO()):
                code = main(['--saved', str(saved), '--out', str(out), '--model', 'fixture', '--execute'])
            after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in saved.iterdir()}
            payload = json.loads((out / 'result.json').read_text())
        self.assertEqual(code, 0)
        self.assertEqual(before, after)
        client.complete.assert_called_once()
        prompt = client.complete.call_args.kwargs['user_prompt']
        self.assertIn('turn_timeout_budget', prompt)
        self.assertIn('only the named sample', prompt)
        self.assertIn('2026-01-02T00:00:00+00:00', prompt)
        self.assertEqual(payload['source_task_state'], 'FAILED')
        self.assertEqual(payload['tool_calls'], 0)
        self.assertIn('turn_timeout_budget', payload['report_meta']['completion_reasons'])


if __name__ == '__main__':
    unittest.main()
