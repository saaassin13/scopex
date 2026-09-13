from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scopex.agent.trace import AgentTrace, ToolCall, ToolResult
from scopex.evidence.catalog import EvidenceCatalog
from scopex.evidence.collector import EvidenceCollector
from scopex.evidence.projector import OpenClawEvidenceProjector
from scopex.events.progress import InMemoryEventSink


class ImageWorkingSetTests(unittest.TestCase):
    def make_projector(self, bind: str):
        catalog = EvidenceCatalog("task-1", "agent:sx:task-1")
        collector = EvidenceCollector(catalog, InMemoryEventSink())
        return catalog, OpenClawEvidenceProjector(
            collector,
            sandbox_binds=(bind,),
        )

    def test_multi_image_view_is_screening_not_claim_grade_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in ("a.png", "b.png", "c.png"):
                (root / name).write_bytes(b"png-" + name.encode())
            catalog, projector = self.make_projector(f"{root}:/agent-data/images:ro")
            trace = AgentTrace(
                calls=(ToolCall(
                    "screen",
                    "view_image",
                    {
                        "paths": [
                            "/agent-data/images/a.png",
                            "/agent-data/images/b.png",
                            "/agent-data/images/c.png",
                        ],
                        "prompt": "screen candidates",
                    },
                ),),
                results=(ToolResult("screen", "Loaded 3 images"),),
            )

            projected = projector.process_trace(trace)

            self.assertEqual(projected, ())
            self.assertEqual(catalog.items, ())
            self.assertIn("screen", projector.processed_call_ids)

    def test_single_original_reopen_is_promoted_after_batch_screening(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in ("a.png", "b.png"):
                (root / name).write_bytes(b"png-" + name.encode())
            catalog, projector = self.make_projector(f"{root}:/agent-data/images:ro")
            trace = AgentTrace(
                calls=(
                    ToolCall(
                        "screen",
                        "view_image",
                        {"paths": [
                            "/agent-data/images/a.png",
                            "/agent-data/images/b.png",
                        ]},
                    ),
                    ToolCall(
                        "confirm",
                        "view_image",
                        {"path": "/agent-data/images/b.png", "prompt": "confirm final evidence"},
                    ),
                ),
                results=(
                    ToolResult("screen", "Loaded 2 images"),
                    ToolResult("confirm", "Loaded 1 image"),
                ),
            )

            projector.process_trace(trace)

            self.assertEqual(len(catalog.items), 1)
            item = catalog.items[0]
            self.assertEqual(item.source, "/agent-data/images/b.png")
            self.assertEqual(item.metadata["evidence_type"], "image")
            self.assertEqual(item.metadata["evidence_role"], "claim_grade_single_image")

    def test_scratch_image_is_never_promoted_by_read_only_resolver(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "source.png").write_bytes(b"source")
            catalog, projector = self.make_projector(f"{root}:/agent-data/images:ro")
            trace = AgentTrace(
                calls=(ToolCall(
                    "scratch",
                    "view_image",
                    {"path": "/task-scratch/contact-sheet.png"},
                ),),
                results=(ToolResult("scratch", "Loaded 1 image"),),
            )

            projector.process_trace(trace)

            self.assertEqual(catalog.items, ())


if __name__ == "__main__":
    unittest.main()
