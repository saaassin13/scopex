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
    def projector(self, *, binds=(), exec_host="sandbox", **kwargs):
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
                **kwargs,
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

    def test_exec_projects_claim_grade_lines_with_full_result_hash(self):
        catalog, _, projector = self.projector(exec_host="gateway")
        content = "20\nMem: 121Gi 56Gi 25Gi\nPID CPU MEM COMMAND\n123 88.0 2.4 vllm\n"
        trace = AgentTrace(
            calls=(
                ToolCall(
                    "c2",
                    "exec",
                    {"command": "inspect-system", "title": "system snapshot"},
                ),
            ),
            results=(ToolResult("c2", content),),
        )

        projector.process_trace(trace)

        self.assertEqual(len(catalog.items), 4)
        self.assertEqual(
            [item.raw for item in catalog.items],
            ["20", "Mem: 121Gi 56Gi 25Gi", "PID CPU MEM COMMAND", "123 88.0 2.4 vllm"],
        )
        expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.assertTrue(all(item.metadata["evidence_type"] == "command_line" for item in catalog.items))
        self.assertTrue(all(item.metadata["exec_host"] == "gateway" for item in catalog.items))
        self.assertTrue(all(item.metadata["command"] == "inspect-system" for item in catalog.items))
        self.assertTrue(all(item.metadata["result_sha256"] == expected_hash for item in catalog.items))
        self.assertEqual([item.metadata["line_number"] for item in catalog.items], [1, 2, 3, 4])

    def test_exec_call_host_overrides_runtime_default_for_provenance(self):
        catalog, _, projector = self.projector(exec_host="sandbox")
        trace = AgentTrace(
            calls=(
                ToolCall(
                    "c-host",
                    "exec",
                    {"command": "hostname", "host": "gateway"},
                ),
            ),
            results=(ToolResult("c-host", "spark-host\n"),),
        )

        projector.process_trace(trace)

        self.assertEqual(catalog.items[0].metadata["exec_host"], "gateway")

    def test_exec_projection_is_bounded_by_lines_and_chars(self):
        catalog, _, projector = self.projector(
            max_exec_lines=2,
            max_exec_line_chars=5,
            max_exec_chars=8,
        )
        trace = AgentTrace(
            calls=(ToolCall("c-bounded", "exec", {"command": "x"}),),
            results=(ToolResult("c-bounded", "abcdef\n123456\nthird\n"),),
        )

        projector.process_trace(trace)

        self.assertEqual([item.raw for item in catalog.items], ["abcde", "123"])
        self.assertTrue(catalog.items[0].metadata["line_truncated"])
        self.assertTrue(catalog.items[1].metadata["line_truncated"])

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

    def test_image_identity_includes_digest(self):
        catalog = EvidenceCatalog("task-1", "agent:sx:task-1")
        first = catalog.add(
            source="/agent-data/frame.jpg",
            raw="image:frame.jpg",
            metadata={"evidence_type": "image", "sha256": "a" * 64},
        )
        same = catalog.add(
            source="/agent-data/frame.jpg",
            raw="image:frame.jpg",
            metadata={"evidence_type": "image", "sha256": "a" * 64},
        )
        changed = catalog.add(
            source="/agent-data/frame.jpg",
            raw="image:frame.jpg",
            metadata={"evidence_type": "image", "sha256": "b" * 64},
        )

        self.assertEqual(first.ref, same.ref)
        self.assertNotEqual(first.ref, changed.ref)
        self.assertEqual(len(catalog.items), 2)

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
