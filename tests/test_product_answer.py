from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scopex.evidence.catalog import EvidenceCatalog
from scopex.finalizer.answer import compose_product_answer
from scopex.finalizer.claims import claim_set_from_dict
from scopex.storage.audit import AuditStore
from scopex.storage.runtime_audit import RuntimeAudit


class ProductAnswerTests(unittest.TestCase):
    def test_observed_text_fact_uses_raw_evidence_not_free_form_topic(self) -> None:
        catalog = EvidenceCatalog("task-1", "session-1")
        catalog.add(
            source="status.txt",
            raw="state=RUNNING",
            metadata={"evidence_type": "file_line", "line_number": 1},
        )
        claims = claim_set_from_dict(
            {
                "claims": [
                    {
                        "id": "C1",
                        "kind": "fact",
                        "topic": "GPU OOM caused everything",
                        "evidence_refs": ["E1"],
                        "confidence": "high",
                        "scope": "component",
                        "relation": "observed",
                    }
                ],
                "summary_claim_ids": ["C1"],
            }
        )

        answer = compose_product_answer(claims, catalog).to_dict()

        self.assertEqual(answer["conclusion"][0]["text"], "state=RUNNING")
        self.assertNotIn("GPU OOM caused everything", str(answer))
        self.assertEqual(answer["conclusion"][0]["claim_ids"], ["C1"])
        self.assertEqual(answer["recommendations"], [])

    def test_visual_fact_may_use_fresh_finalizer_topic(self) -> None:
        catalog = EvidenceCatalog("task-1", "session-1")
        catalog.add(
            source="/agent-data/frame.jpg",
            raw="image evidence",
            metadata={"evidence_type": "image", "sha256": "a" * 64},
        )
        claims = claim_set_from_dict(
            {
                "claims": [
                    {
                        "id": "C1",
                        "kind": "fact",
                        "topic": "画面存在明显整体低对比度",
                        "evidence_refs": ["E1"],
                        "confidence": "high",
                        "scope": "event",
                        "relation": "observed",
                    }
                ],
                "summary_claim_ids": ["C1"],
            }
        )

        answer = compose_product_answer(claims, catalog).to_dict()
        self.assertEqual(answer["conclusion"][0]["text"], "画面存在明显整体低对比度")

    def test_temporal_association_cannot_surface_causal_topic_as_fact(self) -> None:
        catalog = EvidenceCatalog("task-1", "session-1")
        catalog.add(source="a.log", raw="camera warning at 10:00", metadata={"evidence_type": "file_line"})
        catalog.add(source="b.log", raw="task failed at 10:01", metadata={"evidence_type": "file_line"})
        claims = claim_set_from_dict(
            {
                "claims": [
                    {
                        "id": "C1",
                        "kind": "inference",
                        "topic": "camera warning caused task failure",
                        "evidence_refs": ["E1", "E2"],
                        "confidence": "medium",
                        "scope": "time_window",
                        "relation": "temporal_association",
                    }
                ],
                "summary_claim_ids": ["C1"],
            }
        )

        answer = compose_product_answer(claims, catalog).to_dict()
        text = answer["conclusion"][0]["text"]
        self.assertIn("camera warning at 10:00", text)
        self.assertIn("task failed at 10:01", text)
        self.assertIn("未证明因果", text)
        self.assertNotIn("camera warning caused task failure", text)

    def test_observed_fact_is_preferred_over_hypothesis_for_headline(self) -> None:
        catalog = EvidenceCatalog("task-1", "session-1")
        catalog.add(source="a.log", raw="blur detected", metadata={"evidence_type": "file_line"})
        catalog.add(source="b.log", raw="humidity high", metadata={"evidence_type": "file_line"})
        claims = claim_set_from_dict(
            {
                "claims": [
                    {
                        "id": "C1",
                        "kind": "inference",
                        "topic": "可能起雾",
                        "evidence_refs": ["E1"],
                        "confidence": "low",
                        "scope": "component",
                        "relation": "causal_hypothesis",
                    },
                    {
                        "id": "C2",
                        "kind": "fact",
                        "topic": "ignored",
                        "evidence_refs": ["E1"],
                        "confidence": "high",
                        "scope": "event",
                        "relation": "observed",
                    },
                ],
                "summary_claim_ids": ["C1", "C2"],
            }
        )

        answer = compose_product_answer(claims, catalog).to_dict()
        self.assertEqual(answer["conclusion"][0]["claim_ids"], ["C2"])
        self.assertEqual(answer["conclusion"][0]["text"], "blur detected")
        self.assertIn("待验证假设：可能起雾", answer["explanation"][0]["text"])

    def test_generic_command_evidence_is_not_promoted_to_execution(self) -> None:
        catalog = EvidenceCatalog("task-1", "session-1")
        catalog.add(
            source="exec:call-1",
            raw="matched 42 rows",
            metadata={
                "evidence_type": "command_line",
                "line_number": 1,
                "command": "grep overload telemetry.csv",
            },
        )
        claims = claim_set_from_dict(
            {
                "claims": [
                    {
                        "id": "C1",
                        "kind": "fact",
                        "topic": "发现 42 条过载记录",
                        "evidence_refs": ["E1"],
                        "confidence": "high",
                        "scope": "time_window",
                        "relation": "observed",
                    }
                ],
                "summary_claim_ids": ["C1"],
            }
        )

        answer = compose_product_answer(claims, catalog).to_dict()

        self.assertEqual(answer["execution"], [])

    def test_runtime_audit_persists_answer_only_after_revalidation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AuditStore(Path(tmp))
            audit = RuntimeAudit(store, "task-1")
            catalog = EvidenceCatalog("task-1", "session-1")
            catalog.add(
                source="recovery.txt",
                raw="state=RUNNING",
                metadata={
                    "evidence_type": "command_line",
                    "evidence_role": "action_verification",
                    "line_number": 1,
                    "command": "device status",
                },
            )
            audit.persist_evidence(catalog)
            audit.persist_claims(
                {
                    "claims": [
                        {
                            "id": "C1",
                            "kind": "fact",
                            "topic": "恢复后状态为 RUNNING",
                            "evidence_refs": ["E1"],
                            "confidence": "high",
                            "scope": "component",
                            "relation": "observed",
                        }
                    ],
                    "summary_claim_ids": ["C1"],
                }
            )

            audit.persist_result({"valid": True, "task_state": "COMPLETED"}, rendered="fallback")

            result = store.read_json("task-1", "result.json")
            answer = store.read_json("task-1", "answer.json")
            self.assertEqual(result["answer"], answer)
            self.assertEqual(answer["execution"][0]["claim_ids"], ["C1"])
            self.assertEqual(answer["execution"][0]["text"], "state=RUNNING")
            self.assertEqual(store.read_text("task-1", "final.txt"), "fallback")

    def test_runtime_audit_does_not_publish_answer_for_invalid_claims(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AuditStore(Path(tmp))
            audit = RuntimeAudit(store, "task-1")
            catalog = EvidenceCatalog("task-1", "session-1")
            catalog.add(source="status.txt", raw="state=RUNNING")
            audit.persist_evidence(catalog)
            audit.persist_claims(
                {
                    "claims": [
                        {
                            "id": "C1",
                            "kind": "fact",
                            "topic": "unsupported",
                            "evidence_refs": ["E99"],
                            "confidence": "high",
                            "scope": "component",
                            "relation": "observed",
                        }
                    ],
                    "summary_claim_ids": ["C1"],
                }
            )

            audit.persist_result({"valid": True, "task_state": "COMPLETED"})

            result = store.read_json("task-1", "result.json")
            self.assertNotIn("answer", result)
            with self.assertRaises(FileNotFoundError):
                store.read_json("task-1", "answer.json")


if __name__ == "__main__":
    unittest.main()
