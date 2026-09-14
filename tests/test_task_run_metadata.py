from __future__ import annotations

import unittest

from scopex.runtime.task import Task, TaskState


class TaskRunMetadataTests(unittest.TestCase):
    def test_task_records_start_finish_duration_and_trigger(self):
        task = Task(
            'task-1',
            '检查当前磁盘',
            'agent:a:task-1',
            mode='task',
            trigger_type='schedule',
            schedule_id='schedule-1',
            scheduled_for='2026-09-14T14:30:00+08:00',
        )
        self.assertIsNone(task.started_at)
        task.transition(TaskState.RUNNING)
        self.assertIsNotNone(task.started_at)
        task.transition(TaskState.COMPLETED)
        snapshot = task.snapshot()
        self.assertIsNotNone(snapshot['finished_at'])
        self.assertIsInstance(snapshot['duration_ms'], int)
        self.assertGreaterEqual(snapshot['duration_ms'], 0)
        self.assertEqual(snapshot['trigger_type'], 'schedule')
        self.assertEqual(snapshot['schedule_id'], 'schedule-1')
        self.assertEqual(snapshot['scheduled_for'], '2026-09-14T14:30:00+08:00')

    def test_conversation_uses_same_task_model(self):
        task = Task('task-2', '有哪些 Skill', 'agent:a:task-2', mode='conversation')
        task.transition(TaskState.RUNNING)
        task.transition(TaskState.COMPLETED)
        self.assertEqual(task.snapshot()['mode'], 'conversation')
        self.assertEqual(task.snapshot()['trigger_type'], 'manual')


if __name__ == '__main__':
    unittest.main()
