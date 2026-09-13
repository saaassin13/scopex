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
            max_claim_images=4,
        )

    def test_large_multi_image_view_is_screening_not_claim_grade_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = [f"{letter}.png" for letter in "abcde"]
            for name in names:
                (root / name).write_bytes(b"png-" + name.encode())
            catalog, projector = self.make_projector(f"{root}:/agent-data/images:ro")
            trace = AgentTrace(
                calls=(ToolCall(
                    "screen",
                    "view_image",
                    {
                        "paths": [f"/agent-data/images/{name}" for name in names],
                        "prompt": "screen candidates",
                    },
                ),),
                results=(ToolResult("screen", "Loaded 5 images"),),
            )

            projected = projector.process_trace(trace)

            self.assertEqual(projected, ())
            self.assertEqual(catalog.items, ())
            self.assertIn("screen", projector.processed_call_ids)

    def test_bounded_final_image_set_is_promoted_without_singleton_churn(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = ("a.png", "b.png", "c.png")
            for name in names:
                (root / name).write_bytes(b"png-" + name.encode())
            catalog, projector = self.make_projector(f"{root}:/agent-data/images:ro")
            trace = AgentTrace(
                calls=(ToolCall(
                    "confirm",
                    "view_image",
                    {
                        "paths": [f"/agent-data/images/{name}" for name in names],
                        "prompt": "confirm final bounded evidence set",
                    },
                ),),
                results=(ToolResult("confirm", "Loaded 3 images"),),
            )

            projector.process_trace(trace)

            self.assertEqual(len(catalog.items), 3)
            self.assertEqual(
                sorted(Path(item.source).name for item in catalog.items),
                sorted(names),
            )
            self.assertTrue(all(item.metadata["evidence_type"] == "image" for item in catalog.items))
            self.assertTrue(
                all(item.metadata["evidence_role"] == "claim_grade_bounded_image_set" for item in catalog.items)
            )
            self.assertTrue(all(item.metadata["view_set_size"] == 3 for item in catalog.items))

    def test_single_original_view_is_still_claim_grade(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.png").write_bytes(b"png-a")
            catalog, projector = self.make_projector(f"{root}:/agent-data/images:ro")
            trace = AgentTrace(
                calls=(ToolCall(
                    "confirm-one",
                    "view_image",
                    {"path": "/agent-data/images/a.png"},
                ),),
                results=(ToolResult("confirm-one", "Loaded 1 image"),),
            )

            projector.process_trace(trace)

            self.assertEqual(len(catalog.items), 1)
            self.assertEqual(catalog.items[0].metadata["view_set_size"], 1)

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
