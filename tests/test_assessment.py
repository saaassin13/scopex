from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from scopex.assessment import INSTRUCTION, from_native, parse_footer, record, task_summary
from scopex.api.assessment_classifier import TextAssessmentClassifier
from scopex.api.fastapi_app import create_app
from scopex.api.schedules import ScheduleService
from scopex.api.service import TaskService, TaskBusyError, TaskConflictError
from scopex.finalizer.client import FinalizerResponse
from scopex.runtime.task import Task, TaskState
from scopex.runtime.session import Session
import test_native_answers as native_fixture


def footer(status='abnormal', summary='按本任务说明，已观察到异常。'):
    return '<scopex_result>' + json.dumps({'status': status, 'summary': summary}, ensure_ascii=False) + '</scopex_result>'


def wait_assessment(service, task_id):
    until = time.monotonic() + 3
    while time.monotonic() < until:
        value = service.get_assessment(task_id)['assessment']
        if value['status'] != 'pending':
            return value
        time.sleep(.01)
    raise AssertionError('assessment did not finish')


class AssessmentContractTests(unittest.TestCase):
    def test_three_terminal_labels_preserve_native_text(self):
        for status in ('normal', 'abnormal', 'needs_review'):
            with self.subTest(status=status):
                text = '当前正文🙂，不是第二篇报告。\n' + footer(status) + '\n'
                value, suffix = parse_footer(text)
                self.assertEqual(value['status'], status)
                self.assertEqual(text[:-len(suffix)], '当前正文🙂，不是第二篇报告。\n')

    def test_keywords_and_no_footer_are_never_classified(self):
        for text in ['没有异常', '历史曾异常，现在不确定', '异常', '', '结果：正常']:
            self.assertEqual(parse_footer(text), (None, None))

    def test_envelope_rejects_ambiguous_or_malformed_labels(self):
        cases = [footer(), '正文\n' + footer() + '\n其他正文', '正文\n' + footer() * 2,
                 '正文\n> ' + footer(), '正文\n```text\n' + footer(),
                 '正文\n<scopex_result>{"status":"normal","status":"abnormal","summary":"x"}</scopex_result>',
                 '正文\n<scopex_result>{"status":[],"summary":"x"}</scopex_result>',
                 '正文\n<scopex_result>{"status":"normal","summary":true}</scopex_result>',
                 '正文\n<scopex_result>{"status":"normal","summary":"x","push":true}</scopex_result>',
                 '正文\n' + footer('success'), '正文\n' + footer(summary=''),
                 '正文\n' + footer(summary='x' * 241), '正文\n' + footer(summary='a\nb'),
                 '正文\n<scopex_result>{"status":"normal"}']
        for text in cases:
            with self.subTest(text=text[:70]):
                self.assertEqual(parse_footer(text), (None, None))

    def test_failure_and_no_data_cannot_publish_business_normal_or_alert(self):
        for complete, no_data in [(False, False), (True, True)]:
            for status in ('normal', 'abnormal'):
                value, _ = from_native('正文\n' + footer(status), complete=complete,
                                       no_data=no_data, request='原任务条件')
                self.assertEqual(value['status'], 'needs_review')
                self.assertEqual(value['push_decision'], 'none')
                self.assertEqual(value['model_calls'], 0)

    def test_missing_label_is_not_an_execution_error_or_a_retry(self):
        value, _ = from_native('完整原生正文，没有标签。', complete=True, no_data=False, request='x')
        self.assertEqual(value['reason'], 'label_missing_or_invalid')
        self.assertEqual(value['status'], 'needs_review')
        self.assertFalse(value['semantic_validation'])

    def test_stale_positive_label_is_suppressed_on_failed_execution(self):
        value = task_summary({'state': 'FAILED', 'metadata': {'assessment': record('abnormal', 'old')}})
        self.assertEqual(value['status'], 'needs_review')
        self.assertEqual(value['push_decision'], 'none')

    def test_old_tasks_remain_unassessed_without_backfill(self):
        for state in ('COMPLETED', 'FAILED'):
            self.assertEqual(task_summary({'state': state, 'metadata': {}})['status'], 'not_assessed')


