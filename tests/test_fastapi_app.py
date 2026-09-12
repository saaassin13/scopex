from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from scopex.api.fastapi_app import create_app
from scopex.api.service import TaskBusyError, TaskConflictError, TaskNotFoundError


class StubService:
    def __init__(self):
        self.active_task_id = None
        self.calls = []
        self.shutdown_calls = []

    def shutdown(self, timeout_s=10.0):
        self.shutdown_calls.append(timeout_s)

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
        rows = [
            {"seq": 2, "task_id": task_id, "type": "MODEL_REQUEST"},
            {"seq": 3, "task_id": task_id, "type": "TOOL_CALL"},
        ]
        return [row for row in rows if row["seq"] > after]

    def get_evidence(self, task_id):
        self.get_task(task_id)
        return {"task_id": task_id, "items": [{"ref": "E1"}]}

    def get_result(self, task_id):
        self.get_task(task_id)
        return {"task_id": task_id, "available": False}

    def stop(self, task_id, message=""):
        if task_id == "conflict":
            raise TaskConflictError("stop requires RUNNING")
        self.calls.append(("stop", task_id, message))
        return {"id": task_id, "state": "PAUSING"}

    def resume(self, task_id, message):
        self.calls.append(("resume", task_id, message))
        return {"id": task_id, "state": "RUNNING"}

    def steer(self, task_id, message):
        self.calls.append(("steer", task_id, message))
        return {"id": task_id, "state": "RUNNING"}


class FastApiRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.service = StubService()
        self.client_ctx = TestClient(
            create_app(self.service, shutdown_timeout_s=7.5)
        )
        self.client = self.client_ctx.__enter__()

    def tearDown(self):
        self.client_ctx.__exit__(None, None, None)

    def test_health_and_reads(self):
        self.assertEqual(self.client.get("/health").json()["status"], "ok")
        self.assertEqual(self.client.get("/tasks").json()["tasks"][0]["id"], "task-1")
        self.assertEqual(self.client.get("/tasks/task-1").json()["state"], "RUNNING")
        self.assertEqual(
            self.client.get("/tasks/task-1/evidence").json()["items"][0]["ref"],
            "E1",
        )
        self.assertFalse(self.client.get("/tasks/task-1/result").json()["available"])

    def test_events_cursor(self):
        response = self.client.get("/tasks/task-1/events?after=2")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([row["seq"] for row in payload["events"]], [3])
        self.assertEqual(payload["next_after"], 3)

    def test_create_and_controls(self):
        created = self.client.post("/tasks", json={"message": "diagnose"})
        self.assertEqual(created.status_code, 202)
        self.assertEqual(created.json()["id"], "task-1")

        stopped = self.client.post("/tasks/task-1/stop", json={"message": "pause"})
        self.assertEqual(stopped.status_code, 202)
        self.assertEqual(stopped.json()["state"], "PAUSING")

        resumed = self.client.post("/tasks/task-1/resume", json={"message": "continue"})
        self.assertEqual(resumed.status_code, 202)
        self.assertEqual(resumed.json()["state"], "RUNNING")

        steered = self.client.post("/tasks/task-1/steer", json={"message": "check system"})
        self.assertEqual(steered.status_code, 202)
        self.assertEqual(steered.json()["state"], "RUNNING")

    def test_machine_readable_errors_and_validation(self):
        missing = self.client.get("/tasks/missing")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "task_not_found")

        busy = self.client.post("/tasks", json={"message": "busy"})
        self.assertEqual(busy.status_code, 409)
        self.assertEqual(busy.json()["error"]["code"], "task_busy")

        conflict = self.client.post("/tasks/conflict/stop", json={"message": "x"})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "task_conflict")

        invalid = self.client.post("/tasks", json={"message": "", "extra": True})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["error"]["code"], "invalid_request")

        invalid_cursor = self.client.get("/tasks/task-1/events?after=-1")
        self.assertEqual(invalid_cursor.status_code, 400)

    def test_duplicate_json_key_and_large_body_are_rejected(self):
        duplicate = self.client.post(
            "/tasks",
            content=b'{"message":"a","message":"b"}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(duplicate.json()["error"]["code"], "invalid_request")

        large = self.client.post(
            "/tasks",
            content=b'{"message":"' + (b"x" * 70000) + b'"}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(large.status_code, 413)
        self.assertEqual(large.json()["error"]["code"], "request_too_large")

    def test_lifespan_shutdowns_service(self):
        self.assertEqual(self.service.shutdown_calls, [])
        self.client_ctx.__exit__(None, None, None)
        self.assertEqual(self.service.shutdown_calls, [7.5])
        self.client_ctx = TestClient(create_app(self.service))
        self.client = self.client_ctx.__enter__()


if __name__ == "__main__":
    unittest.main()
