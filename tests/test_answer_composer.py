import unittest

from scopex.evidence.catalog import EvidenceCatalog
from scopex.finalizer.answer import ConstrainedAnswerComposer, build_answer_prompts
from scopex.finalizer.claims import claim_set_from_dict
from scopex.finalizer.client import FinalizerResponse


class FakeClient:
    def __init__(self, content, finish="stop", done=True):
        self.content = content
        self.finish = finish
        self.done = done
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return FinalizerResponse(
            content=self.content,
            headers_s=0.01,
            first_content_s=0.02,
            elapsed_s=0.03,
            finish_reasons=(self.finish,),
            done_seen=self.done,
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )


class AnswerComposerTests(unittest.TestCase):
    def fixture(self):
        catalog = EvidenceCatalog("t1", "s1")
        catalog.add(source="system.log", raw="worker exited status=137")
        catalog.add(source="app.log", raw="task failed target_pose_unavailable")
        catalog.add(source="robot.log", raw="restart verification is incomplete")
        claims = claim_set_from_dict(
            {
                "claims": [
                    {
                        "id": "C1",
                        "kind": "fact",
                        "topic": "GPU OOM caused everything",
                        "evidence_refs": ["E1"],
                        "confidence": "high",
                        "scope": "event",
                        "relation": "observed",
                    },
                    {
                        "id": "C2",
                        "kind": "inference",
                        "topic": "events are close in time",
                        "evidence_refs": ["E1", "E2"],
                        "confidence": "medium",
                        "scope": "time_window",
                        "relation": "temporal_association",
                    },
                    {
                        "id": "C3",
                        "kind": "unknown",
                        "topic": "restart recovery remains unverified",
                        "evidence_refs": ["E3"],
                        "confidence": "unknown",
                        "scope": "component",
                        "relation": "unknown",
                    },
                ],
                "summary_claim_ids": ["C1", "C2"],
            }
        )
        return catalog, claims

    def test_composer_only_selects_ids_and_runtime_materializes_trusted_text(self):
        catalog, claims = self.fixture()
        client = FakeClient(
            '{"conclusion_claim_ids":["C1"],'
            '"explanation_claim_ids":["C2"],'
            '"execution_claim_ids":[],"recommendation_claim_ids":["C3"]}'
        )
        result = ConstrainedAnswerComposer(client, model="m").run(
            user_request="diagnose failure",
            claims=claims,
            catalog=catalog,
        )

        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.answer.conclusion[0].text, "worker exited status=137")
        self.assertNotIn("GPU OOM", result.answer.conclusion[0].text)
        self.assertIn("task failed target_pose_unavailable", result.answer.explanation[0].text)
        self.assertIn("尚不能据此证明因果", result.answer.explanation[0].text)
        self.assertIn("尚不能确定", result.answer.recommendation[0].text)
        self.assertEqual(result.answer.conclusion[0].evidence_refs, ("E1",))
        self.assertEqual(client.calls[0]["temperature"], 0)
        self.assertEqual(client.calls[0]["max_tokens"], 256)

    def test_composer_prompt_contains_no_raw_evidence_or_tool_transcript(self):
        catalog, claims = self.fixture()
        _system, user = build_answer_prompts("diagnose failure", claims)
        for item in catalog.items:
            self.assertNotIn(item.raw, user)
        self.assertIn("GPU OOM caused everything", user)
        self.assertIn('"evidence_refs":["E1"]', user)

    def test_unknown_claim_id_is_rejected(self):
        catalog, claims = self.fixture()
        result = ConstrainedAnswerComposer(
            FakeClient(
                '{"conclusion_claim_ids":["C99"],'
                '"explanation_claim_ids":["C1","C2"],'
                '"execution_claim_ids":[],"recommendation_claim_ids":[]}'
            ),
            model="m",
        ).run(user_request="diagnose", claims=claims, catalog=catalog)
        self.assertFalse(result.valid)
        self.assertIn("conclusion_claim_ids.unknown_claim", result.errors)

    def test_execution_section_cannot_upgrade_inference_to_executed_fact(self):
        catalog, claims = self.fixture()
        result = ConstrainedAnswerComposer(
            FakeClient(
                '{"conclusion_claim_ids":["C1"],'
                '"explanation_claim_ids":[],"execution_claim_ids":["C2"],'
                '"recommendation_claim_ids":[]}'
            ),
            model="m",
        ).run(user_request="diagnose", claims=claims, catalog=catalog)
        self.assertFalse(result.valid)
        self.assertIn("execution_claim_ids.not_fact", result.errors)

    def test_bad_composer_output_does_not_materialize_answer(self):
        catalog, claims = self.fixture()
        result = ConstrainedAnswerComposer(
            FakeClient('{"conclusion_claim_ids":["C1"]}', finish="stop"),
            model="m",
        ).run(user_request="diagnose", claims=claims, catalog=catalog)
        self.assertFalse(result.valid)
        self.assertIsNone(result.answer)
        self.assertIn("top_level_fields", result.errors)


if __name__ == "__main__":
    unittest.main()