class NativeAssessmentTests(unittest.TestCase):
    def run_native(self, root, **kwargs):
        return native_fixture.NativeAnswerTests().run_task(root, **kwargs)

    def test_native_inline_label_zero_extra_calls_body_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            text = '本次异常条件已满足。\n' + footer()
            with patch('scopex.finalizer.client.StreamingFinalizerClient.complete', side_effect=AssertionError('no model')):
                service, tid = self.run_native(Path(td), text=text, assessment_enabled=True)
            try:
                result = service.get_result(tid)['result']
                self.assertEqual(result['report_text'], text)
                self.assertEqual(result['report_meta']['postprocess_model_calls'], 0)
                self.assertEqual(result['report_meta']['assessment_footer'], footer())
                task = service.get_task(tid)
                self.assertEqual(task['state'], 'COMPLETED')
                self.assertEqual(task['assessment']['status'], 'abnormal')
                self.assertEqual(task['assessment']['delivery_status'], 'not_connected')
                native_message = service._handles[tid].coordinator.agent.run_turn.call_args.args[0]
                self.assertTrue(native_message.startswith(task['user_request']))
                self.assertTrue(native_message.endswith(INSTRUCTION))
                self.assertEqual(service._handles[tid].session.turns[0].content, task['user_request'])
            finally:
                service.shutdown()

    def test_native_disabled_does_not_inject_label_or_evaluate(self):
        with tempfile.TemporaryDirectory() as td:
            service, tid = self.run_native(Path(td), text='正文\n' + footer(), assessment_enabled=False)
            try:
                self.assertEqual(service.get_task(tid)['assessment']['status'], 'not_assessed')
                self.assertNotIn('scopex_result', service._handles[tid].coordinator.agent.run_turn.call_args.args[0])
                self.assertFalse((Path(td)/'tasks'/tid/'assessment.json').exists())
            finally:
                service.shutdown()

    def test_invalid_label_does_not_fail_completed_task(self):
        with tempfile.TemporaryDirectory() as td:
            service, tid = self.run_native(Path(td), assessment_enabled=True)
            try:
                self.assertEqual(service.get_task(tid)['state'], 'COMPLETED')
                self.assertEqual(service.get_task(tid)['assessment']['status'], 'needs_review')
                self.assertTrue(service.get_result(tid)['result']['valid'])
            finally:
                service.shutdown()

    def test_timeout_keeps_execution_failure_despite_positive_footer(self):
        with tempfile.TemporaryDirectory() as td:
            service, tid = self.run_native(Path(td), text='草稿\n' + footer(), stop='timeout',
                                          limit='turn_timeout_budget', assessment_enabled=True)
            try:
                self.assertEqual(service.get_task(tid)['state'], 'FAILED')
                self.assertEqual(service.get_task(tid)['assessment']['status'], 'needs_review')
                self.assertEqual(service.get_task(tid)['assessment']['push_decision'], 'none')
            finally:
                service.shutdown()

    def test_assessment_write_failure_does_not_remove_native_answer(self):
        with tempfile.TemporaryDirectory() as td:
            from scopex.storage.audit import AuditStore
            original = AuditStore.write_json
            def fail_assessment(store, task_id, name, value):
                if name == 'assessment.json': raise OSError('fixture storage failure')
                return original(store, task_id, name, value)
            with patch.object(AuditStore, 'write_json', fail_assessment):
                service, tid = self.run_native(Path(td), text='正文\n' + footer(), assessment_enabled=True)
            try:
                self.assertEqual(service.get_task(tid)['state'], 'COMPLETED')
                self.assertTrue(service.get_result(tid)['result']['valid'])
                self.assertEqual(service.get_task(tid)['assessment']['reason'], 'assessment_storage_error')
            finally:
                service.shutdown()


