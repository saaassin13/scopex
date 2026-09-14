from __future__ import annotations

from pathlib import Path
import json
import tempfile
import time
import unittest

from scopex.agent.outcome import CliOutcome
from scopex.agent.runtime import OpenClawTurnResult
from scopex.api.service import TaskService
from scopex.evidence.catalog import EvidenceCatalog
from scopex.runtime.controller import TaskController
from scopex.runtime.session import Session
from scopex.runtime.steering import PendingSteeringQueue


class Cleanup:
    container_ids = ()
    warnings = ()


class NoEvidenceCoordinator:
    def __init__(self, task, session, events, audit, *, business_attempt=False):
        self.task = task
        self.session = session
        self.events = events
        self.audit = audit
        self.business_attempt = business_attempt
        self.controller = TaskController(task, session, events)
        self.catalog = EvidenceCatalog(task.id, task.session_key)
        self.steering = PendingSteeringQueue()

    def start(self, message, *, turn_name):
        self.session.user(message)
        self.controller.created()
        self.controller.start()
        self.audit.snapshot_control(self.task, self.session, self.catalog)
        root = self.audit.store.task_dir(self.task.id)
        stdout = root / f'{turn_name}.stdout'
        stderr = root / f'{turn_name}.stderr'
        message_path = root / f'{turn_name}.message'
        stdout.write_text('', encoding='utf-8')
        stderr.write_text('', encoding='utf-8')
        message_path.write_text('', encoding='utf-8')
        if self.business_attempt:
            request = {
                'messages': [{
                    'role': 'assistant',
                    'tool_calls': [{
                        'id': 'call-1',
                        'type': 'function',
                        'function': {
                            'name': 'exec',
                            'arguments': json.dumps({'command': 'python3 encoder_health.py --log-dir /agent-data/logs'}),
                        },
                    }],
                }],
            }
            (root / 'wire-001-request.json').write_text(json.dumps(request), encoding='utf-8')
        process = type('Process', (), {
            'returncode': 0,
            'stop_reason': None,
            'wall_s': 0.01,
            'stdout_path': stdout,
            'stderr_path': stderr,
            'message_path': message_path,
        })()
        outcome = CliOutcome((), (), '当前可用 Skill 有 6 个。', 1, {})
        return OpenClawTurnResult(
            turn_name=turn_name,
            process=process,
            cli_outcome=outcome,
            proxy_records=(),
            audit_dir=root,
        )

    def close(self):
        return Cleanup()


class NoEvidenceFactory:
    def __init__(self, *, business_attempt=False):
        self.business_attempt = business_attempt

    def __call__(self, task, session, events, audit):
        return NoEvidenceCoordinator(task, session, events, audit, business_attempt=self.business_attempt)


def wait_terminal(service: TaskService, task_id: str, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = service.get_task(task_id)
        if row['state'] in {'COMPLETED', 'FAILED', 'CANCELLED'}:
            handle = service._handles.get(task_id)
            if handle is None or not handle.worker_alive:
                return row
        time.sleep(0.01)
    raise AssertionError('task did not become terminal')


class ConversationModeTests(unittest.TestCase):
    def test_conversation_can_publish_normal_answer_without_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            service = TaskService(audit_root=Path(td), coordinator_factory=NoEvidenceFactory(), finalizer_factory=lambda: object())
            created = service.create_task('当前有哪些 Skill', mode='conversation')
            row = wait_terminal(service, created['id'])
            self.assertEqual(row['state'], 'COMPLETED')
            result = service.get_result(created['id'])
            self.assertEqual(result['result']['mode'], 'conversation')
            self.assertEqual(service.get_evidence(created['id'])['items'], [])

    def test_auto_manual_run_resolves_to_conversation_without_business_work(self):
        with tempfile.TemporaryDirectory() as td:
            service = TaskService(audit_root=Path(td), coordinator_factory=NoEvidenceFactory(), finalizer_factory=lambda: object())
            created = service.create_auto_run('当前有哪些 Skill')
            self.assertEqual(created['mode'], 'auto')
            row = wait_terminal(service, created['id'])
            self.assertEqual(row['state'], 'COMPLETED')
            self.assertEqual(row['mode'], 'conversation')
            self.assertEqual(service.get_result(created['id'])['result']['answer_text'], '当前可用 Skill 有 6 个。')

    def test_auto_business_attempt_without_business_evidence_does_not_downgrade_to_chat(self):
        with tempfile.TemporaryDirectory() as td:
            service = TaskService(
                audit_root=Path(td),
                coordinator_factory=NoEvidenceFactory(business_attempt=True),
                finalizer_factory=lambda: object(),
            )
            created = service.create_auto_run('检查3点编码器')
            row = wait_terminal(service, created['id'])
            self.assertEqual(row['state'], 'FAILED')
            self.assertEqual(row['mode'], 'task')
            self.assertEqual(row['last_reason'], 'investigation_completed_without_business_evidence')
            self.assertFalse(service.get_result(created['id'])['available'])

    def test_audited_task_still_fails_without_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            service = TaskService(audit_root=Path(td), coordinator_factory=NoEvidenceFactory(), finalizer_factory=lambda: object())
            created = service.create_task('检查设备异常', mode='task')
            row = wait_terminal(service, created['id'])
            self.assertEqual(row['state'], 'FAILED')
            self.assertEqual(row['last_reason'], 'investigation_completed_without_evidence')


if __name__ == '__main__':
    unittest.main()
