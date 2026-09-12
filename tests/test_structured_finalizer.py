import unittest

from scopex.evidence.catalog import EvidenceCatalog
from scopex.finalizer.client import FinalizerResponse
from scopex.finalizer.structured import StructuredFinalizer, parse_structured_payload


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
            usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        )


class StructuredFinalizerTests(unittest.TestCase):
    def catalog(self):
        catalog = EvidenceCatalog("t1", "s1")
        catalog.add(source="system.log", raw="worker exited status=137")
        catalog.add(source="app.log", raw="task failed target_pose_unavailable")
        return catalog

    def test_fenced_json_parse(self):
        self.assertEqual(
            parse_structured_payload('```json\n{"claims": [], "summary_claim_ids": []}\n```')["claims"],
            [],
        )

    def test_finalizer_validates_and_renders(self):
        content = '''{
          "claims": [
            {
              "id": "C1",
              "kind": "fact",
              "topic": "GPU OOM caused everything",
              "evidence_refs": ["E1"],
              "confidence": "high",
              "scope": "event",
              "relation": "observed"
            },
            {
              "id": "C2",
              "kind": "inference",
              "topic": "close in time",
              "evidence_refs": ["E1", "E2"],
              "confidence": "medium",
              "scope": "time_window",
              "relation": "temporal_association"
            }
          ],
          "summary_claim_ids": ["C1", "C2"]
        }'''
        client = FakeClient(content)
        result = StructuredFinalizer(client, model="m").run(
            user_request="diagnose",
            catalog=self.catalog(),
        )
        self.assertTrue(result.valid)
        self.assertTrue(result.finalization.valid)
        self.assertIn("直接观察｜system.log", result.finalization.rendered)
        self.assertIn("worker exited status=137", result.finalization.rendered)
        self.assertNotIn("GPU OOM caused everything", result.finalization.rendered)
        self.assertIn("这不表示已经证明因果", result.finalization.rendered)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["temperature"], 0)
        self.assertEqual(client.calls[0]["max_tokens"], 768)

    def test_facts_from_one_source_are_grouped_for_readability(self):
        catalog = EvidenceCatalog("t1", "s1")
        catalog.add(source="/agent-data/poc07/marker.txt", raw="POC07_BIND_OK", metadata={"line_number": 1})
        catalog.add(source="/agent-data/poc07/marker.txt", raw="source=spark-host", metadata={"line_number": 2})
        catalog.add(source="/agent-data/poc07/marker.txt", raw="purpose=openclaw-readonly-bind-validation", metadata={"line_number": 3})
        content = '''{
          "claims": [
            {"id":"C1","kind":"fact","topic":"marker","evidence_refs":["E1"],"confidence":"high","scope":"component","relation":"observed"},
            {"id":"C2","kind":"fact","topic":"source","evidence_refs":["E2"],"confidence":"high","scope":"component","relation":"observed"},
            {"id":"C3","kind":"fact","topic":"purpose","evidence_refs":["E3"],"confidence":"high","scope":"component","relation":"observed"}
          ],
          "summary_claim_ids": ["C1", "C2", "C3"]
        }'''
        result = StructuredFinalizer(FakeClient(content), model="m").run(
            user_request="read marker",
            catalog=catalog,
        )
        self.assertTrue(result.valid)
        rendered = result.finalization.rendered
        self.assertEqual(rendered.count("直接观察｜marker.txt"), 1)
        self.assertIn("[E1 · L1] POC07_BIND_OK", rendered)
        self.assertIn("[E2 · L2] source=spark-host", rendered)
        self.assertIn("[E3 · L3] purpose=openclaw-readonly-bind-validation", rendered)
        self.assertNotIn("事实｜组件范围", rendered)

    def test_causal_hypothesis_kind_alias_is_safely_normalized_to_inference(self):
        content = '''{
          "claims": [
            {
              "id": "C1",
              "kind": "hypothesis",
              "topic": "worker exit may contribute to app failure",
              "evidence_refs": ["E1", "E2"],
              "confidence": "medium",
              "scope": "time_window",
              "relation": "causal_hypothesis"
            }
          ],
          "summary_claim_ids": ["C1"]
        }'''
        result = StructuredFinalizer(FakeClient(content), model="m").run(
            user_request="diagnose",
            catalog=self.catalog(),
        )
        self.assertTrue(result.valid, result.finalization.errors if result.finalization else None)
        self.assertEqual(result.payload["claims"][0]["kind"], "inference")
        self.assertEqual(
            result.normalizations,
            ("claims[0].kind:hypothesis->inference",),
        )
        self.assertIn("待验证假设", result.finalization.rendered)

    def test_observed_relation_is_never_upgraded_to_fact_by_normalization(self):
        content = '''{
          "claims": [{
            "id": "C1",
            "kind": "inference",
            "topic": "worker exit",
            "evidence_refs": ["E1"],
            "confidence": "medium",
            "scope": "event",
            "relation": "observed"
          }],
          "summary_claim_ids": ["C1"]
        }'''
        result = StructuredFinalizer(FakeClient(content), model="m").run(
            user_request="diagnose",
            catalog=self.catalog(),
        )
        self.assertFalse(result.valid)
        self.assertEqual(result.normalizations, ())
        self.assertIn("claims[0].inference_relation", result.finalization.errors)

    def test_invalid_claim_structure_does_not_render(self):
        client = FakeClient('''{
          "claims": [{
            "id": "C1",
            "kind": "fact",
            "topic": "x",
            "evidence_refs": [],
            "confidence": "high",
            "scope": "event",
            "relation": "observed"
          }],
          "summary_claim_ids": ["C1"]
        }''')
        result = StructuredFinalizer(client, model="m").run(
            user_request="diagnose",
            catalog=self.catalog(),
        )
        self.assertFalse(result.valid)
        self.assertIn("claims[0].fact_requires_evidence", result.finalization.errors)
        self.assertIsNone(result.finalization.rendered)

    def test_transport_length_is_reported_as_truncation_before_json_parse(self):
        result = StructuredFinalizer(
            FakeClient('{"claims":[{"id":"C1"', finish="length"),
            model="m",
        ).run(user_request="diagnose", catalog=self.catalog())
        self.assertFalse(result.valid)
        self.assertEqual(result.parse_error, "structured_finalizer_truncated")
        self.assertIsNone(result.payload)
        self.assertIsNone(result.finalization)

    def test_incomplete_stream_is_reported_without_parse_attempt(self):
        result = StructuredFinalizer(
            FakeClient('{"claims":[]}', finish="stop", done=False),
            model="m",
        ).run(user_request="diagnose", catalog=self.catalog())
        self.assertFalse(result.valid)
        self.assertEqual(result.parse_error, "structured_finalizer_stream_incomplete")


if __name__ == "__main__":
    unittest.main()
