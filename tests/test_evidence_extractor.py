import unittest

from scopex.agent.trace import AgentTrace, ToolCall, ToolResult
from scopex.evidence.catalog import EvidenceCatalog
from scopex.evidence.collector import EvidenceCollector
from scopex.evidence.extractor import EvidenceExtractionPipeline, ReadResultExtractor
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


if __name__ == "__main__":
    unittest.main()
