from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scopex.evidence.catalog import EvidenceCatalog
from scopex.finalizer.claims import ClaimSet, claim_set_from_dict
from scopex.finalizer.renderer import render_claims
from scopex.finalizer.validator import normalize_claim_payload, validate_claim_payload


@dataclass(frozen=True, slots=True)
class FinalizationResult:
    valid: bool
    errors: tuple[str, ...]
    claims: ClaimSet | None
    rendered: str | None
    normalizations: tuple[str, ...] = ()


class FinalizationService:
    """Validate structured model output before any user-facing rendering."""

    def finalize(self, payload: Any, catalog: EvidenceCatalog) -> FinalizationResult:
        normalized, normalizations = normalize_claim_payload(payload)
        errors = tuple(validate_claim_payload(normalized, catalog))
        if errors:
            return FinalizationResult(False, errors, None, None, normalizations)
        claims = claim_set_from_dict(normalized)
        rendered = render_claims(claims, catalog)
        return FinalizationResult(True, (), claims, rendered, normalizations)
