from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from scopex.evidence.catalog import EvidenceCatalog
from scopex.evidence.media import EvidenceMediaLoader
from scopex.finalizer.client import FinalizerResponse
from scopex.finalizer.structured import StructuredFinalizer


class FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return FinalizerResponse(
            content=self.content,
            headers_s=0.01,
            first_content_s=0.02,
            elapsed_s=0.03,
            finish_reasons=("stop",),
            done_seen=True,
            usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        )


class MultimodalStructuredFinalizerTests(unittest.TestCase):
    def test_verified_image_is_reattached_and_visual_fact_is_rendered(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            payload = b"fake-image"
            image = root / "frame.jpg"
            image.write_bytes(payload)
            catalog = EvidenceCatalog("t1", "s1")
            catalog.add(
                source="/agent-data/frame.jpg",
                raw="image:frame.jpg",
                tool_call_id="view-1",
                metadata={
                    "evidence_type": "image",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "byte_size": len(payload),
                    "media_type": "image/jpeg",
                },
            )
            client = FakeClient('''{
              "claims": [{
                "id": "C1",
                "kind": "fact",
                "topic": "画面明显模糊",
                "evidence_refs": ["E1"],
                "confidence": "high",
                "scope": "event",
                "relation": "observed"
              }],
              "summary_claim_ids": ["C1"]
            }''')
            finalizer = StructuredFinalizer(
                client,
                model="m",
                media_loader=EvidenceMediaLoader((f"{root}:/agent-data:ro",)),
            )

            result = finalizer.run(user_request="inspect image", catalog=catalog)

            self.assertTrue(result.valid, result.parse_error)
            self.assertEqual(len(client.calls), 1)
            image_inputs = client.calls[0]["image_inputs"]
            self.assertEqual(len(image_inputs), 1)
            self.assertIn("E1 source=/agent-data/frame.jpg", image_inputs[0][0])
            self.assertTrue(image_inputs[0][1].startswith("data:image/jpeg;base64,"))
            self.assertIn("视觉观察｜frame.jpg", result.finalization.rendered)
            self.assertIn("画面明显模糊", result.finalization.rendered)
            self.assertIn("E1 frame.jpg", result.finalization.rendered)

    def test_changed_image_stops_before_model_call(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            image = root / "frame.jpg"
            image.write_bytes(b"before")
            catalog = EvidenceCatalog("t1", "s1")
            catalog.add(
                source="/agent-data/frame.jpg",
                raw="image:frame.jpg",
                metadata={
                    "evidence_type": "image",
                    "sha256": hashlib.sha256(b"before").hexdigest(),
                    "media_type": "image/jpeg",
                },
            )
            image.write_bytes(b"after")
            client = FakeClient('{"claims":[],"summary_claim_ids":[]}')
            finalizer = StructuredFinalizer(
                client,
                model="m",
                media_loader=EvidenceMediaLoader((f"{root}:/agent-data:ro",)),
            )

            result = finalizer.run(user_request="inspect image", catalog=catalog)

            self.assertFalse(result.valid)
            self.assertEqual(result.parse_error, "image_evidence_changed:E1")
            self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
