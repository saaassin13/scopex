import json
from pathlib import Path
import tempfile
import unittest

from scopex.agent.outcome import CliOutcome
from scopex.agent.runtime import OpenClawTurnResult
from scopex.evidence.catalog import EvidenceCatalog
from scopex.evidence.collector import EvidenceCollector
from scopex.evidence.extractor import EvidenceExtractionPipeline, ReadResultExtractor
from scopex.events.observer import AgentProgressObserver
from scopex.events.progress import InMemoryEventSink
from scopex.runtime.controller import TaskController
from scopex.runtime.convergence import ConvergencePolicy
from scopex.runtime.investigation import InvestigationCoordinator
from scopex.runtime.session import Session
from scopex.runtime.stop import SafeStopGate
from scopex.runtime.task import Task, TaskState
from scopex.storage.audit import AuditStore
from scopex.storage.runtime_audit import RuntimeAudit


class EvidenceFakeAgent:
    def __init__(self, task_id, events, root):
        self.observer = AgentProgressObserver(task_id, events)
        self.root = Path(root)

    def run_turn(self, message, *, turn_name):
        audit = self.root / turn_name
        audit.mkdir(parents=True)
        payload = {
            "messages": [
                {"role": "user", "content": message},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "read",
                        "arguments": '{"path":"/agent/system.log"}',
                    },
                }]},
                {"role": "tool", "tool_call_id": "c1", "content": "worker exited status=137"},
            ]
        }
        (audit / "wire-01-request.json").write_text(json.dumps(payload), encoding="utf-8")
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


class InvestigationAuditExtractionTests(unittest.TestCase):
    def test_turn_extracts_evidence_and_persists_full_task_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events = InMemoryEventSink()
            task = Task("task-1", "diagnose", "agent:sx:task-1")
            session = Session(task.id, task.session_key)
            controller = TaskController(task, session, events)
            catalog = EvidenceCatalog(task.id, task.session_key)
            collector = EvidenceCollector(catalog, events)
            pipeline = EvidenceExtractionPipeline(collector, [ReadResultExtractor()])
            audit = RuntimeAudit(AuditStore(root / "audit"), task.id)
            coordinator = InvestigationCoordinator(
                task=task,
                session=session,
                controller=controller,
                agent=EvidenceFakeAgent(task.id, events, root / "turns"),
                stop_gate=SafeStopGate(),
                catalog=catalog,
                collector=collector,
                convergence_policy=ConvergencePolicy(max_stale_rounds=2),
                events=events,
                evidence_pipeline=pipeline,
                audit=audit,
            )

            coordinator.start("inspect", turn_name="turn-1")
            self.assertEqual(len(catalog.items), 1)
            self.assertEqual(catalog.get("E1").source, "/agent/system.log")
            self.assertEqual(coordinator.metrics.stale_rounds, 0)

            task_dir = root / "audit" / task.id
            self.assertEqual(json.loads((task_dir / "task.json").read_text())["state"], "RUNNING")
            evidence = json.loads((task_dir / "evidence.json").read_text())
            self.assertEqual(evidence["items"][0]["raw"], "worker exited status=137")
            self.assertEqual(json.loads((task_dir / "session.json").read_text())["turns"][0]["kind"], "USER")

            coordinator.begin_finalization(goal_satisfied=True)
            payload = {
                "claims": [{
                    "id": "C1",
                    "kind": "fact",
                    "topic": "ignored",
                    "evidence_refs": ["E1"],
                    "confidence": "high",
                    "scope": "event",
                    "relation": "observed",
                }],
                "summary_claim_ids": ["C1"],
            }
            final = coordinator.finish_finalization(payload)
            self.assertTrue(final.valid, final.errors)
            self.assertEqual(task.state, TaskState.COMPLETED)
            self.assertTrue((task_dir / "claims.json").is_file())
            self.assertTrue((task_dir / "result.json").is_file())
            self.assertIn("worker exited status=137", (task_dir / "final.txt").read_text())


if __name__ == "__main__":
    unittest.main()
