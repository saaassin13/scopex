from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tempfile
import threading
import time
import unittest

from scopex.agent.outcome import CliOutcome
from scopex.agent.runtime import OpenClawTurnResult
from scopex.api.service import TaskBusyError, TaskConflictError, TaskService
from scopex.evidence.catalog import EvidenceCatalog
from scopex.runtime.controller import TaskController
from scopex.runtime.session import Session
from scopex.runtime.steering import PendingSteeringQueue
from scopex.runtime.task import TaskState


@dataclass(frozen=True)
class FakeCleanup:
    container_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class FakeCoordinator:
    def __init__(
        self,
        task,
        session,
        events,
        audit,
        *,
        block_initial=False,
        block_finalizer=False,
        normal_turn=True,
    ):
        self.task = task
        self.session = session
        self.events = events
        self.audit = audit
        self.controller = TaskController(task, session, events)
        self.catalog = EvidenceCatalog(task.id, task.session_key)
        self.steering = PendingSteeringQueue()
        self.block_initial = block_initial
        self.block_finalizer = block_finalizer
        self.normal_turn = normal_turn
        self.release = threading.Event()
        self.finalizer_release = threading.Event()
        self.closed = False
        self.messages = []

    def _turn_result(self, turn_name):
        root = self.audit.store.task_dir(self.task.id)
        stdout = root / f"{turn_name}.stdout"
        stderr = root / f"{turn_name}.stderr"
        message = root / f"{turn_name}.message"
        for path in (stdout, stderr, message):
            path.write_text("", encoding="utf-8")
        process = type("Process", (), {
            "returncode": 0 if self.normal_turn else 1,
            "stop_reason": None,
            "wall_s": 0.01,
            "stdout_path": stdout,
            "stderr_path": stderr,
            "message_path": message,
        })()
        outcome = CliOutcome((), (), "done", 1, {}) if self.normal_turn else None
        return OpenClawTurnResult(
            turn_name=turn_name,
            process=process,
            cli_outcome=outcome,
            proxy_records=(),
            audit_dir=root,
        )

    def start(self, message, *, turn_name):
        self.messages.append((turn_name, message))
        self.session.user(message)
        self.controller.created()
        self.controller.start()
        self.audit.snapshot_control(self.task, self.session, self.catalog)
        if self.block_initial:
            self.release.wait(timeout=2)
        if self.controller.state is TaskState.RUNNING and not self.steering.pending:
            self.catalog.add(
                source="/agent/app.log",
                raw="task failed target_pose_unavailable",
                metadata={"line_number": 1},
            )
            self.audit.persist_evidence(self.catalog)
        return self._turn_result(turn_name)

    def request_stop(self, message=""):
        self.controller.request_stop(message)
        self.controller.safe_stop(before_model_request=2, running_tool_cancelled=False)
        self.audit.snapshot_control(self.task, self.session, self.catalog)
        self.release.set()

    def request_steer(self, message):
        self.steering.push(message)
        self.controller.steer(message)
        self.release.set()

    def continue_pending_steering(self, *, turn_name):
        message = self.steering.drain_message()
        if message is None:
            raise ValueError("no pending steering")
        self.messages.append((turn_name, message))
        self.catalog.add(
            source="/agent/system.log",
            raw="inference-worker exited status=137",
            metadata={"line_number": 3},
        )
        self.audit.persist_evidence(self.catalog)
        return self._turn_result(turn_name)

    def resume(self, message, *, turn_name):
        self.messages.append((turn_name, message))
        self.controller.resume(message)
        self.catalog.add(
            source="/agent/robot.log",
            raw="joint_fault_code=0",
            metadata={"line_number": 4},
        )
        self.audit.persist_evidence(self.catalog)
        return self._turn_result(turn_name)

    def convergence(self, *, goal_satisfied=False):
        return type("Decision", (), {
            "should_finalize": bool(goal_satisfied),
            "reasons": ("goal_satisfied",) if goal_satisfied else (),
        })()

    def begin_finalization(self, *, goal_satisfied=False):
        self.controller.begin_finalization(
            reasons=("goal_satisfied",) if goal_satisfied else ()
        )

    def finish_fresh_finalization(self, _finalizer):
        if self.controller.state is not TaskState.FINALIZING:
            raise ValueError("task must be FINALIZING")
        if self.block_finalizer:
            self.finalizer_release.wait(timeout=2)

        # Mirror the production publication contract: result must exist before
        # FINALIZATION_COMPLETED/TASK_COMPLETED become externally visible.
        self.audit.persist_result(
            {"valid": True, "errors": [], "task_state": TaskState.COMPLETED.value},
            rendered="final result",
        )
        self.controller.finalization_completed()
        self.controller.complete()
        self.audit.snapshot_control(self.task, self.session, self.catalog)
        return object()

    def finalize_fresh(self, finalizer, *, goal_satisfied=False):
        self.begin_finalization(goal_satisfied=goal_satisfied)
        return self.finish_fresh_finalization(finalizer)

    def close(self):
        self.closed = True
        return FakeCleanup()


