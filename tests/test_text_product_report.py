"""Text contract tests; these do not grade a real model's business accuracy."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scopex.evidence.catalog import EvidenceCatalog
from scopex.evidence.media import EvidenceMediaLoader
from scopex.finalizer.client import FinalizerResponse
from scopex.finalizer.text_report import TextReportComposer
from scopex.api.factory import LocalRuntimeConfig, OpenClawRuntimeFactory
from scopex.events.progress import InMemoryEventSink
from scopex.runtime.task import Task
from scopex.runtime.session import Session
from scopex.storage.audit import AuditStore
from scopex.storage.runtime_audit import RuntimeAudit


def response(text='发现两段回退候选。所列事件在同一分钟，不能确定硬件根因。', finish='stop', done=True):
    return FinalizerResponse(text, 0.01, 0.02, 0.05, (finish,), done, {'completion_tokens': 30})


class TextReportTests(unittest.TestCase):
    def setUp(self):
        self.catalog = EvidenceCatalog('run', 'session')
        self.catalog.add(source='business_facts:counter', raw='{"candidate_count":2,"unknown_cause":true}',
                         metadata={'evidence_type': 'structured_business_facts', 'evidence_role': 'business_facts'})
        self.client = Mock(api_key='test-secret')
        self.client.complete.return_value = response()
        self.composer = TextReportComposer(self.client, model='local')

    def test_plain_text_without_titles_or_json_is_complete(self):
        result = self.composer.run(user_request='check', catalog=self.catalog)
        self.assertTrue(result.valid)
        self.assertEqual(self.client.complete.call_count, 1)
        self.assertNotIn('claims', result.meta)
        self.assertEqual(result.meta['source_check'], 'identity_only_not_semantic_validation')

    def test_markdown_and_nonstandard_headings_do_not_fail(self):
        self.client.complete.return_value = response('### 我的观察\n\n两段候选。\n\n不知原因。')
        result = self.composer.run(user_request='check', catalog=self.catalog)
        self.assertTrue(result.valid)
        self.assertIn('我的观察', result.text)

    def test_empty_report_is_unavailable_not_success_or_retried(self):
        self.client.complete.return_value = response('')
        result = self.composer.run(user_request='check', catalog=self.catalog)
        self.assertFalse(result.valid)
        self.assertEqual(result.status, 'unavailable')
        self.assertIn('text_report_empty', result.meta['errors'])
        self.assertEqual(self.client.complete.call_count, 1)

    def test_length_and_missing_done_are_partial(self):
        for finish, done in [('length', True), ('stop', False)]:
            with self.subTest(finish=finish, done=done):
                self.client.complete.return_value = response('不完整的结果', finish, done)
                result = self.composer.run(user_request='check', catalog=self.catalog)
                self.assertFalse(result.valid)
                self.assertEqual(result.status, 'partial')
                self.assertEqual(result.text, '不完整的结果')

    def test_refusal_is_not_complete(self):
        self.client.complete.return_value = response('拒绝', 'content_filter')
        self.assertFalse(self.composer.run(user_request='check', catalog=self.catalog).valid)

    def test_unknown_citation_is_warned_not_linked_or_faked(self):
        self.client.complete.return_value = response('结果见 [E999]')
        result = self.composer.run(user_request='check', catalog=self.catalog)
        self.assertTrue(result.valid)  # Text delivered, NOT semantically verified.
        self.assertEqual(result.meta['unresolved_citation_refs'], ['E999'])
        self.assertEqual([x['ref'] for x in result.meta['sources']], ['E1'])

    def test_input_over_budget_never_silently_drops_evidence(self):
        result = TextReportComposer(self.client, model='local', max_input_chars=3).run(
            user_request='check', catalog=self.catalog)
        self.assertFalse(result.valid)
        self.assertIn('capacity_exceeded', result.meta['errors'][0])
        self.client.complete.assert_not_called()

    def test_working_data_not_promoted_and_original_ids_preserved(self):
        catalog = EvidenceCatalog('t', 's')
        catalog.add(source='/task-scratch/tmp', raw='working', metadata={'evidence_role': 'working_derived'})
        catalog.add(source='business_facts:result', raw='observed', metadata={'evidence_type': 'structured_business_facts'})
        result = self.composer.run(user_request='check', catalog=catalog)
        self.assertEqual([x['ref'] for x in result.meta['sources']], ['E2'])
        self.assertNotIn('working', self.client.complete.call_args.kwargs['user_prompt'])

    def test_no_evidence_does_not_make_a_generic_report(self):
        result = self.composer.run(user_request='check', catalog=EvidenceCatalog('t', 's'))
        self.assertFalse(result.valid)
        self.client.complete.assert_not_called()

    def test_image_sha_failure_stops_before_model_request(self):
        catalog = EvidenceCatalog('t', 's')
        catalog.add(source='/readonly/a.png', raw='image', metadata={'evidence_type': 'image', 'sha256': 'changed', 'media_type': 'image/png'})
        with tempfile.TemporaryDirectory() as td:
            Path(td, 'a.png').write_bytes(b'original')
            composer = TextReportComposer(self.client, model='local', media_loader=EvidenceMediaLoader((f'{td}:/readonly:ro',)))
            result = composer.run(user_request='see image', catalog=catalog)
        self.assertFalse(result.valid)
        self.assertIn('image_evidence_changed', result.meta['errors'][0])
        self.client.complete.assert_not_called()

    def test_original_image_is_attached_once(self):
        import hashlib
        catalog = EvidenceCatalog('t', 's')
        data = b'test-original'
        catalog.add(source='/readonly/a.png', raw='image', metadata={'evidence_type': 'image',
                    'sha256': hashlib.sha256(data).hexdigest(), 'media_type': 'image/png'})
        with tempfile.TemporaryDirectory() as td:
            Path(td, 'a.png').write_bytes(data)
            result = TextReportComposer(self.client, model='local', media_loader=EvidenceMediaLoader((f'{td}:/readonly:ro',))).run(user_request='view', catalog=catalog)
        self.assertTrue(result.valid)
        call = self.client.complete.call_args.kwargs
        self.assertEqual(len(call['image_inputs']), 1)
        self.assertNotIn('tools', call)
        self.assertEqual(result.meta['image_evidence_refs'], ['E1'])
        self.assertNotIn('base64', json.dumps(result.meta))

    def test_secret_is_redacted_and_control_scope_reaches_report(self):
        self.client.complete.return_value = response('Bearer test-secret')
        result = self.composer.run(user_request='check', catalog=self.catalog,
                                   control_context='STEER: only application data')
        self.assertNotIn('test-secret', result.text)
        self.assertIn('only application data', self.client.complete.call_args.kwargs['user_prompt'])

    def test_factory_product_path_uses_text_without_claims_call(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cli = root / 'openclaw'; cli.write_text('#!/bin/sh\nexit 0\n'); cli.chmod(0o755)
            workspace = root / 'workspace'; workspace.mkdir()
            cfg = LocalRuntimeConfig(cli_path=cli, model_id='local', base_url='http://127.0.0.1:18002/v1',
                                     api_key='', workspace=workspace, work_root=root/'work', sandbox_image='test',
                                     docker_host='unix:///var/run/docker.sock')
            task = Task('t', 'check', 'agent:sx1:t', metadata={'agent_id':'sx1'})
            audit = RuntimeAudit(AuditStore(root/'audit'), task.id)
            with patch('scopex.api.factory.write_current_host_snapshot'):
                coord = OpenClawRuntimeFactory(cfg).coordinator(task, Session(task.id, task.session_key), InMemoryEventSink(), audit)
            self.assertIsNotNone(coord.text_reporter)
            self.assertIsNone(coord.report_composer)
            coord.text_reporter.client = self.client
            coord.catalog.add(source='business_facts:counter', raw='two candidates', metadata={'evidence_type':'structured_business_facts'})
            coord.controller.start(); coord.begin_finalization(goal_satisfied=True)
            legacy = Mock()
            result = coord.finish_fresh_finalization(legacy)
            self.assertTrue(result.valid)
            legacy.run.assert_not_called()
            self.assertEqual(self.client.complete.call_count, 1)
            stored = audit.store.read_json(task.id,'result.json')
            self.assertEqual(stored['version'], 2)
            self.assertEqual(stored['report_text'], result.text)
            self.assertFalse((root/'audit'/'t'/'claims.json').exists())
