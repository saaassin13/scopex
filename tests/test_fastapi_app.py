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

    def create_auto_run(self, message):
        self.calls.append(("auto", message))
        if message == "busy":
            raise TaskBusyError("active task exists")
        return {"id": "task-auto", "state": "CREATED", "user_request": message, "mode": "auto", "trigger_type": "manual"}

    def create_task(self, message, **kwargs):
        self.calls.append(("create", message, kwargs))
        if message == "busy":
            raise TaskBusyError("active task exists")
        return {
            "id": "task-1", "state": "CREATED", "user_request": message,
            "mode": kwargs.get("mode", "task"), "trigger_type": kwargs.get("trigger_type", "manual"),
        }

    def continue_conversation(self, task_id, message):
        self.calls.append(("continue", task_id, message))
        return {"id": "task-next", "state": "CREATED", "mode": "conversation"}

    def list_tasks(self, *, mode=None, day=None, schedule_id=None, limit=None, offset=0):
        self.calls.append(("list", mode, day))
        return [{"id": "task-1", "state": "COMPLETED", "mode": mode or "task"}]

    def calendar_month(self, month):
        return {"month": month, "days": [{"date": month + "-14", "count": 2, "completed": 1, "failed": 1, "running": 0, "scheduled": 1, "manual": 1}]}

    def get_task(self, task_id):
        if task_id == "missing":
            raise TaskNotFoundError(task_id)
        return {"id": task_id, "state": "RUNNING"}

    def delete_task(self, task_id):
        if task_id == "conflict":
            raise TaskConflictError("only terminal tasks can be deleted")
        self.calls.append(("delete", task_id))
        return {"task_id": task_id, "deleted": True, "external_business_data_deleted": False}

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

    def get_evaluation(self, task_id):
        self.get_task(task_id)
        return None

    def set_evaluation(self, task_id, *, rating, tags=None, note=""):
        self.get_task(task_id)
        return {"task_id": task_id, "rating": rating, "tags": tags or [], "note": note}

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


class StubSchedules:
    def __init__(self):
        self.started = 0
        self.stopped = 0
        self.items = []

    def start(self): self.started += 1
    def shutdown(self): self.stopped += 1
    def list(self): return list(self.items)
    def create(self, **kwargs):
        row = {"id": "schedule-1", **kwargs, "next_run_at": "2026-09-14T15:00:00+08:00"}
        self.items.append(row)
        return row
    def set_enabled(self, schedule_id, enabled): return {"id": schedule_id, "enabled": enabled}
    def run_now(self, schedule_id): return {"schedule_id": schedule_id, "status": "TRIGGERED", "task_id": "task-1"}
    def delete(self, schedule_id): self.items = [row for row in self.items if row.get("id") != schedule_id]


class FastApiRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.service = StubService()
        self.schedules = StubSchedules()
        self.client_ctx = TestClient(create_app(self.service, schedules=self.schedules, shutdown_timeout_s=7.5))
        self.client = self.client_ctx.__enter__()

    def tearDown(self):
        self.client_ctx.__exit__(None, None, None)

    def test_health_reads_calendar_and_day_filter(self):
        self.assertEqual(self.client.get("/health").json()["status"], "ok")
        self.assertEqual(self.client.get("/tasks?day=2026-09-14").json()["tasks"][0]["id"], "task-1")
        self.assertIn(("list", None, "2026-09-14"), self.service.calls)
        calendar = self.client.get('/tasks/calendar?month=2026-09').json()
        self.assertEqual(calendar['days'][0]['count'], 2)
        self.assertEqual(self.client.get("/tasks/task-1").json()["state"], "RUNNING")
        self.assertEqual(self.client.get("/tasks/task-1/evidence").json()["items"][0]["ref"], "E1")
        self.assertFalse(self.client.get("/tasks/task-1/result").json()["available"])

    def test_unified_run_and_compatibility_routes(self):
        run = self.client.post('/runs', json={'message': '检查3点编码器'})
        self.assertEqual(run.status_code, 202)
        self.assertEqual(run.json()['mode'], 'auto')
        self.assertIn(("auto", "检查3点编码器"), self.service.calls)

        created = self.client.post("/tasks", json={"message": "diagnose"})
        self.assertEqual(created.status_code, 202)
        self.assertEqual(created.json()["mode"], "task")
        chat = self.client.post("/conversations", json={"message": "what skills are available"})
        self.assertEqual(chat.status_code, 202)
        self.assertEqual(chat.json()["mode"], "conversation")

    def test_delete_task_route_never_implies_business_data_delete(self):
        response = self.client.delete('/tasks/task-1')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        self.assertFalse(response.json()['external_business_data_deleted'])

    def test_events_cursor(self):
        response = self.client.get("/tasks/task-1/events?after=2")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([row["seq"] for row in payload["events"]], [3])
        self.assertEqual(payload["next_after"], 3)

    def test_controls(self):
        stopped = self.client.post("/tasks/task-1/stop", json={"message": "pause"})
        self.assertEqual(stopped.status_code, 202)
        resumed = self.client.post("/tasks/task-1/resume", json={"message": "continue"})
        self.assertEqual(resumed.status_code, 202)
        steered = self.client.post("/tasks/task-1/steer", json={"message": "check system"})
        self.assertEqual(steered.status_code, 202)

    def test_evaluation(self):
        response = self.client.post("/tasks/task-1/evaluation", json={"rating": "down", "tags": ["hard_to_read"], "note": "too mechanical"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rating"], "down")

    def test_schedule_routes(self):
        self.assertEqual(self.schedules.started, 1)
        created = self.client.post('/schedules', json={
            'name': 'encoder', 'message': '检查过去30分钟编码器', 'kind': 'interval', 'interval_minutes': 30,
        })
        self.assertEqual(created.status_code, 201)
        self.assertEqual(len(self.client.get('/schedules').json()['schedules']), 1)
        run = self.client.post('/schedules/schedule-1/run', json={})
        self.assertEqual(run.status_code, 202)
        disabled = self.client.patch('/schedules/schedule-1/enabled', json={'enabled': False})
        self.assertFalse(disabled.json()['enabled'])
        self.assertEqual(self.client.delete('/schedules/schedule-1').status_code, 204)

    def test_machine_readable_errors_and_validation(self):
        missing = self.client.get("/tasks/missing")
        self.assertEqual(missing.status_code, 404)
        busy = self.client.post("/tasks", json={"message": "busy"})
        self.assertEqual(busy.status_code, 409)
        conflict = self.client.post("/tasks/conflict/stop", json={"message": "x"})
        self.assertEqual(conflict.status_code, 409)
        invalid = self.client.post("/tasks", json={"message": "", "extra": True})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(self.client.get("/tasks/task-1/events?after=-1").status_code, 400)
        self.assertEqual(self.client.get('/tasks/calendar?month=bad').status_code, 400)

    def test_duplicate_json_key_and_large_body_are_rejected(self):
        duplicate = self.client.post("/runs", content=b'{"message":"a","message":"b"}', headers={"Content-Type": "application/json"})
        self.assertEqual(duplicate.status_code, 400)
        large = self.client.post("/runs", content=b'{"message":"' + (b"x" * 70000) + b'"}', headers={"Content-Type": "application/json"})
        self.assertEqual(large.status_code, 413)

    def test_lifespan_shutdowns_service_and_scheduler(self):
        self.client_ctx.__exit__(None, None, None)
        self.assertEqual(self.service.shutdown_calls, [7.5])
        self.assertEqual(self.schedules.stopped, 1)
        self.client_ctx = TestClient(create_app(self.service, schedules=self.schedules))
        self.client = self.client_ctx.__enter__()


if __name__ == "__main__":
    unittest.main()
