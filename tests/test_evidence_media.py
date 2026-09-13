from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from scopex.evidence.catalog import EvidenceCatalog
from scopex.evidence.media import EvidenceMediaLoader


class EvidenceMediaTests(unittest.TestCase):
    def catalog_with_image(self, source: str, sha256: str, *, media_type="image/jpeg"):
        catalog = EvidenceCatalog("task-1", "agent:sx:task-1")
        catalog.add(
            source=source,
            raw=f"image:{Path(source).name}",
            tool_call_id="c1",
            metadata={
                "evidence_type": "image",
                "sha256": sha256,
                "media_type": media_type,
            },
        )
        return catalog

    def test_loader_reopens_same_image_and_returns_data_url(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            image = root / "frame.jpg"
            payload = b"jpeg-bytes"
            image.write_bytes(payload)
            sha = hashlib.sha256(payload).hexdigest()
            catalog = self.catalog_with_image("/agent-data/frame.jpg", sha)
            loader = EvidenceMediaLoader((f"{root}:/agent-data:ro",))

            loaded = loader.load(catalog)

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].ref, "E1")
            self.assertEqual(loaded[0].sha256, sha)
            self.assertTrue(loaded[0].data_url.startswith("data:image/jpeg;base64,"))

    def test_loader_rejects_changed_image(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            image = root / "frame.jpg"
            image.write_bytes(b"before")
            sha = hashlib.sha256(b"before").hexdigest()
            catalog = self.catalog_with_image("/agent-data/frame.jpg", sha)
            image.write_bytes(b"after")
            loader = EvidenceMediaLoader((f"{root}:/agent-data:ro",))

            with self.assertRaisesRegex(ValueError, "image_evidence_changed:E1"):
                loader.load(catalog)

    def test_loader_rejects_unresolvable_image(self):
        sha = hashlib.sha256(b"x").hexdigest()
        catalog = self.catalog_with_image("/agent-data/missing.jpg", sha)
        loader = EvidenceMediaLoader(())

        with self.assertRaisesRegex(ValueError, "image_evidence_unresolvable:E1"):
            loader.load(catalog)

    def test_loader_rejects_too_many_images_instead_of_silent_omission(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            catalog = EvidenceCatalog("task-1", "agent:sx:task-1")
            for index in range(3):
                payload = f"image-{index}".encode()
                path = root / f"{index}.jpg"
                path.write_bytes(payload)
                catalog.add(
                    source=f"/agent-data/{index}.jpg",
                    raw=f"image:{index}.jpg",
                    metadata={
                        "evidence_type": "image",
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "media_type": "image/jpeg",
                    },
                )
            loader = EvidenceMediaLoader((f"{root}:/agent-data:ro",), max_images=2)

            with self.assertRaisesRegex(ValueError, "too_many_image_evidence:3>2"):
                loader.load(catalog)


if __name__ == "__main__":
    unittest.main()