class ManualClassifierTests(unittest.TestCase):
    def response(self, text=None, **kw):
        return FinalizerResponse(content=text or footer(), headers_s=.01, first_content_s=.02,
                                 elapsed_s=.1, finish_reasons=kw.get('finish', ('stop',)),
                                 done_seen=kw.get('done', True), usage=None,
                                 tool_call_chunks=kw.get('tools', 0))

    def test_only_bounded_text_and_controls_enter_one_no_tool_request(self):
        client = Mock()
        client.complete.return_value = self.response()
        c = TextAssessmentClassifier(client, model='local')
        value = c(request='CPU与内存任一项超过80%异常', answer='CPU85%，内存72%，CPU超阈值。',
                  controls=[{'kind':'STEER','content':'只检查CPU'}], anchor='2026-09-15T01:00:00Z')
        self.assertEqual(value['status'], 'abnormal')
        self.assertEqual(value['model_calls'], 1)
        self.assertFalse(value['semantic_validation'])
        kwargs = client.complete.call_args.kwargs
        self.assertEqual(kwargs['max_tokens'], 256)
        self.assertNotIn('image_inputs', kwargs)
        data = json.loads(kwargs['user_prompt'])
        self.assertEqual(set(data), {'task', 'request_time', 'user_controls', 'saved_answer'})
        self.assertEqual(data['user_controls'][0]['content'], '只检查CPU')
        client.complete.assert_called_once()

    def test_long_text_not_silently_shortened_or_submitted(self):
        client = Mock()
        value = TextAssessmentClassifier(client, model='local')(request='r', answer='图' * 12000, controls=[], anchor=None)
        self.assertEqual(value['reason'], 'input_over_budget')
        self.assertEqual(value['model_calls'], 0)
        client.complete.assert_not_called()

    def test_incomplete_tool_call_or_malformed_response_has_no_retry(self):
        for response in [self.response(finish=('length',)), self.response(done=False),
                         self.response(tools=1), self.response(text='没有异常')]:
            client = Mock(); client.complete.return_value = response
            value = TextAssessmentClassifier(client, model='local')(request='r', answer='a', controls=[], anchor=None)
            self.assertEqual(value['status'], 'needs_review')
            client.complete.assert_called_once()

    def test_secrets_not_persisted_from_error_or_label(self):
        for response in [ValueError('secret-key'), self.response(text=footer(summary='secret-key'))]:
            client = Mock()
            if isinstance(response, Exception): client.complete.side_effect = response
            else: client.complete.return_value = response
            value = TextAssessmentClassifier(client, model='local', api_key='secret-key')(
                request='r', answer='a', controls=[], anchor=None)
            self.assertNotIn('secret-key', json.dumps(value))


class AssessmentServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.classifier = Mock(return_value=record('abnormal', 'CPU超过任务阈值', source='manual_text', model_calls=1))
        self.service = TaskService(audit_root=self.root/'tasks', coordinator_factory=Mock(),
                                   finalizer_factory=Mock(), assessment_classifier=self.classifier)

    def tearDown(self):
        self.service.shutdown(timeout_s=3)
        self.temp.cleanup()

    def seed(self, tid='task-saved', text='CPU为85%，高于本任务80%阈值。', state=TaskState.COMPLETED, **metadata):
        task = Task(tid, 'CPU超过80%视为异常', 'agent:fixture:'+tid, metadata=metadata, schedule_id='schedule-fixture')
        task.transition(TaskState.RUNNING)
        task.transition(state)
        session = Session(task.id, task.session_key); session.user(task.user_request)
        session.steer('只检查当前CPU，不推断历史')
        self.service.store.write_json(tid, 'task.json', task.snapshot())
        self.service.store.write_json(tid, 'session.json', session.snapshot())
        self.service.store.write_json(tid, 'result.json', {'version':2, 'valid':state==TaskState.COMPLETED,
              'report_text':text, 'report_meta':{'status':'complete' if state==TaskState.COMPLETED else 'partial'}})
        return task

    def test_manual_model_requires_explicit_permission(self):
        task=self.seed()
        response=self.service.request_assessment(task.id)
        self.assertTrue(response['requires_model'])
        self.assertEqual(self.service.get_task(task.id)['assessment']['status'], 'not_assessed')
        self.classifier.assert_not_called()

    def test_manual_classification_is_audited_but_original_result_and_lifecycle_unchanged(self):
        task=self.seed()
        raw=(self.root/'tasks'/task.id/'result.json').read_bytes()
        with patch.object(self.service, 'get_evidence', side_effect=AssertionError('no evidence read')):
            self.service.request_assessment(task.id, allow_model=True)
            value=wait_assessment(self.service, task.id)
        self.assertEqual(value['status'], 'abnormal')
        self.assertEqual(value['manual_model_calls_total'], 1)
        self.assertEqual((self.root/'tasks'/task.id/'result.json').read_bytes(), raw)
        row=self.service.get_task(task.id)
        self.assertEqual(row['finished_at'], task.finished_at)
        self.assertEqual(row['duration_ms'], task.duration_ms)
        self.assertEqual(row['state'], 'COMPLETED')
        self.assertEqual(self.classifier.call_args.kwargs['controls'][0]['kind'], 'STEER')
        self.service.request_assessment(task.id, allow_model=True)
        self.classifier.assert_called_once()
        bundle=self.service.export_review_bundle(task.id)
        import zipfile
        with zipfile.ZipFile(bundle) as z:
            self.assertIn('assessment.json', z.namelist())
            self.assertIn('assessment-history.jsonl', z.namelist())

    def test_manual_footer_reuse_has_no_model_call(self):
        task=self.seed(text='正文\n'+footer())
        value=self.service.request_assessment(task.id)['assessment']
        self.assertEqual(value['source'], 'native_reuse')
        self.classifier.assert_not_called()

    def test_failure_no_data_and_missing_control_do_not_trigger_model(self):
        task=self.seed(state=TaskState.FAILED)
        self.assertEqual(self.service.request_assessment(task.id, allow_model=True)['assessment']['status'], 'needs_review')
        second=self.seed(tid='task-empty')
        self.service.store.write_json(second.id,'result.json',{'valid':True,'report_text':'无数据',
                                                             'report_meta':{'producer':'scopex_no_data'}})
        self.assertEqual(self.service.request_assessment(second.id, allow_model=True)['assessment']['reason'], 'no_data')
        third=self.seed(tid='task-missing-controls')
        (self.root/'tasks'/third.id/'session.json').unlink()
        self.assertEqual(self.service.request_assessment(third.id, allow_model=True)['assessment']['reason'], 'control_context_unavailable')
        self.classifier.assert_not_called()

    def test_duplicate_click_and_delete_do_not_race_live_assessment(self):
        entered=threading.Event(); release=threading.Event()
        def classify(**kwargs):
            entered.set(); release.wait(2)
            return record('normal','当前正文未报告异常',source='manual_text',model_calls=1)
        self.classifier.side_effect=classify
        task=self.seed()
        try:
            self.service.request_assessment(task.id,allow_model=True)
            self.assertTrue(entered.wait(1))
            self.assertEqual(self.service.request_assessment(task.id,allow_model=True)['assessment']['status'],'pending')
            with self.assertRaises(TaskConflictError): self.service.delete_task(task.id)
            other=self.seed(tid='task-other')
            with self.assertRaises(TaskBusyError): self.service.request_assessment(other.id,allow_model=True)
        finally: release.set()
        wait_assessment(self.service,task.id)
        self.classifier.assert_called_once()

    def test_busy_foreground_rejects_manual_without_mutating_label(self):
        task=self.seed()
        self.service._active_ids.add('fixture-active')
        try:
            with self.assertRaises(TaskBusyError): self.service.request_assessment(task.id,allow_model=True)
            self.assertEqual(self.service.get_task(task.id)['assessment']['status'],'not_assessed')
        finally: self.service._active_ids.clear()
        self.classifier.assert_not_called()

    def test_filter_before_pagination_reads_no_reports(self):
        self.seed('task-normal',assessment=record('normal','ok'))
        self.seed('task-abnormal',assessment=record('abnormal','bad'))
        self.seed('task-review',assessment=record('needs_review','unknown'))
        self.seed('task-old')
        with patch.object(self.service,'get_result',side_effect=AssertionError('list must not read report')):
            with TestClient(create_app(self.service)) as client:
                rows=client.get('/tasks?schedule_id=schedule-fixture&assessment_status=abnormal&limit=1&offset=0').json()['tasks']
                self.assertEqual([r['id'] for r in rows],['task-abnormal'])
                self.assertEqual(len(client.get('/tasks?push_decision=suggested').json()['tasks']),1)
                self.assertEqual(client.get('/tasks?assessment_status=bad').status_code,400)
                self.assertEqual(client.get('/tasks?state=UNKNOWN').status_code,400)

    def test_schedule_defaults_toggle_persist_and_apply_only_to_future_runs(self):
        tasks=Mock(); tasks.create_task.return_value={'id':'task-triggered'}
        schedules=ScheduleService(self.root/'scheduler',tasks)
        row=schedules.create(name='CPU',message='任一超过80%',kind='interval',interval_minutes=30)
        self.assertTrue(row['assessment_enabled'])
        schedules.run_now(row['id'])
        self.assertTrue(tasks.create_task.call_args.kwargs['metadata']['assessment_enabled'])
        before=row['next_run_at']
        schedules.set_assessment_enabled(row['id'],False)
        reloaded=ScheduleService(self.root/'scheduler',tasks)
        self.assertFalse(reloaded.get(row['id'])['assessment_enabled'])
        self.assertEqual(reloaded.get(row['id'])['next_run_at'],before)
        reloaded.run_now(row['id'])
        self.assertFalse(tasks.create_task.call_args.kwargs['metadata']['assessment_enabled'])
        self.assertEqual(tasks.create_task.call_args.args[0],'任一超过80%')

    def test_api_strict_flags_and_schedule_toggle(self):
        schedules=ScheduleService(self.root/'scheduler',self.service)
        self.seed(text='正文\n'+footer('normal'))
        with TestClient(create_app(self.service,schedules=schedules)) as client:
            r=client.post('/schedules',json={'name':'x','message':'明确条件','kind':'interval','interval_minutes':30})
            self.assertEqual(r.status_code,201)
            self.assertTrue(r.json()['assessment_enabled'])
            sid=r.json()['id']
            self.assertFalse(client.patch(f'/schedules/{sid}/assessment',json={'assessment_enabled':False}).json()['assessment_enabled'])
            self.assertEqual(client.patch(f'/schedules/{sid}/assessment',json={'assessment_enabled':'false'}).status_code,400)
            self.assertEqual(client.post('/tasks/task-saved/assessment',json={'allow_model':'true'}).status_code,400)
            self.assertEqual(client.post('/tasks/task-saved/assessment',json={}).json()['assessment']['status'],'normal')
        self.classifier.assert_not_called()

    def test_restart_expires_pending_assessment_without_changing_completed_run(self):
        task=self.seed(assessment=record('pending','running',source='manual_text'))
        other=self.seed('task-old')
        second=TaskService(audit_root=self.root/'tasks',coordinator_factory=Mock(),finalizer_factory=Mock(),
                           assessment_classifier=Mock(side_effect=AssertionError('no replay')),reconcile_interrupted=True)
        try:
            row=second.get_task(task.id)
            self.assertEqual(row['state'],'COMPLETED')
            self.assertEqual(row['finished_at'],task.finished_at)
            self.assertEqual(row['assessment']['status'],'needs_review')
            self.assertEqual(second.get_task(other.id)['assessment']['status'],'not_assessed')
        finally: second.shutdown()

    def test_changed_answer_during_manual_call_is_not_published_as_abnormal(self):
        entered=threading.Event(); release=threading.Event()
        def classify(**kwargs):
            entered.set(); release.wait(2)
            return record('abnormal','旧正文异常',source='manual_text',model_calls=1)
        self.classifier.side_effect=classify
        task=self.seed()
        try:
            self.service.request_assessment(task.id,allow_model=True)
            self.assertTrue(entered.wait(1))
            self.service.store.write_json(task.id,'result.json',{'valid':True,'report_text':'正文变更'})
        finally: release.set()
        wait_assessment(self.service,task.id)
        value=self.service.get_task(task.id)['assessment']
        self.assertEqual(value['reason'],'assessment_source_changed')
        self.assertEqual(value['push_decision'],'none')

    def test_legacy_schedule_defaults_on_without_retroactive_runs(self):
        tasks=Mock()
        schedules=ScheduleService(self.root/'scheduler',tasks)
        row=schedules.create(name='x',message='自然语言判据',kind='interval',interval_minutes=30)
        stored=json.loads(schedules.path.read_text())
        # Use the actual on-disk container shape, rather than a fabricated schema.
        for item in stored:
            item.pop('assessment_enabled',None)
        schedules.path.write_text(json.dumps(stored))
        reloaded=ScheduleService(self.root/'scheduler',tasks)
        self.assertTrue(reloaded.get(row['id'])['assessment_enabled'])
        tasks.create_task.assert_not_called()

    def test_explicit_retry_counts_once_per_user_request(self):
        task=self.seed()
        self.service.request_assessment(task.id,allow_model=True)
        wait_assessment(self.service,task.id)
        self.service.request_assessment(task.id,allow_model=True,retry=True)
        wait_assessment(self.service,task.id)
        value=self.service.get_task(task.id)['assessment']
        self.assertEqual(self.classifier.call_count,2)
        self.assertEqual(value['manual_model_calls_total'],2)

    def test_restart_repairs_current_assessment_after_task_summary_write_was_interrupted(self):
        task=self.seed(text='正文\n'+footer())
        original=self.service.store.write_json
        def fail_task_summary(task_id,name,value):
            if name == 'task.json': raise OSError('simulated crash boundary')
            return original(task_id,name,value)
        with patch.object(self.service.store,'write_json',side_effect=fail_task_summary):
            with self.assertRaises(OSError): self.service.request_assessment(task.id)
        current=self.service.store.read_json(task.id,'assessment.json')
        self.assertEqual(current['status'],'abnormal')
        second=TaskService(audit_root=self.root/'tasks',coordinator_factory=Mock(),finalizer_factory=Mock(),
                           reconcile_interrupted=True)
        try:
            visible=second.get_task(task.id)['assessment']
            self.assertEqual(visible['assessment_id'],current['assessment_id'])
            self.assertEqual(visible['status'],'abnormal')
            history=second.store.read_jsonl(task.id,'assessment-history.jsonl')
            self.assertEqual(sum(row.get('assessment_id') == current['assessment_id'] for row in history),1)
        finally: second.shutdown()
        third=TaskService(audit_root=self.root/'tasks',coordinator_factory=Mock(),finalizer_factory=Mock(),
                          reconcile_interrupted=True)
        try:
            history=third.store.read_jsonl(task.id,'assessment-history.jsonl')
            self.assertEqual(sum(row.get('assessment_id') == current['assessment_id'] for row in history),1)
        finally: third.shutdown()

    def test_restart_repairs_history_when_append_was_interrupted(self):
        task=self.seed(text='正文\n'+footer())
        with patch.object(self.service.store,'append_jsonl',side_effect=OSError('simulated history failure')):
            with self.assertRaises(OSError): self.service.request_assessment(task.id)
        current=self.service.store.read_json(task.id,'assessment.json')
        second=TaskService(audit_root=self.root/'tasks',coordinator_factory=Mock(),finalizer_factory=Mock(),
                           reconcile_interrupted=True)
        try:
            visible=second.get_task(task.id)['assessment']
            self.assertEqual(visible['assessment_id'],current['assessment_id'])
            history=second.store.read_jsonl(task.id,'assessment-history.jsonl')
            self.assertEqual([row['assessment_id'] for row in history],[current['assessment_id']])
        finally: second.shutdown()

    def test_manual_storage_failure_preserves_actual_model_attempt(self):
        task=self.seed()
        original=self.service.store.write_json
        writes=0
        def fail_final_assessment_once(task_id,name,value):
            nonlocal writes
            if name == 'assessment.json':
                writes += 1
                if writes == 2: raise OSError('simulated final assessment failure')
            return original(task_id,name,value)
        with patch.object(self.service.store,'write_json',side_effect=fail_final_assessment_once):
            self.service.request_assessment(task.id,allow_model=True)
            value=wait_assessment(self.service,task.id)
        self.assertEqual(self.classifier.call_count,1)
        self.assertEqual(value['source'],'manual_text')
        self.assertEqual(value['model_calls'],1)
        self.assertEqual(value['manual_model_calls_total'],1)
        self.assertEqual(value['reason'],'assessment_storage_error')
