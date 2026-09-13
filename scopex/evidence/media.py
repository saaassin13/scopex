from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
from pathlib import Path

from scopex.evidence.catalog import EvidenceCatalog
from scopex.evidence.projector import DataBindResolver


@dataclass(frozen=True, slots=True)
class VerifiedImageEvidence:
    ref: str
    source: str
    media_type: str
    sha256: str
    data_url: str


class EvidenceMediaLoader:
    """Re-open immutable image Evidence for the fresh multimodal finalizer."""

    def __init__(
        self,
        sandbox_binds: tuple[str, ...] = (),
        *,
        max_images: int = 4,
        max_image_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        if max_images <= 0 or max_image_bytes <= 0:
            raise ValueError("image evidence limits must be positive")
        self.resolver = DataBindResolver(sandbox_binds)
        self.max_images = max_images
        self.max_image_bytes = max_image_bytes

    def load(self, catalog: EvidenceCatalog) -> tuple[VerifiedImageEvidence, ...]:
        image_items = [
            item for item in catalog.items
            if item.metadata.get("evidence_type") == "image"
        ]
        if len(image_items) > self.max_images:
            raise ValueError(
                f"too_many_image_evidence:{len(image_items)}>{self.max_images}"
            )

        loaded: list[VerifiedImageEvidence] = []
        for item in image_items:
            expected = item.metadata.get("sha256")
            media_type = item.metadata.get("media_type")
            if not isinstance(expected, str) or not expected:
                raise ValueError(f"image_evidence_missing_sha256:{item.ref}")
            if not isinstance(media_type, str) or not media_type.startswith("image/"):
                raise ValueError(f"image_evidence_invalid_media_type:{item.ref}")

            resolved = self.resolver.resolve(item.source)
            if resolved is None:
                raise ValueError(f"image_evidence_unresolvable:{item.ref}")
            size = resolved.host_path.stat().st_size
            if size > self.max_image_bytes:
                raise ValueError(f"image_evidence_too_large:{item.ref}:{size}")

            data = resolved.host_path.read_bytes()
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected:
                raise ValueError(f"image_evidence_changed:{item.ref}")

            encoded = base64.b64encode(data).decode("ascii")
            loaded.append(
                VerifiedImageEvidence(
                    ref=item.ref,
                    source=item.source,
                    media_type=media_type,
                    sha256=actual,
                    data_url=f"data:{media_type};base64,{encoded}",
                )
            )
        return tuple(loaded)
