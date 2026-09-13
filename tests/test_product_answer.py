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
    def test_projection_uses_exact_validated_claim_topics(self) -> None:
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
                        "topic": "设备当前为 RUNNING",
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

        self.assertEqual(answer["conclusion"][0]["text"], "设备当前为 RUNNING")
        self.assertEqual(answer["conclusion"][0]["claim_ids"], ["C1"])
        self.assertEqual(answer["recommendations"], [])

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