class FakeFactory:
    def __init__(
        self,
        *,
        block_initial=False,
        block_finalizer=False,
        normal_turn=True,
    ):
        self.block_initial = block_initial
        self.block_finalizer = block_finalizer
        self.normal_turn = normal_turn
        self.coordinators = []

    def __call__(self, task, session, events, audit):
        coordinator = FakeCoordinator(
            task,
            session,
            events,
            audit,
            block_initial=self.block_initial,
            block_finalizer=self.block_finalizer,
            normal_turn=self.normal_turn,
        )
        self.coordinators.append(coordinator)
        return coordinator


def wait_state(service, task_id, state, timeout=2.0):
    deadline = time.monotonic() + timeout
    terminal_states = {"COMPLETED", "FAILED", "CANCELLED"}
    while time.monotonic() < deadline:
        current = service.get_task(task_id)["state"]
        if current == state:
            if state in terminal_states:
                handle = service._handles.get(task_id)
                while handle is not None and handle.worker_alive:
                    if time.monotonic() >= deadline:
                        raise AssertionError(
                            f"task {task_id} reached {state} but worker did not quiesce"
                        )
                    time.sleep(0.01)
            return
        time.sleep(0.01)
    raise AssertionError(f"task {task_id} did not reach {state}")


class RuntimeApiServiceTests(unittest.TestCase):
    def test_task_runs_to_completion_and_is_queryable_from_audit(self):
        with tempfile.TemporaryDirectory() as td:
            factory = FakeFactory()
            service = TaskService(
                audit_root=Path(td),
                coordinator_factory=factory,
                finalizer_factory=lambda: object(),
            )
            task = service.create_task("diagnose")
            task_id = task["id"]
            wait_state(service, task_id, "COMPLETED")
            result = service.get_result(task_id)
            self.assertTrue(result["available"])
            self.assertEqual(result["rendered"], "final result")
            evidence = service.get_evidence(task_id)
            self.assertEqual(len(evidence["items"]), 1)
            events = service.get_events(task_id)
            self.assertEqual(events[-1]["type"], "TASK_COMPLETED")
            self.assertIsNone(service.active_task_id)

    def test_abnormal_turn_with_evidence_does_not_auto_finalize(self):
        with tempfile.TemporaryDirectory() as td:
            service = TaskService(
                audit_root=Path(td),
                coordinator_factory=FakeFactory(normal_turn=False),
                finalizer_factory=lambda: object(),
            )
            task = service.create_task("diagnose")
            wait_state(service, task["id"], "FAILED")
            result = service.get_result(task["id"])
            self.assertFalse(result["available"])
            error = service.store.read_json(task["id"], "investigation-error.json")
            self.assertEqual(error["returncode"], 1)
            self.assertIn("missing_cli_outcome", error["cli_blockers"])

    def test_paused_task_keeps_single_task_slot_until_resume_completes(self):
        with tempfile.TemporaryDirectory() as td:
            factory = FakeFactory(block_initial=True)
            service = TaskService(
                audit_root=Path(td),
                coordinator_factory=factory,
                finalizer_factory=lambda: object(),
            )
            first = service.create_task("first")
            first_id = first["id"]
            wait_state(service, first_id, "RUNNING")
            with self.assertRaises(TaskBusyError):
                service.create_task("second")

            service.stop(first_id, "pause now")
            wait_state(service, first_id, "PAUSED")
            deadline = time.monotonic() + 2
            while service._handles[first_id].worker_alive:
                if time.monotonic() >= deadline:
                    self.fail("paused worker did not unwind")
                time.sleep(0.01)
            with self.assertRaises(TaskBusyError):
                service.create_task("second")

            service.resume(first_id, "continue")
            wait_state(service, first_id, "COMPLETED")
            factory.block_initial = False
            second = service.create_task("second")
            wait_state(service, second["id"], "COMPLETED")

    def test_steer_runs_as_same_task_next_turn(self):
        with tempfile.TemporaryDirectory() as td:
            factory = FakeFactory(block_initial=True)
            service = TaskService(
                audit_root=Path(td),
                coordinator_factory=factory,
                finalizer_factory=lambda: object(),
            )
            task = service.create_task("initial")
            task_id = task["id"]
            wait_state(service, task_id, "RUNNING")
            service.steer(task_id, "先查 system.log")
            wait_state(service, task_id, "COMPLETED")
            coordinator = factory.coordinators[0]
            self.assertEqual(len(coordinator.messages), 2)
            self.assertIn("先查 system.log", coordinator.messages[1][1])
            events = service.get_events(task_id)
            self.assertTrue(any(row["type"] == "USER_STEER" for row in events))

    def test_controls_are_rejected_after_finalization_claims_state(self):
        with tempfile.TemporaryDirectory() as td:
            factory = FakeFactory(block_finalizer=True)
            service = TaskService(
                audit_root=Path(td),
                coordinator_factory=factory,
                finalizer_factory=lambda: object(),
            )
            task = service.create_task("diagnose")
            task_id = task["id"]
            wait_state(service, task_id, "FINALIZING")
            with self.assertRaises(TaskConflictError):
                service.steer(task_id, "too late")
            with self.assertRaises(TaskConflictError):
                service.stop(task_id, "too late")
            factory.coordinators[0].finalizer_release.set()
            wait_state(service, task_id, "COMPLETED")

    def test_historical_task_is_read_only_after_service_restart(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first_factory = FakeFactory()
            first = TaskService(
                audit_root=root,
                coordinator_factory=first_factory,
                finalizer_factory=lambda: object(),
            )
            task = first.create_task("diagnose")
            wait_state(first, task["id"], "COMPLETED")
            first.shutdown()

            restarted = TaskService(
                audit_root=root,
                coordinator_factory=FakeFactory(),
                finalizer_factory=lambda: object(),
            )
            self.assertEqual(restarted.get_task(task["id"])["state"], "COMPLETED")
            with self.assertRaises(TaskConflictError):
                restarted.stop(task["id"])
            restarted.shutdown()

    def test_events_after_filter_is_incremental(self):
        with tempfile.TemporaryDirectory() as td:
            service = TaskService(
                audit_root=Path(td),
                coordinator_factory=FakeFactory(),
                finalizer_factory=lambda: object(),
            )
            task = service.create_task("diagnose")
            wait_state(service, task["id"], "COMPLETED")
            all_events = service.get_events(task["id"])
            self.assertGreaterEqual(len(all_events), 4)
            cutoff = all_events[1]["seq"]
            newer = service.get_events(task["id"], after=cutoff)
            self.assertTrue(all(row["seq"] > cutoff for row in newer))


if __name__ == "__main__":
    unittest.main()
