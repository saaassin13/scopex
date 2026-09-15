"""Exercise product publication, with only OpenClaw execution replaced."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scopex.agent.outcome import parse_cli_outcome
from scopex.agent.runtime import OpenClawTurnResult
from scopex.api.factory import LocalRuntimeConfig, OpenClawRuntimeFactory
from scopex.api.service import TaskService
from test_runtime_api_service import wait_state


class NativeAnswerTests(unittest.TestCase):
    def run_task(self, root, *, text="计数保持不变，对应停转；当前依据不足以确认故障。",
                 meta=None, stop=None, limit=None, evidence=True, mode="task"):
        cli = root / "openclaw"
        cli.write_text("#!/bin/sh\nexit 0\n")
        cli.chmod(0o755)
        workspace = root / "workspace"
        workspace.mkdir()
        cfg = LocalRuntimeConfig(cli_path=cli, model_id="local", base_url="http://127.0.0.1:18002/v1",
                                 api_key="test-secret", workspace=workspace, work_root=root / "work",
                                 sandbox_image="test", docker_host="unix:///tmp/docker.sock")
        factory = OpenClawRuntimeFactory(cfg)
        def coordinator(task, session, events, audit):
            with patch("scopex.api.factory.write_current_host_snapshot"):
                coord = factory.coordinator(task, session, events, audit)
            if evidence:
                coord.catalog.add(source="business_facts:custom", raw='{"value":17}',
                                  metadata={"evidence_type": "structured_business_facts"})
            envelope = {"payloads": [{"text": text}] if text is not None else [], "meta": meta or {}}
            turn = OpenClawTurnResult(
                turn_name="turn-001", audit_dir=root / "wire",
                process=SimpleNamespace(returncode=143 if stop else 0, stop_reason=stop),
                cli_outcome=parse_cli_outcome(json.dumps(envelope)), proxy_records=(),
                runtime_limit_reason=limit,
            )
            coord.agent.run_turn = Mock(return_value=turn)
            coord.agent.close = Mock(return_value=SimpleNamespace(container_ids=(), warnings=()))
            return coord
        finalizer = Mock(side_effect=AssertionError("native path must not construct a finalizer"))
        service = TaskService(audit_root=root / "tasks", coordinator_factory=coordinator,
                              finalizer_factory=finalizer)
        task = service.create_task("检查指定时间窗", mode=mode)
        expected = "FAILED" if stop or limit or meta or not text or len(text) > 32768 else "COMPLETED"
        wait_state(service, task["id"], expected)
        finalizer.assert_not_called()
        return service, task["id"]

    def test_native_answer_is_delivered_unchanged_with_audit_and_no_extra_model(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            service, tid = self.run_task(root)
            result = service.get_result(tid)["result"]
            self.assertEqual(result["report_text"], "计数保持不变，对应停转；当前依据不足以确认故障。")
            self.assertEqual(result["report_meta"]["producer"], "openclaw")
            self.assertEqual(result["report_meta"]["postprocess_model_calls"], 0)
            self.assertEqual(result["execution_status"], "completed")
            self.assertEqual(len(service.get_evidence(tid)["items"]), 1)
            self.assertEqual(service.get_events(tid)[-1]["type"], "TASK_COMPLETED")
            self.assertFalse((root / "tasks" / tid / "claims.json").exists())

    def test_success_does_not_require_a_scopex_business_schema(self):
        with tempfile.TemporaryDirectory() as td:
            service, tid = self.run_task(Path(td), evidence=False)
            self.assertTrue(service.get_result(tid)["result"]["valid"])

    def test_timeout_keeps_native_text_as_draft_even_with_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            service, tid = self.run_task(Path(td), stop="timeout", limit="turn_timeout_budget")
            result = service.get_result(tid)["result"]
            self.assertFalse(result["valid"])
            self.assertEqual(result["report_meta"]["status"], "partial")
            self.assertIn("turn_timeout_budget", result["errors"])
            self.assertEqual(service.get_task(tid)["last_reason"], "native_answer_partial")

    def test_missing_native_answer_is_not_recovered_from_a_utility_response(self):
        with tempfile.TemporaryDirectory() as td:
            service, tid = self.run_task(Path(td), text=None, stop="timeout")
            result = service.get_result(tid)["result"]
            self.assertEqual(result["report_text"], "")
            self.assertEqual(result["report_meta"]["status"], "unavailable")

    def test_native_error_cannot_be_promoted_by_nonempty_text(self):
        with tempfile.TemporaryDirectory() as td:
            service, tid = self.run_task(Path(td), meta={"aborted": True})
            self.assertFalse(service.get_result(tid)["result"]["valid"])

    def test_native_length_stop_keeps_partial_answer_not_the_truncation_notice(self):
        with tempfile.TemporaryDirectory() as td:
            service, tid = self.run_task(Path(td), text="Output truncated.",
                                        meta={"stopReason": "length", "finalAssistantVisibleText": "已检查两张，尚未"})
            result = service.get_result(tid)["result"]
            self.assertEqual(result["report_text"], "已检查两张，尚未")
            self.assertEqual(result["report_meta"]["status"], "partial")

    def test_native_secret_redaction_and_unknown_references(self):
        with tempfile.TemporaryDirectory() as td:
            service, tid = self.run_task(Path(td), text="test-secret [E999]")
            result = service.get_result(tid)["result"]
            self.assertNotIn("test-secret", result["report_text"])
            self.assertEqual(result["report_meta"]["unresolved_citation_refs"], ["E999"])

    def test_plain_conversation_uses_same_text_path(self):
        with tempfile.TemporaryDirectory() as td:
            service, tid = self.run_task(Path(td), mode="auto", evidence=False)
            self.assertEqual(service.get_task(tid)["mode"], "conversation")
            self.assertTrue(service.get_result(tid)["result"]["valid"])
