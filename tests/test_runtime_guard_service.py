from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest

from scopex.agent.outcome import NATIVE_TOOL_LOOP_GUARD
from scopex.agent.runtime import OpenClawTurnResult
from scopex.api.service import TaskService
from scopex.evidence.catalog import EvidenceCatalog
from scopex.runtime.controller import TaskController
from scopex.runtime.session import Session
from scopex.runtime.steering import PendingSteeringQueue
from scopex.runtime.task import TaskState


class _Cleanup:
    container_ids = ()
    warnings = ()


class GuardCoordinator:
    def __init__(self, task, session, events, audit, *, with_evidence):
        self.task = task
        self.session = session
        self.events = events
        self.audit = audit
        self.controller = TaskController(task, session, events)
        self.catalog = EvidenceCatalog(task.id, task.session_key)
        self.steering = PendingSteeringQueue()
        self.with_evidence = with_evidence
        self.finalization_reasons = ()

    def start(self, message, *, turn_name):
        self.session.user(message)
        self.controller.created()
        self.controller.start()
        if self.with_evidence:
            self.catalog.add(
                source="/agent-data/marker.txt",
                raw="STATIC-SOURCE-FACT",
                metadata={"line_number": 1},
            )
            self.audit.persist_evidence(self.catalog)
        root = self.audit.store.task_dir(self.task.id)
        stdout = root / f"{turn_name}.stdout"
        stderr = root / f"{turn_name}.stderr"
        message_path = root / f"{turn_name}.message"
        for path in (stdout, stderr, message_path):
            path.write_text("", encoding="utf-8")
        process = type(
            "Process",
            (),
            {
                "returncode": 1,
                "stop_reason": None,
                "wall_s": 0.01,
                "stdout_path": stdout,
                "stderr_path": stderr,
                "message_path": message_path,
            },
        )()
        return OpenClawTurnResult(
            turn_name=turn_name,
            process=process,
            cli_outcome=None,
            proxy_records=({"forwarded": True},),
            audit_dir=root,
            runtime_guard_reason=NATIVE_TOOL_LOOP_GUARD,
        )

    def begin_runtime_guard_finalization(self, reason):
        self.finalization_reasons = ("runtime_guard_reached", reason)
        self.controller.begin_finalization(reasons=self.finalization_reasons)

    def finish_fresh_finalization(self, _finalizer):
        self.audit.persist_result(
            {
                "valid": True,
                "errors": [],
                "investigation_reasons": list(self.finalization_reasons),
                "task_state": TaskState.COMPLETED.value,
            },
            rendered="source-only final result",
        )
        self.controller.finalization_completed()
        self.controller.complete()
        self.audit.snapshot_control(self.task, self.session, self.catalog)
        return object()

    def close(self):
        return _Cleanup()


class GuardFactory:
    def __init__(self, *, with_evidence=True):
        self.with_evidence = with_evidence

    def __call__(self, task, session, events, audit):
        return GuardCoordinator(
            task,
            session,
            events,
            audit,
            with_evidence=self.with_evidence,
        )


def wait_terminal(service, task_id, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = service.get_task(task_id)
        if row["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            handle = service._handles[task_id]
            while handle.worker_alive and time.monotonic() < deadline:
                time.sleep(0.01)
            return service.get_task(task_id)
        time.sleep(0.01)
    raise AssertionError("task did not become terminal")


class RuntimeGuardServiceTests(unittest.TestCase):
    def test_native_loop_terminal_with_evidence_finalizes_current_facts(self):
        with tempfile.TemporaryDirectory() as td:
            service = TaskService(
                audit_root=Path(td),
                coordinator_factory=GuardFactory(with_evidence=True),
                finalizer_factory=lambda: object(),
            )
            task = service.create_task("repeat until native guard")
            terminal = wait_terminal(service, task["id"])
            self.assertEqual(terminal["state"], "COMPLETED")
            result = service.get_result(task["id"])
            self.assertEqual(
                result["result"]["investigation_reasons"],
                ["runtime_guard_reached", NATIVE_TOOL_LOOP_GUARD],
            )
            guard = service.store.read_json(task["id"], "runtime-guard.json")
            self.assertEqual(guard["reason"], NATIVE_TOOL_LOOP_GUARD)
            self.assertEqual(result["rendered"], "source-only final result")

    def test_native_loop_terminal_without_evidence_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as td:
            service = TaskService(
                audit_root=Path(td),
                coordinator_factory=GuardFactory(with_evidence=False),
                finalizer_factory=lambda: object(),
            )
            task = service.create_task("guard before evidence")
            terminal = wait_terminal(service, task["id"])
            self.assertEqual(terminal["state"], "FAILED")
            self.assertEqual(
                terminal["last_reason"],
                "runtime_guard_reached_without_evidence",
            )
            guard = service.store.read_json(task["id"], "runtime-guard.json")
            self.assertEqual(guard["reason"], NATIVE_TOOL_LOOP_GUARD)
            self.assertFalse(service.get_result(task["id"])["available"])


if __name__ == "__main__":
    unittest.main()
