"""Concurrency correctness with controlled stubs, not GPU speed benchmarks."""
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock
from fastapi.testclient import TestClient

from scopex.api.service import TaskService, TaskBusyError, TaskConflictError
from scopex.api.fastapi_app import create_app
from scopex.runtime.task import TaskState
from test_runtime_api_service import FakeFactory, wait_state


class ParallelAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.factory = FakeFactory(block_initial=True, block_finalizer=True)
        self.service = TaskService(audit_root=Path(self.tmp.name)/'tasks', coordinator_factory=self.factory,
                                   finalizer_factory=lambda: object(), max_active_tasks=2, max_queued_tasks=2)

    def tearDown(self):
        for coord in self.factory.coordinators:
            coord.release.set(); coord.finalizer_release.set()
        self.service.shutdown(timeout_s=5)
        self.tmp.cleanup()

    def start(self, name, **kwargs):
        return self.service.create_task(name, **kwargs)['id']

    def release(self, task_id):
        self.service._handles[task_id].coordinator.release.set()
        self.service._handles[task_id].coordinator.finalizer_release.set()

    def test_two_actual_workers_overlap_third_waits_without_factory(self):
        a,b,c = [self.start(name) for name in ['a','b','c']]
        wait_state(self.service,a,'RUNNING'); wait_state(self.service,b,'RUNNING')
        self.assertEqual(self.service.get_task(c)['state'], 'QUEUED')
        self.assertEqual(len(self.factory.coordinators),2)
        self.assertIsNone(self.service._handles[c].coordinator)
        self.assertIsNone(self.service.get_task(c)['started_at'])
        self.release(a); wait_state(self.service,a,'COMPLETED')
        wait_state(self.service,c,'RUNNING')
        self.assertEqual(self.service.get_task(b)['state'],'RUNNING')
        self.assertIsNotNone(self.service.get_task(c)['queue_wait_ms'])

    def test_reports_can_overlap_without_a_global_model_lock(self):
        a,b = self.start('a'),self.start('b')
        wait_state(self.service,a,'RUNNING');wait_state(self.service,b,'RUNNING')
        self.service._handles[a].coordinator.release.set()
        self.service._handles[b].coordinator.release.set()
        wait_state(self.service,a,'FINALIZING');wait_state(self.service,b,'FINALIZING')
        self.assertEqual(self.service.activity()['running_count'],2)
        self.assertTrue(self.service._handles[a].worker_alive)
        self.assertTrue(self.service._handles[b].worker_alive)

    def test_cancel_queued_never_builds_or_cleans_runtime(self):
        a,b,c = [self.start(name) for name in ['a','b','c']]
        wait_state(self.service,a,'RUNNING');wait_state(self.service,b,'RUNNING')
        self.service.cancel_queued(c)
        self.assertEqual(self.service.get_task(c)['state'],'CANCELLED')
        self.assertIsNone(self.service._handles[c].coordinator)
        self.assertEqual(len(self.factory.coordinators),2)
        self.assertEqual(self.service.activity()['queued_count'],0)
        self.assertEqual(self.service.get_task(a)['state'],'RUNNING')

    def test_queue_capacity_rejects_without_creating_extra_run(self):
        for name in ['a','b','c','d']: self.start(name)
        with self.assertRaises(TaskBusyError): self.start('e')
        self.assertEqual(len(self.service.list_tasks()),4)

    def test_queue_timeout_expires_without_tools(self):
        a,b,c = [self.start(name) for name in ['a','b','c']]
        self.service._handles[c].queued_at -= 601
        with self.service._lock: self.service._dispatch_locked()
        self.assertEqual(self.service.get_task(c)['last_reason'],'queue_expired')
        self.assertIsNone(self.service._handles[c].coordinator)

    def test_same_schedule_not_duplicated_but_other_schedule_admitted(self):
        self.start('s1',trigger_type='schedule',schedule_id='s1')
        with self.assertRaises(TaskBusyError): self.start('s1again',trigger_type='schedule',schedule_id='s1')
        b = self.start('s2',trigger_type='schedule',schedule_id='s2')
        self.assertIn(self.service.get_task(b)['state'],{'CREATED','RUNNING'})

    def test_same_agent_never_runs_or_cleans_another_live_session(self):
        self.start('a',agent_id='sxshared')
        with self.assertRaises(TaskConflictError): self.start('b',agent_id='sxshared')

    def test_setup_failure_releases_slot_and_queue_continues(self):
        a,b,c = [self.start(name) for name in ['a','b','c']]
        wait_state(self.service,a,'RUNNING');wait_state(self.service,b,'RUNNING')
        original = self.service.coordinator_factory
        def factory(task,*args):
            if task.id == c: raise ValueError('fixture setup failure')
            return original(task,*args)
        self.service.coordinator_factory = factory
        self.release(a); wait_state(self.service,a,'COMPLETED');wait_state(self.service,c,'FAILED')
        d = self.start('d'); wait_state(self.service,d,'RUNNING')
        self.assertEqual(self.service.get_task(b)['state'],'RUNNING')

    def test_activity_is_global_and_does_not_scan_history_or_evidence(self):
        a,b,c = [self.start(name) for name in ['a','b','c']]
        self.service.store.list_task_ids = Mock(side_effect=AssertionError('no history scan'))
        self.service.get_evidence = Mock(side_effect=AssertionError('no evidence scan'))
        value = self.service.activity()
        self.assertEqual({x['id'] for x in value['tasks']},{a,b,c})
        self.assertEqual(value['queued_count'],1)
        self.assertEqual(value['running_count'],2)

    def test_activity_and_cancel_queue_routes(self):
        a,b,c = [self.start(name) for name in ['a','b','c']]
        client=TestClient(create_app(self.service))
        self.assertEqual(client.get('/activity').json()['queued_count'],1)
        self.assertEqual(client.post(f'/tasks/{c}/cancel-queued',json={}).json()['state'],'CANCELLED')
        self.assertEqual(client.post(f'/tasks/{a}/cancel-queued',json={}).status_code,409)

    def test_shutdown_does_not_launch_waiting_tasks(self):
        a,b,c = [self.start(name) for name in ['a','b','c']]
        self.service.shutdown(timeout_s=5)
        self.assertEqual(self.service.get_task(c)['state'],'CANCELLED')
        self.assertIsNone(self.service._handles[c].coordinator)
        with self.assertRaises(TaskBusyError): self.start('after-shutdown')

    def test_startup_marks_interrupted_records_without_replay(self):
        root=Path(self.tmp.name)/'other'
        root.mkdir()
        from scopex.storage.audit import AuditStore
        store=AuditStore(root)
        for i,state in enumerate(['RUNNING','QUEUED','FINALIZING','COMPLETED']):
            store.write_json(f't-{i}','task.json',{'id':f't-{i}','state':state,'metadata':{},'created_at':'2026-01-01T00:00:00+00:00'})
        factory=Mock(side_effect=AssertionError('no automatic replay'))
        other=TaskService(audit_root=root,coordinator_factory=factory,finalizer_factory=lambda:object(),reconcile_interrupted=True)
        self.assertEqual(other.get_task('t-0')['last_reason'],'interrupted_on_restart')
        self.assertEqual(other.get_task('t-1')['last_reason'],'missed_on_restart')
        self.assertEqual(other.get_task('t-3')['state'],'COMPLETED')
        self.assertEqual(other.activity()['tasks'],[])
        factory.assert_not_called()
