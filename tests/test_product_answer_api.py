from pathlib import Path
import tempfile
import unittest

from scopex.api.service import TaskService


class ProductAnswerApiTests(unittest.TestCase):
    def service(self, root: Path) -> TaskService:
        return TaskService(
            audit_root=root,
            coordinator_factory=lambda *_args: None,
            finalizer_factory=lambda: None,
        )

    def seed_completed_task(self, service: TaskService, task_id: str = "task-1") -> None:
        service.store.write_json(
            task_id,
            "task.json",
            {"id": task_id, "state": "COMPLETED", "user_request": "diagnose"},
        )
        service.store.write_json(
            task_id,
            "result.json",
            {"valid": True, "errors": [], "task_state": "COMPLETED"},
        )
        service.store.write_text(task_id, "final.txt", "trusted deterministic fallback")

    def test_historical_task_without_answer_json_keeps_renderer_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            service = self.service(Path(td))
            self.seed_completed_task(service)
            result = service.get_result("task-1")
            self.assertTrue(result["available"])
            self.assertIsNone(result["product_answer"])
            self.assertIsNone(result["answer_composer"])
            self.assertEqual(result["rendered"], "trusted deterministic fallback")

    def test_valid_answer_json_is_exposed_without_removing_renderer(self):
        with tempfile.TemporaryDirectory() as td:
            service = self.service(Path(td))
            self.seed_completed_task(service)
            answer = {
                "schema_version": 1,
                "conclusion": [
                    {
                        "claim_id": "C1",
                        "text": "worker exited status=137",
                        "kind": "fact",
                        "relation": "observed",
                        "confidence": "high",
                        "evidence_refs": ["E1"],
                    }
                ],
                "explanation": [],
                "execution": [],
                "recommendation": [],
            }
            service.store.write_json(
                "task-1",
                "answer.json",
                {
                    "valid": True,
                    "errors": [],
                    "parse_error": None,
                    "finish_reasons": ["stop"],
                    "elapsed_s": 0.5,
                    "answer": answer,
                },
            )
            result = service.get_result("task-1")
            self.assertEqual(result["product_answer"], answer)
            self.assertTrue(result["answer_composer"]["valid"])
            self.assertEqual(result["rendered"], "trusted deterministic fallback")

    def test_invalid_composer_result_never_hides_trusted_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            service = self.service(Path(td))
            self.seed_completed_task(service)
            service.store.write_json(
                "task-1",
                "answer.json",
                {
                    "valid": False,
                    "errors": ["execution_claim_ids.not_fact"],
                    "parse_error": None,
                    "answer": None,
                },
            )
            result = service.get_result("task-1")
            self.assertIsNone(result["product_answer"])
            self.assertFalse(result["answer_composer"]["valid"])
            self.assertEqual(result["rendered"], "trusted deterministic fallback")


if __name__ == "__main__":
    unittest.main()
