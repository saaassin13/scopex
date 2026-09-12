import unittest

from scopex.evidence.catalog import EvidenceCatalog
from scopex.evidence.collector import EvidenceCollector
from scopex.events.progress import EventType, InMemoryEventSink
from scopex.finalizer.service import FinalizationService
from scopex.runtime.controller import TaskController
from scopex.runtime.session import Session
from scopex.runtime.task import Task, TaskState


class RuntimeEndToEndSmokeTests(unittest.TestCase):
    def test_task_evidence_structured_finalization_flow(self):
        events = InMemoryEventSink()
        task = Task(id="task-1", user_request="diagnose failure", session_key="agent:sx:task-1")
        session = Session(task_id=task.id, session_key=task.session_key)
        controller = TaskController(task, session, events)
        catalog = EvidenceCatalog(task.id, task.session_key)
        evidence = EvidenceCollector(catalog, events)

        controller.created()
        controller.start()

        worker = evidence.add(
            source="system.log",
            raw="2026-09-12 10:15:00.180 [ERROR] inference-worker exited status=137",
            tool_call_id="c-system",
        )
        app = evidence.add(
            source="app.log",
            raw="2026-09-12 10:15:00.722 [ERROR] task execution failed reason=target_pose_unavailable",
            tool_call_id="c-app",
        )
        robot = evidence.add(
            source="robot.log",
            raw="2026-09-12 10:15:00.800 [INFO] joint_fault_code=0 emergency_stop=false",
            tool_call_id="c-robot",
        )

        controller.begin_finalization(reasons=("goal_satisfied",))
        payload = {
            "claims": [
                {
                    "id": "C1",
                    "kind": "fact",
                    "topic": "model prose must not replace exact evidence",
                    "evidence_refs": [worker.ref],
                    "confidence": "high",
                    "scope": "event",
                    "relation": "observed",
                },
                {
                    "id": "C2",
                    "kind": "fact",
                    "topic": "app failure",
                    "evidence_refs": [app.ref],
                    "confidence": "high",
                    "scope": "event",
                    "relation": "observed",
                },
                {
                    "id": "C3",
                    "kind": "inference",
                    "topic": "events are temporally associated",
                    "evidence_refs": [worker.ref, app.ref],
                    "confidence": "medium",
                    "scope": "time_window",
                    "relation": "temporal_association",
                },
                {
                    "id": "C4",
                    "kind": "fact",
                    "topic": "robot window",
                    "evidence_refs": [robot.ref],
                    "confidence": "high",
                    "scope": "time_window",
                    "relation": "observed",
                },
                {
                    "id": "C5",
                    "kind": "unknown",
                    "topic": "status 137 trigger mechanism",
                    "evidence_refs": [worker.ref],
                    "confidence": "unknown",
                    "scope": "event",
                    "relation": "unknown",
                },
            ],
            "summary_claim_ids": ["C1", "C2", "C3", "C4", "C5"],
        }

        final = FinalizationService().finalize(payload, catalog)
        self.assertTrue(final.valid, final.errors)
        self.assertIn("inference-worker exited status=137", final.rendered)
        self.assertIn("该结构不表示已证明因果", final.rendered)
        self.assertNotIn("model prose must not replace exact evidence", final.rendered)

        controller.finalization_completed()
        controller.complete()
        self.assertEqual(task.state, TaskState.COMPLETED)
        event_types = [event.type for event in events.events]
        self.assertEqual(event_types.count(EventType.EVIDENCE_ADDED), 3)
        self.assertIn(EventType.FINALIZATION_STARTED, event_types)
        self.assertEqual(event_types[-1], EventType.TASK_COMPLETED)


if __name__ == "__main__":
    unittest.main()
