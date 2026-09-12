from __future__ import annotations

import http.client
import json
import threading
import unittest

from scopex.api.http import RuntimeApiServer
from scopex.api.service import TaskBusyError, TaskConflictError, TaskNotFoundError


class StubService:
    def __init__(self):
        self.active_task_id = None
        self.calls = []

    def create_task(self, message):
        self.calls.append(("create", message))
        if message == "busy":
            raise TaskBusyError("active task exists")
        return {"id": "task-1", "state": "CREATED", "user_request": message}

    def list_tasks(self):
        return [{"id": "task-1", "state": "COMPLETED"}]

    def get_task(self, task_id):
        if task_id == "missing":
            raise TaskNotFoundError(task_id)
        return {"id": task_id, "state": "RUNNING"}

    def get_events(self, task_id, *, after=0):
        self.get_task(task_id)
        return [
            {"seq": 2, "task_id": task_id, "type": "MODEL_REQUEST"},
            {"seq": 3, "task_id": task_id, "type": "TOOL_CALL"},
        ] if after < 2 else [
            {"seq": 3, "task_id": task_id, "type": "TOOL_CALL"}
        ]

    def get_evidence(self, task_id):
        self.get_task(task_id)
        return {"task_id": task_id, "items": [{"ref": "E1"}]}

    def get_result(self, task_id):
        self.get_task(task_id)
        return {"task_id": task_id, "available": False}

    def stop(self, task_id, message=""):
        self.calls.append(("stop", task_id, message))
        if task_id == "conflict":
            raise TaskConflictError("stop requires RUNNING")
        return {"id": task_id, "state": "PAUSING"}

    def resume(self, task_id, message):
        self.calls.append(("resume", task_id, message))
        return {"id": task_id, "state": "RUNNING"}

    def steer(self, task_id, message):
        self.calls.append(("steer", task_id, message))
        return {"id": task_id, "state": "RUNNING"}


class RuntimeApiHttpTests(unittest.TestCase):
    def setUp(self):
        self.service = StubService()
        self.server = RuntimeApiServer(("127.0.0.1", 0), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.server_port,
            timeout=3,
        )
        try:
            encoded = None
            headers = {}
            if body is not None:
                encoded = body if isinstance(body, bytes) else json.dumps(body).encode()
                headers["Content-Type"] = "application/json"
            connection.request(method, path, body=encoded, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            return response.status, json.loads(raw)
        finally:
            connection.close()

    def test_health_and_task_reads(self):
        status, health = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")

        status, tasks = self.request("GET", "/tasks")
        self.assertEqual(status, 200)
        self.assertEqual(tasks["tasks"][0]["id"], "task-1")

        status, task = self.request("GET", "/tasks/task-1")
        self.assertEqual(status, 200)
        self.assertEqual(task["state"], "RUNNING")

        status, evidence = self.request("GET", "/tasks/task-1/evidence")
        self.assertEqual(status, 200)
        self.assertEqual(evidence["items"][0]["ref"], "E1")

        status, result = self.request("GET", "/tasks/task-1/result")
        self.assertEqual(status, 200)
        self.assertFalse(result["available"])

    def test_events_support_after_cursor(self):
        status, payload = self.request("GET", "/tasks/task-1/events?after=2")
        self.assertEqual(status, 200)
        self.assertEqual([row["seq"] for row in payload["events"]], [3])
        self.assertEqual(payload["next_after"], 3)

    def test_create_and_controls_return_202(self):
        status, task = self.request("POST", "/tasks", {"message": "diagnose"})
        self.assertEqual(status, 202)
        self.assertEqual(task["id"], "task-1")

        status, stopped = self.request("POST", "/tasks/task-1/stop", {"message": "pause"})
        self.assertEqual(status, 202)
        self.assertEqual(stopped["state"], "PAUSING")

        status, resumed = self.request("POST", "/tasks/task-1/resume", {"message": "continue"})
        self.assertEqual(status, 202)
        self.assertEqual(resumed["state"], "RUNNING")

        status, steered = self.request("POST", "/tasks/task-1/steer", {"message": "check system"})
        self.assertEqual(status, 202)
        self.assertEqual(steered["state"], "RUNNING")

    def test_errors_are_machine_readable(self):
        status, missing = self.request("GET", "/tasks/missing")
        self.assertEqual(status, 404)
        self.assertEqual(missing["error"]["code"], "task_not_found")

        status, busy = self.request("POST", "/tasks", {"message": "busy"})
        self.assertEqual(status, 409)
        self.assertEqual(busy["error"]["code"], "task_busy")

        status, conflict = self.request("POST", "/tasks/conflict/stop", {"message": "x"})
        self.assertEqual(status, 409)
        self.assertEqual(conflict["error"]["code"], "task_conflict")

        status, invalid = self.request(
            "POST",
            "/tasks",
            b'{"message":"a","message":"b"}',
        )
        self.assertEqual(status, 400)
        self.assertEqual(invalid["error"]["code"], "invalid_request")

    def test_non_loopback_bind_is_rejected_before_server_start(self):
        with self.assertRaises(ValueError):
            RuntimeApiServer(("0.0.0.0", 0), self.service)


if __name__ == "__main__":
    unittest.main()
