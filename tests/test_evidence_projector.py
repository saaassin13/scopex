from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from scopex.agent.trace import AgentTrace, ToolCall, ToolResult
from scopex.evidence.catalog import EvidenceCatalog
from scopex.evidence.collector import EvidenceCollector
from scopex.evidence.projector import DataBindResolver, OpenClawEvidenceProjector
from scopex.events.progress import InMemoryEventSink


class EvidenceProjectorTests(unittest.TestCase):
    def projector(self, *, binds=(), exec_host="sandbox", max_exec_chars=12000):
        catalog = EvidenceCatalog("task-1", "agent:sx:task-1")
        events = InMemoryEventSink()
        collector = EvidenceCollector(catalog, events)
        return (
            catalog,
            events,
            OpenClawEvidenceProjector(
                collector,
                sandbox_binds=tuple(binds),
                exec_host=exec_host,
                max_exec_chars=max_exec_chars,
            ),
        )

    def test_read_projects_exact_nonempty_lines(self):
        catalog, _, projector = self.projector()
        trace = AgentTrace(
            calls=(ToolCall("c1", "read", {"path": "/agent/app.log"}),),
            results=(ToolResult("c1", "first\n\nsecond\n"),),
        )

        projector.process_trace(trace)

        self.assertEqual([item.raw for item in catalog.items], ["first", "second"])
        self.assertEqual([item.metadata["line_number"] for item in catalog.items], [1, 3])
        self.assertTrue(all(item.metadata["evidence_type"] == "file_line" for item in catalog.items))

    def test_exec_projects_bounded_excerpt_and_full_result_hash(self):
        catalog, _, projector = self.projector(exec_host="gateway", max_exec_chars=120)
        content = "A" * 200 + "\nMIDDLE\n" + "Z" * 200
        trace = AgentTrace(
            calls=(
                ToolCall(
                    "c2",
                    "exec",
                    {"command": "free -h", "title": "memory"},
                ),
            ),
            results=(ToolResult("c2", content),),
        )

        projector.process_trace(trace)

        self.assertEqual(len(catalog.items), 1)
        item = catalog.items[0]
        self.assertEqual(item.metadata["evidence_type"], "command_output")
        self.assertEqual(item.metadata["exec_host"], "gateway")
        self.assertEqual(item.metadata["command"], "free -h")
        self.assertTrue(item.metadata["truncated"])
        self.assertEqual(item.metadata["original_chars"], len(content))
        self.assertEqual(
            item.metadata["result_sha256"],
            hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
        self.assertLessEqual(len(item.raw), 140)
        self.assertIn("truncated", item.raw)

    def test_view_image_freezes_identity_from_explicit_read_only_bind(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            images = root / "images"
            images.mkdir()
            image = images / "frame.jpg"
            image.write_bytes(b"fake-jpeg-bytes")
            bind = f"{root}:/agent-data:ro"
            catalog, _, projector = self.projector(binds=(bind,))
            trace = AgentTrace(
                calls=(
                    ToolCall(
                        "c3",
                        "view_image",
                        {
                            "path": "/agent-data/images/frame.jpg",
                            "prompt": "inspect quality",
                        },
                    ),
                ),
                results=(ToolResult("c3", "Loaded 1 image into private model context"),),
            )

            projector.process_trace(trace)

            self.assertEqual(len(catalog.items), 1)
            item = catalog.items[0]
            self.assertEqual(item.source, "/agent-data/images/frame.jpg")
            self.assertEqual(item.raw, "image:frame.jpg")
            self.assertEqual(item.metadata["evidence_type"], "image")
            self.assertEqual(item.metadata["byte_size"], len(b"fake-jpeg-bytes"))
            self.assertEqual(item.metadata["media_type"], "image/jpeg")
            self.assertEqual(item.metadata["view_prompt"], "inspect quality")
            self.assertEqual(
                item.metadata["sha256"],
                hashlib.sha256(b"fake-jpeg-bytes").hexdigest(),
            )

    def test_unresolved_image_is_not_promoted_to_strong_evidence(self):
        catalog, _, projector = self.projector()
        trace = AgentTrace(
            calls=(ToolCall("c4", "view_image", {"path": "/outside/frame.jpg"}),),
            results=(ToolResult("c4", "Loaded 1 image"),),
        )

        projector.process_trace(trace)

        self.assertEqual(catalog.items, ())

    def test_progress_card_is_not_evidence(self):
        catalog, _, projector = self.projector()
        trace = AgentTrace(
            calls=(ToolCall("c5", "progress_card", {"action": "update"}),),
            results=(ToolResult("c5", "progress updated"),),
        )

        projector.process_trace(trace)

        self.assertEqual(catalog.items, ())

    def test_bind_resolver_never_escapes_configured_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inside = root / "inside.txt"
            inside.write_text("ok", encoding="utf-8")
            resolver = DataBindResolver((f"{root}:/agent-data:ro",))
            resolved = resolver.resolve("/agent-data/inside.txt")
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.host_path, inside.resolve())
            self.assertIsNone(resolver.resolve("/agent-data/../etc/passwd"))


if __name__ == "__main__":
    unittest.main()
