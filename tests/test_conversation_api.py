from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from scopex.api.fastapi_app import create_app


class StubService:
    active_task_id = None

    def __init__(self):
        self.calls = []

    def shutdown(self, timeout_s=10.0):
        pass

    def list_tasks(self, *, mode=None):
        return []

    def create_task(self, message, **kwargs):
        return {'id': 'task-first', 'state': 'CREATED', 'mode': kwargs.get('mode', 'task')}

    def continue_conversation(self, task_id, message):
        self.calls.append((task_id, message))
        return {'id': 'task-next', 'state': 'CREATED', 'mode': 'conversation'}

    def get_task(self, task_id):
        return {'id': task_id, 'state': 'COMPLETED'}

    def get_events(self, task_id, *, after=0):
        return []

    def get_evidence(self, task_id):
        return {'task_id': task_id, 'items': []}

    def get_result(self, task_id):
        return {'task_id': task_id, 'available': False}

    def get_evaluation(self, task_id):
        return None

    def set_evaluation(self, task_id, *, rating, tags=None, note=''):
        return {'task_id': task_id, 'rating': rating, 'tags': tags or [], 'note': note}

    def stop(self, task_id, message=''):
        return {'id': task_id, 'state': 'PAUSING'}

    def resume(self, task_id, message):
        return {'id': task_id, 'state': 'RUNNING'}

    def steer(self, task_id, message):
        return {'id': task_id, 'state': 'RUNNING'}


class ConversationApiTests(unittest.TestCase):
    def test_continue_conversation_route_uses_same_task_service(self):
        service = StubService()
        with TestClient(create_app(service)) as client:
            response = client.post('/conversations/task-first/messages', json={'message': '继续解释'})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()['id'], 'task-next')
            self.assertEqual(response.json()['mode'], 'conversation')
            self.assertEqual(service.calls, [('task-first', '继续解释')])


if __name__ == '__main__':
    unittest.main()
