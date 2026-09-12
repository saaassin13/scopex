from pathlib import Path
import tempfile
import unittest

from scopex.agent.outcome import CliOutcome
from scopex.agent.runtime import OpenClawTurnResult
from scopex.evidence.catalog import EvidenceCatalog
from scopex.evidence.collector import EvidenceCollector
from scopex.events.observer import AgentProgressObserver
from scopex.events.progress import InMemoryEventSink
from scopex.runtime.controller import TaskController
from scopex.runtime.convergence import ConvergencePolicy
from scopex.runtime.investigation import InvestigationCoordinator
from scopex.runtime.session import Session
from scopex.runtime.stop import SafeStopGate
from scopex.runtime.task import Task, TaskState


class FakeAgent:
    def __init__(self, task_id, events, root):
        self.observer = AgentProgressObserver(task_id, events)
        self.root = Path(root)
        self.turns = 0

    def run_turn(self, message, *, turn_name):
        self.turns += 1
        audit = self.root / turn_name
        audit.mkdir(parents=True)
        (audit / "wire-01-request.json").write_text(
            '{"messages":[{"role":"user","content":"' + message + '"}]}',
            encoding="utf-8",
        )
        return OpenClawTurnResult(
            turn_name=turn_name,
            process=type("Process", (), {
                "returncode": 0,
                "stop_reason": None,
                "wall_s": 0.01,
                "stdout_path": audit / "stdout",
                "stderr_path": audit / "stderr",
                "message_path": audit / "message",
            })(),
            cli_outcome=CliOutcome((), (), "ok", 1, {}),
            proxy_records=({"forwarded": True},),
            audit_dir=audit,
        )

    def close(self):
        return None


class InvestigationCoordinatorTests(unittest.TestCase):
    def make_coordinator(self, root):
        task = Task("t1", "diagnose", "agent:sx:t1")
        session = Session("t1", "agent:sx:t1")
        events = InMemoryEventSink()
        controller = TaskController(task, session, events)
        catalog = EvidenceCatalog(task.id, task.session_key)
        collector = EvidenceCollector(catalog, events)
        agent = FakeAgent(task.id, events, root)
        return InvestigationCoordinator(
            task=task,
            session=session,
            controller=controller,
            agent=agent,
            stop_gate=SafeStopGate(),
            catalog=catalog,
            collector=collector,
            convergence_policy=ConvergencePolicy(max_stale_rounds=2),
        )

    def test_turn_does_not_mark_stale_before_evidence_checkpoint(self):
        with tempfile.TemporaryDirectory() as td:
            coordinator = self.make_coordinator(td)
            coordinator.start("first", turn_name="turn-1")
            self.assertEqual(coordinator.metrics.stale_rounds, 0)
            coordinator.add_evidence(source="a.log", raw="observed line", tool_call_id="c1")
            self.assertEqual(coordinator.checkpoint_evidence_progress(), 0)
            self.assertEqual(coordinator.metrics.stale_rounds, 0)

    def test_explicit_no_new_evidence_checkpoints_drive_convergence(self):
        with tempfile.TemporaryDirectory() as td:
            coordinator = self.make_coordinator(td)
            coordinator.start("first", turn_name="turn-1")
            self.assertEqual(coordinator.checkpoint_evidence_progress(), 1)
            self.assertEqual(coordinator.checkpoint_evidence_progress(), 2)
            decision = coordinator.convergence()
            self.assertTrue(decision.should_finalize)
            self.assertIn("no_new_evidence", decision.reasons)

    def test_goal_satisfied_can_begin_and_finish_structured_finalization(self):
        with tempfile.TemporaryDirectory() as td:
            coordinator = self.make_coordinator(td)
            coordinator.start("first", turn_name="turn-1")
            evidence = coordinator.add_evidence(source="system.log", raw="worker exited status=137")
            coordinator.checkpoint_evidence_progress()
            decision = coordinator.begin_finalization(goal_satisfied=True)
            self.assertIn("goal_satisfied", decision.reasons)
            payload = {
                "claims": [{
                    "id": "C1",
                    "kind": "fact",
                    "topic": "ignored prose",
                    "evidence_refs": [evidence.ref],
                    "confidence": "high",
                    "scope": "event",
                    "relation": "observed",
                }],
                "summary_claim_ids": ["C1"],
            }
            final = coordinator.finish_finalization(payload)
            self.assertTrue(final.valid, final.errors)
            self.assertEqual(coordinator.task.state, TaskState.COMPLETED)
            self.assertIn("worker exited status=137", final.rendered)


if __name__ == "__main__":
    unittest.main()
