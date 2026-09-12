from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scopex.evidence.catalog import EvidenceCatalog
from scopex.finalizer.claims import ClaimSet, claim_set_from_dict
from scopex.finalizer.renderer import render_claims
from scopex.finalizer.validator import validate_claim_payload


@dataclass(frozen=True, slots=True)
class FinalizationResult:
    valid: bool
    errors: tuple[str, ...]
    claims: ClaimSet | None
    rendered: str | None


class FinalizationService:
    """Validate structured model output before any user-facing rendering."""

    def finalize(self, payload: Any, catalog: EvidenceCatalog) -> FinalizationResult:
        errors = tuple(validate_claim_payload(payload, catalog))
        if errors:
            return FinalizationResult(False, errors, None, None)
        claims = claim_set_from_dict(payload)
        rendered = render_claims(claims, catalog)
        return FinalizationResult(True, (), claims, rendered)
