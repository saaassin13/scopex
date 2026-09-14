from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scopex.agent.trace import AgentTrace, ToolCall, ToolResult
from scopex.evidence.catalog import EvidenceCatalog
from scopex.evidence.collector import EvidenceCollector
from scopex.evidence.projector import OpenClawEvidenceProjector
from scopex.events.progress import InMemoryEventSink


class ImageEvidenceQualificationTests(unittest.TestCase):
    def _projector(self, root: Path):
        catalog = EvidenceCatalog('task-1', 'agent:sx:task-1')
        collector = EvidenceCollector(catalog, InMemoryEventSink())
        projector = OpenClawEvidenceProjector(
            collector,
            sandbox_binds=(f'{root}:/agent-data:ro',),
            max_claim_images=2,
        )
        return catalog, projector

    def test_omitted_visual_batch_is_not_claim_grade(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            images = root / 'images'
            images.mkdir()
            for name in ('a.jpg', 'b.jpg'):
                (images / name).write_bytes(name.encode())
            catalog, projector = self._projector(root)
            trace = AgentTrace(
                calls=(ToolCall('view-1', 'view_image', {
                    'paths': ['/agent-data/images/a.jpg', '/agent-data/images/b.jpg'],
                    'prompt': 'inspect fogging',
                }),),
                results=(ToolResult('view-1', '[1 images omitted from context; rerun with fewer images]'),),
            )
            projector.process_trace(trace)
            self.assertEqual(catalog.items, ())

    def test_complete_two_image_view_can_be_claim_grade(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            images = root / 'images'
            images.mkdir()
            for name in ('a.jpg', 'b.jpg'):
                (images / name).write_bytes(name.encode())
            catalog, projector = self._projector(root)
            trace = AgentTrace(
                calls=(ToolCall('view-2', 'view_image', {
                    'paths': ['/agent-data/images/a.jpg', '/agent-data/images/b.jpg'],
                    'prompt': 'inspect fogging',
                }),),
                results=(ToolResult('view-2', 'Loaded 2 images into private model context'),),
            )
            projector.process_trace(trace)
            self.assertEqual(len(catalog.items), 2)
            self.assertTrue(all(item.metadata['evidence_type'] == 'image' for item in catalog.items))


if __name__ == '__main__':
    unittest.main()
