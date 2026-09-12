from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from scopex.evidence.catalog import EvidenceCatalog


_KINDS = {"fact", "inference", "unknown"}
_RELATIONS = {"observed", "temporal_association", "causal_hypothesis", "unknown"}
_SCOPES = {"event", "time_window", "component", "global", "unknown"}
_CONFIDENCE = {"high", "medium", "low", "unknown"}
_CLAIM_FIELDS = {
    "id",
    "kind",
    "topic",
    "evidence_refs",
    "confidence",
    "scope",
    "relation",
}


def normalize_claim_payload(payload: Any) -> tuple[Any, tuple[str, ...]]:
    """Normalize only epistemically safe redundant claim fields.

    ``relation`` is more specific than ``kind`` for non-observed claims. Runtime
    may therefore deterministically map temporal/causal relations to inference
    and unknown relations to unknown. These mappings preserve or weaken claim
    strength; they never upgrade a claim to an observed fact.

    Observed claims are intentionally not repaired: ``relation=observed`` still
    requires the model to emit ``kind=fact`` and pass the normal fact evidence
    checks. The input object is never mutated.
    """

    if not isinstance(payload, Mapping):
        return payload, ()

    normalized = dict(payload)
    claims = payload.get("claims")
    if not isinstance(claims, list):
        return normalized, ()

    rows: list[Any] = []
    changes: list[str] = []
    for index, claim in enumerate(claims):
        if not isinstance(claim, Mapping):
            rows.append(claim)
            continue
        row = dict(claim)
        relation = row.get("relation")
        current = row.get("kind")
        expected: str | None = None
        if relation in {"temporal_association", "causal_hypothesis"}:
            expected = "inference"
        elif relation == "unknown":
            expected = "unknown"

        if expected is not None and current != expected:
            before = "<missing>" if current is None else str(current)
            row["kind"] = expected
            changes.append(f"claims[{index}].kind:{before}->{expected}")
        rows.append(row)

    normalized["claims"] = rows
    return normalized, tuple(changes)


def _unique_strings(value: Any) -> tuple[bool, list[str]]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return False, []
    if len(value) != len(set(value)):
        return False, value
    return True, value


def _claim_signature(claim: Mapping[str, Any], refs: list[str]) -> tuple[Any, ...] | None:
    """Return the identity of the text the deterministic renderer would show."""

    kind = claim.get("kind")
    relation = claim.get("relation")
    scope = claim.get("scope")
    confidence = claim.get("confidence")
    topic = claim.get("topic")
    if kind not in _KINDS or relation not in _RELATIONS or scope not in _SCOPES:
        return None

    canonical_refs = tuple(sorted(refs))
    if kind == "fact" and relation == "observed":
        # Fact topic/confidence are intentionally not rendered.
        return "fact", scope, canonical_refs
    if relation == "temporal_association":
        return "temporal_association", scope, confidence, canonical_refs
    if relation == "causal_hypothesis":
        return "causal_hypothesis", scope, confidence, canonical_refs, topic
    if kind == "unknown" and relation == "unknown":
        return "unknown", scope, canonical_refs, topic
    return kind, relation, scope, confidence, canonical_refs, topic


def validate_claim_payload(payload: Any, catalog: EvidenceCatalog) -> list[str]:
    """Validate generic epistemic structure.

    The validator contains no domain/fixture semantics. It must return errors for
    malformed model output rather than raising due to unhashable or missing
    fields.
    """

    errors: list[str] = []
    if not isinstance(payload, Mapping):
        return ["top_level_object"]
    if set(payload) != {"claims", "summary_claim_ids"}:
        errors.append("top_level_fields")

    claims = payload.get("claims")
    if not isinstance(claims, list) or not 1 <= len(claims) <= 20:
        return errors + ["claims"]

    valid_refs = catalog.refs
    seen_ids: set[str] = set()
    seen_signatures: set[tuple[Any, ...]] = set()

    for index, claim in enumerate(claims):
        prefix = f"claims[{index}]"
        if not isinstance(claim, Mapping):
            errors.append(prefix)
            continue
        if set(claim) != _CLAIM_FIELDS:
            errors.append(prefix + ".fields")

        cid = claim.get("id")
        if (
            not isinstance(cid, str)
            or not cid.startswith("C")
            or not cid[1:].isdigit()
            or int(cid[1:] or "0") <= 0
            or cid in seen_ids
        ):
            errors.append(prefix + ".id")
        else:
            seen_ids.add(cid)

        kind = claim.get("kind")
        relation = claim.get("relation")
        scope = claim.get("scope")
        confidence = claim.get("confidence")
        topic = claim.get("topic")
        refs_ok, refs = _unique_strings(claim.get("evidence_refs"))

        if kind not in _KINDS:
            errors.append(prefix + ".kind")
        if relation not in _RELATIONS:
            errors.append(prefix + ".relation")
        if scope not in _SCOPES:
            errors.append(prefix + ".scope")
        if confidence not in _CONFIDENCE:
            errors.append(prefix + ".confidence")
        if not isinstance(topic, str) or not topic.strip() or len(topic) > 160:
            errors.append(prefix + ".topic")
        if not refs_ok or any(ref not in valid_refs for ref in refs):
            errors.append(prefix + ".evidence_refs")
            refs = []

        if kind == "fact":
            if not refs:
                errors.append(prefix + ".fact_requires_evidence")
            if relation != "observed":
                errors.append(prefix + ".fact_relation")
            if confidence not in {"high", "medium"}:
                errors.append(prefix + ".fact_confidence")
            if scope == "unknown":
                errors.append(prefix + ".fact_scope")

        elif kind == "inference":
            if not refs:
                errors.append(prefix + ".inference_requires_evidence")
            if relation not in {"temporal_association", "causal_hypothesis"}:
                errors.append(prefix + ".inference_relation")
            if relation == "temporal_association":
                if len(refs) < 2:
                    errors.append(prefix + ".temporal_requires_two_refs")
                if confidence not in {"high", "medium", "low"}:
                    errors.append(prefix + ".temporal_confidence")
            if relation == "causal_hypothesis" and confidence not in {"medium", "low"}:
                errors.append(prefix + ".causal_confidence")

        elif kind == "unknown":
            if relation != "unknown":
                errors.append(prefix + ".unknown_relation")
            if confidence != "unknown":
                errors.append(prefix + ".unknown_confidence")

        signature = _claim_signature(claim, refs)
        if signature is not None:
            if signature in seen_signatures:
                errors.append(prefix + ".duplicate_claim")
            else:
                seen_signatures.add(signature)

    summary_ok, summary = _unique_strings(payload.get("summary_claim_ids"))
    if (
        not summary_ok
        or not summary
        or len(summary) > 4
        or any(cid not in seen_ids for cid in summary)
    ):
        errors.append("summary_claim_ids")

    return errors
