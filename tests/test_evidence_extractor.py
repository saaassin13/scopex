import unittest

from scopex.agent.trace import AgentTrace, ToolCall, ToolResult
from scopex.evidence.catalog import EvidenceCatalog
from scopex.evidence.collector import EvidenceCollector
from scopex.evidence.extractor import (
    EvidenceExtractionPipeline,
    ReadLineExtractor,
    ReadResultExtractor,
)
from scopex.events.progress import EventType, InMemoryEventSink


class EvidenceExtractorTests(unittest.TestCase):
    def make_pipeline(self, *, max_chars=64):
        events = InMemoryEventSink()
        catalog = EvidenceCatalog("t1", "agent:sx:t1")
        collector = EvidenceCollector(catalog, events)
        pipeline = EvidenceExtractionPipeline(
            collector,
            [ReadResultExtractor(max_chars=max_chars)],
        )
        return events, catalog, pipeline

    def test_completed_read_becomes_runtime_owned_evidence(self):
        events, catalog, pipeline = self.make_pipeline()
        trace = AgentTrace(
            calls=(ToolCall("c1", "read", {"path": "/agent/system.log"}),),
            results=(ToolResult("c1", "worker exited status=137"),),
        )
        added = pipeline.process_trace(trace)
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].ref, "E1")
        self.assertEqual(added[0].source, "/agent/system.log")
        self.assertEqual(added[0].tool_call_id, "c1")
        self.assertEqual(catalog.get("E1").raw, "worker exited status=137")
        self.assertEqual(
            [event.type for event in events.events],
            [EventType.EVIDENCE_ADDED],
        )

    def test_pipeline_processes_each_tool_call_once(self):
        _, catalog, pipeline = self.make_pipeline()
        trace = AgentTrace(
            calls=(ToolCall("c1", "read", {"path": "/agent/app.log"}),),
            results=(ToolResult("c1", "failure evidence"),),
        )
        pipeline.process_trace(trace)
        pipeline.process_trace(trace)
        self.assertEqual(len(catalog.items), 1)
        self.assertEqual(pipeline.processed_call_ids, frozenset({"c1"}))

    def test_non_read_tool_is_not_implicitly_promoted(self):
        _, catalog, pipeline = self.make_pipeline()
        trace = AgentTrace(
            calls=(ToolCall("c1", "exec", {"command": "cat /agent/app.log"}),),
            results=(ToolResult("c1", "large shell output"),),
        )
        self.assertEqual(pipeline.process_trace(trace), ())
        self.assertEqual(catalog.items, ())

    def test_read_result_is_bounded_and_marked_truncated(self):
        _, catalog, pipeline = self.make_pipeline(max_chars=5)
        trace = AgentTrace(
            calls=(ToolCall("c1", "read", {"path": "/agent/a.log"}),),
            results=(ToolResult("c1", "123456789"),),
        )
        pipeline.process_trace(trace)
        item = catalog.get("E1")
        self.assertEqual(item.raw, "12345")
        self.assertTrue(item.metadata["truncated"])
        self.assertEqual(item.metadata["original_chars"], 9)

    def test_evidence_refs_follow_tool_call_order_not_set_order(self):
        _, catalog, pipeline = self.make_pipeline()
        trace = AgentTrace(
            calls=(
                ToolCall("z-call", "read", {"path": "/agent/first.log"}),
                ToolCall("a-call", "read", {"path": "/agent/second.log"}),
            ),
            results=(
                ToolResult("a-call", "second evidence"),
                ToolResult("z-call", "first evidence"),
            ),
        )
        pipeline.process_trace(trace)
        self.assertEqual(catalog.get("E1").source, "/agent/first.log")
        self.assertEqual(catalog.get("E2").source, "/agent/second.log")

    def test_read_line_extractor_creates_exact_line_evidence(self):
        events = InMemoryEventSink()
        catalog = EvidenceCatalog("t1", "agent:sx:t1")
        pipeline = EvidenceExtractionPipeline(
            EvidenceCollector(catalog, events),
            [ReadLineExtractor()],
        )
        trace = AgentTrace(
            calls=(ToolCall("c1", "read", {"path": "/agent/system.log"}),),
            results=(ToolResult("c1", "first line\n\nworker exited status=137\nready\n"),),
        )
        added = pipeline.process_trace(trace)
        self.assertEqual([item.raw for item in added], [
            "first line",
            "worker exited status=137",
            "ready",
        ])
        self.assertEqual([item.ref for item in added], ["E1", "E2", "E3"])
        self.assertEqual([item.metadata["line_number"] for item in added], [1, 3, 4])
        self.assertTrue(all(item.source == "/agent/system.log" for item in added))

    def test_read_line_extractor_bounds_lines_and_line_length(self):
        events = InMemoryEventSink()
        catalog = EvidenceCatalog("t1", "agent:sx:t1")
        pipeline = EvidenceExtractionPipeline(
            EvidenceCollector(catalog, events),
            [ReadLineExtractor(max_lines=2, max_line_chars=4)],
        )
        trace = AgentTrace(
            calls=(ToolCall("c1", "read", {"path": "/agent/a.log"}),),
            results=(ToolResult("c1", "123456\nsecond\nthird"),),
        )
        pipeline.process_trace(trace)
        self.assertEqual(len(catalog.items), 2)
        self.assertEqual(catalog.get("E1").raw, "1234")
        self.assertTrue(catalog.get("E1").metadata["line_truncated"])
        self.assertEqual(catalog.get("E2").raw, "seco")
        self.assertTrue(catalog.get("E2").metadata["line_truncated"])

    def test_equal_text_on_different_lines_keeps_distinct_evidence_identity(self):
        events = InMemoryEventSink()
        catalog = EvidenceCatalog("t1", "agent:sx:t1")
        pipeline = EvidenceExtractionPipeline(
            EvidenceCollector(catalog, events),
            [ReadLineExtractor()],
        )
        trace = AgentTrace(
            calls=(ToolCall("c1", "read", {"path": "/agent/repeat.log"}),),
            results=(ToolResult("c1", "heartbeat ok\nheartbeat ok\n"),),
        )
        pipeline.process_trace(trace)
        self.assertEqual(len(catalog.items), 2)
        self.assertEqual(catalog.get("E1").raw, "heartbeat ok")
        self.assertEqual(catalog.get("E2").raw, "heartbeat ok")
        self.assertEqual(catalog.get("E1").metadata["line_number"], 1)
        self.assertEqual(catalog.get("E2").metadata["line_number"], 2)


if __name__ == "__main__":
    unittest.main()
