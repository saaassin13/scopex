"""Synthetic contract regressions, not live-model business-accuracy tests."""
import copy
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scopex.evidence.catalog import EvidenceCatalog
from scopex.finalizer.client import FinalizerResponse, StreamingFinalizerClient
from scopex.finalizer.structured import StructuredFinalizer, build_structured_prompts


def response(content, finish="stop", done=True, **kwargs):
    return FinalizerResponse(content, 0.01, 0.02, 0.03, (finish,), done,
                             {"prompt_tokens": 100, "completion_tokens": 20}, **kwargs)


def payload(*, relation="observed", refs=None, kind="fact"):
    return {"claims": [{"id": "C1", "kind": kind, "topic": "two recorded events",
                        "evidence_refs": ["E1"] if refs is None else refs,
                        "confidence": "medium", "scope": "time_window", "relation": relation}],
            "summary_claim_ids": ["C1"]}


class SequenceClient:
    def __init__(self, *responses, api_key=""):
        self.responses = list(responses)
        self.calls = []
        self.api_key = api_key

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("more than the permitted model calls")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class FinalizerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.catalog = EvidenceCatalog("test-run", "test-session")
        self.catalog.add(source="business_facts:test-counter", raw=json.dumps({
            "facts": {"event_count": 2},
            "events": [{"time": "10:01:01"}, {"time": "10:01:04"}],
        }), metadata={"evidence_type": "structured_business_facts"})
        self.valid = json.dumps(payload())
        self.bad_temporal = json.dumps(payload(relation="temporal_association", kind="inference"))

    def run_client(self, *responses, **kwargs):
        client = SequenceClient(*responses)
        result = StructuredFinalizer(client, model="test-model", **kwargs).run(
            user_request="check recorded counter events", catalog=self.catalog)
        return client, result

    def test_aggregate_prompt_explicitly_distinguishes_statistics_from_association(self):
        system, user = build_structured_prompts("test", self.catalog)
        self.assertIn("type=structured_business_facts", user)
        self.assertIn("同一 E ref 可以支持多个不同统计事实", system)
        self.assertIn("不能仅因含有时间就标成 temporal_association", system)
        self.assertIn("2-4 个不同 E ref", system)

    def test_single_ref_temporal_failure_is_repaired_by_model_not_validator_bypass(self):
        original = copy.deepcopy(self.catalog.snapshot())
        client, result = self.run_client(response(self.bad_temporal), response(self.valid))
        self.assertTrue(result.valid)
        self.assertEqual(result.retry_count, 1)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(self.catalog.snapshot(), original)
        first = result.attempts[0]
        self.assertFalse(first["valid"])
        self.assertEqual(first["content"], self.bad_temporal)
        self.assertIn("claims[0].temporal_requires_two_refs", first["errors"])
        self.assertIn("temporal_requires_two_refs", client.calls[1]["system_prompt"])
        self.assertEqual(client.calls[0]["user_prompt"], client.calls[1]["user_prompt"])
        self.assertEqual(result.attempts[0]["user_prompt_sha256"], result.attempts[1]["user_prompt_sha256"])
        self.assertFalse(first["tools_enabled"])

    def test_repeated_temporal_failure_still_fails_closed_after_two_calls(self):
        client, result = self.run_client(response(self.bad_temporal), response(self.bad_temporal))
        self.assertFalse(result.valid)
        self.assertIsNone(result.finalization.rendered)
        self.assertEqual(result.payload["claims"][0]["relation"], "temporal_association")
        self.assertEqual(len(client.calls), 2)
        self.assertTrue(all(not row["valid"] for row in result.attempts))

    def test_non_json_stop_recovers_without_substring_extraction(self):
        text = "这是统计结果：" + self.valid
        client, result = self.run_client(response(text), response(self.valid))
        self.assertTrue(result.valid)
        self.assertEqual(result.attempts[0]["content"], text)
        self.assertIsNotNone(result.attempts[0]["parse_error"])
        self.assertNotIn(text, client.calls[1]["system_prompt"])
        self.assertEqual(client.calls[1]["max_tokens"], 768)

    def test_empty_content_is_distinguishable_from_reasoning_only(self):
        client, result = self.run_client(response("", reasoning_chars=123), response(self.valid))
        self.assertTrue(result.valid)
        self.assertEqual(result.attempts[0]["content_chars"], 0)
        self.assertEqual(result.attempts[0]["reasoning_chars"], 123)
        self.assertEqual(len(client.calls), 2)

    def test_repeated_empty_stop_is_not_published_as_success(self):
        client, result = self.run_client(response(""), response(""))
        self.assertFalse(result.valid)
        self.assertIsNone(result.payload)
        self.assertEqual(len(client.calls), 2)

    def test_format_then_length_cannot_get_a_third_attempt(self):
        client, result = self.run_client(response("not json"), response("{", finish="length"))
        self.assertFalse(result.valid)
        self.assertEqual(result.parse_error, "structured_finalizer_truncated")
        self.assertEqual(len(client.calls), 2)

    def test_length_then_format_cannot_get_a_third_attempt(self):
        client, result = self.run_client(response("{", finish="length"), response("not json"))
        self.assertFalse(result.valid)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[1]["max_tokens"], 1536)
        self.assertIn("[长度恢复]", client.calls[1]["system_prompt"])
        self.assertEqual(len(result.attempts), 2)

    def test_unknown_and_duplicate_refs_are_not_manufactured_or_accepted(self):
        for refs in (["E99"], ["E1", "E1"]):
            with self.subTest(refs=refs):
                bad = json.dumps(payload(refs=refs))
                client, result = self.run_client(response(bad), response(bad))
                self.assertFalse(result.valid)
                self.assertEqual(len(client.calls), 2)
                self.assertEqual(self.catalog.refs, frozenset({"E1"}))

    def test_invalid_container_in_enum_is_a_bounded_failure_not_worker_exception(self):
        bad = payload()
        bad["claims"][0]["relation"] = []
        client, result = self.run_client(response(json.dumps(bad)), response(self.valid))
        self.assertTrue(result.valid)
        self.assertIn("invalid_claim_structure", result.attempts[0]["parse_error"])
        self.assertEqual(len(client.calls), 2)

    def test_incomplete_stream_is_not_retried(self):
        client, result = self.run_client(response(self.valid, done=False))
        self.assertFalse(result.valid)
        self.assertEqual(len(client.calls), 1)

    def test_http_failure_is_not_retried_and_key_is_redacted(self):
        client = SequenceClient(ValueError("HTTP error: Bearer key-for-test"), api_key="key-for-test")
        result = StructuredFinalizer(client, model="test").run(user_request="test", catalog=self.catalog)
        self.assertFalse(result.valid)
        self.assertEqual(len(client.calls), 1)
        self.assertNotIn("key-for-test", json.dumps(result.attempts))
        self.assertNotIn("key-for-test", result.parse_error)

    def test_retry_transport_failure_preserves_the_failed_first_response(self):
        client, result = self.run_client(response("bad first response"), OSError("connection lost"))
        self.assertFalse(result.valid)
        self.assertEqual(result.attempts[0]["content"], "bad first response")
        self.assertIn("retry_transport_error", result.attempts[1]["parse_error"])
        self.assertEqual(len(client.calls), 2)

    def test_diagnostics_are_bounded_and_credentials_redacted(self):
        bad = 'api_key="sensitive-test-key" Bearer sensitive-test-key ' + "x" * 20000
        client = SequenceClient(response(bad), response(self.valid), api_key="sensitive-test-key")
        result = StructuredFinalizer(client, model="test").run(user_request="test", catalog=self.catalog)
        row = result.attempts[0]
        self.assertEqual(len(row["content"]), 16384)
        self.assertEqual(row["content_chars"], len(bad))
        self.assertTrue(row["content_truncated"])
        self.assertTrue(row["content_redacted"])
        self.assertNotIn("sensitive-test-key", json.dumps(result.attempts))

    def test_media_load_failure_makes_no_model_request(self):
        loader = Mock()
        loader.load.side_effect = ValueError("image SHA mismatch")
        client, result = self.run_client(media_loader=loader)
        self.assertFalse(result.valid)
        self.assertEqual(len(client.calls), 0)

    def test_repair_reuses_identical_verified_image_inputs(self):
        loader = Mock()
        loader.load.return_value = [SimpleNamespace(ref="E1", source="/readonly/a.png",
                                                   sha256="abc", data_url="data:image/png;base64,AAAA")]
        client, result = self.run_client(response("bad"), response(self.valid), media_loader=loader)
        self.assertTrue(result.valid)
        self.assertEqual(loader.load.call_count, 1)
        self.assertEqual(client.calls[0]["image_inputs"], client.calls[1]["image_inputs"])
        self.assertNotIn("base64", json.dumps(result.attempts))

    def test_valid_first_response_uses_one_request(self):
        client, result = self.run_client(response(self.valid))
        self.assertTrue(result.valid)
        self.assertEqual(result.retry_count, 0)
        self.assertEqual(len(client.calls), 1)

    def test_length_at_maximum_budget_still_does_not_retry(self):
        client, result = self.run_client(response("{", finish="length"), max_tokens=4096)
        self.assertFalse(result.valid)
        self.assertEqual(len(client.calls), 1)

    def test_runtime_persists_attempts_in_existing_review_export_result(self):
        from scopex.runtime.investigation import InvestigationCoordinator
        from scopex.runtime.task import TaskState
        client, result = self.run_client(response("bad"), response(self.valid))
        audit = Mock()
        coordinator = SimpleNamespace(audit=audit, _finalization_reasons=("goal_satisfied",))
        InvestigationCoordinator._persist_structured_result(
            coordinator, result, published_state=TaskState.COMPLETED)
        stored = audit.persist_result.call_args.args[0]
        self.assertEqual(stored["finalizer_attempts"], list(result.attempts))
        self.assertEqual(stored["finalizer_retry_count"], 1)
        self.assertNotIn("report", stored)

    def test_client_counts_noncontent_channels_without_using_them_as_json(self):
        events = [
            {"choices": [{"delta": {"reasoning_content": "hidden reasoning"}}]},
            {"choices": [{"delta": {"tool_calls": [{"function": {"arguments": "{}"}}]}}]},
            {"choices": [{"delta": {"content": self.valid}, "finish_reason": "stop"}]},
        ]
        stream = io.BytesIO(("\n".join("data: " + json.dumps(e) for e in events) + "\ndata: [DONE]\n").encode())
        stream.status = 200
        connection = Mock()
        connection.getresponse.return_value = stream
        with patch("scopex.finalizer.client.http.client.HTTPConnection", return_value=connection):
            transport = StreamingFinalizerClient("http://127.0.0.1:18002/v1").complete(
                model="test", system_prompt="json", user_prompt="test")
        self.assertEqual(transport.content, self.valid)
        self.assertEqual(transport.reasoning_chars, len("hidden reasoning"))
        self.assertEqual(transport.tool_call_chunks, 1)
        sent = json.loads(connection.request.call_args.kwargs["body"])
        self.assertNotIn("tools", sent)
        self.assertEqual(sent["chat_template_kwargs"], {"enable_thinking": False})


if __name__ == "__main__":
    unittest.main()
