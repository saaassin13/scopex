from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest

from scopex.agent.outcome import CliOutcome
from scopex.agent.runtime import OpenClawTurnResult
from scopex.api.service import TaskService
from scopex.evidence.catalog import EvidenceCatalog
from scopex.runtime.controller import TaskController
from scopex.runtime.steering import PendingSteeringQueue


class Cleanup:
    container_ids = ()
    warnings = ()


class ConversationCoordinator:
    def __init__(self, task, session, events, audit):
        self.task = task
        self.session = session
        self.events = events
        self.audit = audit
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
        process = type('Process', (), {
            'returncode': 0,
            'stop_reason': None,
            'wall_s': 0.01,
            'stdout_path': stdout,
            'stderr_path': stderr,
            'message_path': message_path,
        })()
        outcome = CliOutcome((), (), f'answer:{message}', 1, {})
        return OpenClawTurnResult(
            turn_name=turn_name,
            process=process,
            cli_outcome=outcome,
            proxy_records=(),
            audit_dir=root,
        )

    def close(self):
        return Cleanup()


class Factory:
    def __call__(self, task, session, events, audit):
        return ConversationCoordinator(task, session, events, audit)


def wait_terminal(service: TaskService, task_id: str, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = service.get_task(task_id)
        handle = service._handles.get(task_id)
        if row['state'] in {'COMPLETED', 'FAILED', 'CANCELLED'} and (handle is None or not handle.worker_alive):
            return row
        time.sleep(0.01)
    raise AssertionError('task did not become terminal')


class ConversationContinuationTests(unittest.TestCase):
    def test_followup_reuses_agent_and_session_but_gets_new_run_id(self):
        with tempfile.TemporaryDirectory() as td:
            service = TaskService(
                audit_root=Path(td),
                coordinator_factory=Factory(),
                finalizer_factory=lambda: object(),
            )
            first = service.create_task('有哪些 Skill', mode='conversation')
            first_done = wait_terminal(service, first['id'])
            second = service.continue_conversation(first['id'], '哪个适合编码器？')
            second_done = wait_terminal(service, second['id'])

            self.assertNotEqual(first_done['id'], second_done['id'])
            self.assertEqual(first_done['session_key'], second_done['session_key'])
            self.assertEqual(first_done['metadata']['agent_id'], second_done['metadata']['agent_id'])
            self.assertEqual(first_done['metadata']['conversation_id'], first_done['id'])
            self.assertEqual(second_done['metadata']['conversation_id'], first_done['id'])
            self.assertEqual(second_done['metadata']['parent_task_id'], first_done['id'])
            self.assertEqual(second_done['mode'], 'conversation')
            self.assertEqual(service.get_result(second_done['id'])['result']['answer_text'], 'answer:哪个适合编码器？')


if __name__ == '__main__':
    unittest.main()
