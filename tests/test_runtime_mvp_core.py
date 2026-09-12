from pathlib import Path
import tempfile
import unittest

from scopex.agent.openclaw import OpenClawCommandBuilder
from scopex.evidence.catalog import EvidenceCatalog
from scopex.events.progress import EventType, InMemoryEventSink
from scopex.finalizer.claims import claim_set_from_dict
from scopex.finalizer.renderer import render_claims
from scopex.finalizer.validator import validate_claim_payload
from scopex.runtime.controller import TaskController
from scopex.runtime.convergence import ConvergencePolicy, ConvergenceSnapshot, evaluate
from scopex.runtime.permissions import ActionClass, PermissionDecision, PermissionPolicy
from scopex.runtime.session import Session
from scopex.runtime.task import Task, TaskState
from scopex.storage.audit import AuditStore


class RuntimeMvpCoreTests(unittest.TestCase):
    def make_control(self):
        task = Task(id="t1", user_request="diagnose", session_key="agent:sx:t1")
        session = Session(task_id="t1", session_key="agent:sx:t1")
        events = InMemoryEventSink()
        return task, session, events, TaskController(task, session, events)

    def test_task_controller_stop_resume_finalize(self):
        task, session, events, controller = self.make_control()
        controller.created()
        controller.start()
        controller.steer("check system first")
        controller.request_stop("user clicked stop")
        self.assertEqual(task.state, TaskState.PAUSING)
        controller.safe_stop(before_model_request=3, running_tool_cancelled=False)
        self.assertEqual(task.state, TaskState.PAUSED)
        controller.resume("continue with robot")
        controller.begin_finalization(reasons=("goal_satisfied",))
        controller.finalization_completed()
        controller.complete()
        self.assertEqual(task.state, TaskState.COMPLETED)
        self.assertEqual(
            [event.type for event in events.events],
            [
                EventType.TASK_CREATED,
                EventType.TASK_STARTED,
                EventType.USER_STEER,
                EventType.USER_STOP,
                EventType.SAFE_STOP,
                EventType.USER_RESUME,
                EventType.INVESTIGATION_COMPLETED,
                EventType.FINALIZATION_STARTED,
                EventType.FINALIZATION_COMPLETED,
                EventType.TASK_COMPLETED,
            ],
        )
        self.assertEqual([turn.kind.value for turn in session.turns], ["STEER", "STOP", "RESUME"])

    def test_invalid_task_transition_is_rejected(self):
        task = Task(id="t1", user_request="x", session_key="s")
        with self.assertRaises(ValueError):
            task.transition(TaskState.PAUSED)

    def test_convergence_policy_is_generic(self):
        decision = evaluate(
            ConvergencePolicy(max_model_requests=4, max_stale_rounds=2),
            ConvergenceSnapshot(
                model_requests=4,
                tool_calls=1,
                elapsed_s=10,
                context_chars=100,
                stale_rounds=2,
            ),
        )
        self.assertTrue(decision.should_finalize)
        self.assertIn("model_request_budget", decision.reasons)
        self.assertIn("no_new_evidence", decision.reasons)

    def test_permission_defaults_require_confirmation_for_side_effects(self):
        policy = PermissionPolicy()
        self.assertEqual(policy.decision_for(ActionClass.READ_ONLY), PermissionDecision.AUTO)
        self.assertTrue(policy.requires_confirmation(ActionClass.CONFIG_WRITE))
        self.assertTrue(policy.requires_confirmation(ActionClass.DEVICE_CONTROL))

    def test_evidence_catalog_owns_exact_refs_and_deduplicates(self):
        catalog = EvidenceCatalog("t1", "s1")
        one = catalog.add(source="system.log", raw="worker exited status=137", tool_call_id="c1")
        same = catalog.add(source="system.log", raw="worker exited status=137", tool_call_id="c2")
        two = catalog.add(source="robot.log", raw="joint_fault_code=0", tool_call_id="c3")
        self.assertEqual(one.ref, "E1")
        self.assertIs(one, same)
        self.assertEqual(two.ref, "E2")
        self.assertEqual(catalog.get("E1").raw, "worker exited status=137")

    def make_claim_fixture(self):
        catalog = EvidenceCatalog("t1", "s1")
        catalog.add(source="system.log", raw="worker exited status=137")
        catalog.add(source="app.log", raw="task failed target_pose_unavailable")
        catalog.add(source="robot.log", raw="joint_fault_code=0")
        payload = {
            "claims": [
                {
                    "id": "C1",
                    "kind": "fact",
                    "topic": "GPU OOM definitely caused this",
                    "evidence_refs": ["E1"],
                    "confidence": "high",
                    "scope": "event",
                    "relation": "observed",
                },
                {
                    "id": "C2",
                    "kind": "inference",
                    "topic": "worker exit and app failure are close in time",
                    "evidence_refs": ["E1", "E2"],
                    "confidence": "medium",
                    "scope": "time_window",
                    "relation": "temporal_association",
                },
                {
                    "id": "C3",
                    "kind": "unknown",
                    "topic": "status 137 trigger mechanism",
                    "evidence_refs": ["E1"],
                    "confidence": "unknown",
                    "scope": "event",
                    "relation": "unknown",
                },
            ],
            "summary_claim_ids": ["C1", "C2", "C3"],
        }
        return catalog, payload

    def test_generic_claim_validator_and_renderer(self):
        catalog, payload = self.make_claim_fixture()
        self.assertEqual(validate_claim_payload(payload, catalog), [])
        rendered = render_claims(claim_set_from_dict(payload), catalog)
        self.assertIn("worker exited status=137", rendered)
        self.assertIn("该结构不表示已证明因果", rendered)
        self.assertIn("status 137 trigger mechanism", rendered)
        # A malicious/incorrect model topic on an observed fact must not become prose.
        self.assertNotIn("GPU OOM definitely caused this", rendered)

    def test_validator_rejects_malformed_refs_without_crashing(self):
        catalog, payload = self.make_claim_fixture()
        payload["claims"][0]["evidence_refs"] = [{"bad": "E1"}]
        errors = validate_claim_payload(payload, catalog)
        self.assertIn("claims[0].evidence_refs", errors)
        self.assertIn("claims[0].fact_requires_evidence", errors)

    def test_validator_requires_two_refs_for_temporal_association(self):
        catalog, payload = self.make_claim_fixture()
        payload["claims"][1]["evidence_refs"] = ["E1"]
        errors = validate_claim_payload(payload, catalog)
        self.assertIn("claims[1].temporal_requires_two_refs", errors)

    def test_audit_store_writes_task_files(self):
        with tempfile.TemporaryDirectory() as td:
            store = AuditStore(Path(td))
            result = store.write_json("task-1", "task.json", {"state": "RUNNING"})
            store.append_jsonl("task-1", "events.jsonl", {"event": "TASK_STARTED"})
            self.assertTrue(result.is_file())
            self.assertIn('"RUNNING"', result.read_text(encoding="utf-8"))
            self.assertTrue((Path(td) / "task-1" / "events.jsonl").is_file())

    def test_openclaw_builder_uses_same_session_key(self):
        command = OpenClawCommandBuilder(Path("/opt/openclaw")).build(
            session_key="agent:sx:task-1",
            message_file=Path("/tmp/message.txt"),
            timeout_s=180,
        )
        self.assertIn("--session-key", command)
        index = command.index("--session-key")
        self.assertEqual(command[index + 1], "agent:sx:task-1")
        self.assertNotIn("--session-id", command)


if __name__ == "__main__":
    unittest.main()
