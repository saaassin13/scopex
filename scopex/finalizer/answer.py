from __future__ import annotations

from dataclasses import dataclass

from scopex.evidence.catalog import EvidenceCatalog
from scopex.finalizer.claims import Claim, ClaimKind, ClaimRelation, ClaimSet


@dataclass(frozen=True, slots=True)
class AnswerItem:
    """One product-facing statement backed by validated claim identities."""

    text: str
    claim_ids: tuple[str, ...]
    kind: str

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "claim_ids": list(self.claim_ids),
            "kind": self.kind,
        }


@dataclass(frozen=True, slots=True)
class ProductAnswer:
    """Result-first projection over an already validated ClaimSet.

    This object does not investigate, call a model, execute tools, or create new
    factual content. Observed text/command facts remain grounded in runtime-owned
    raw Evidence, matching the deterministic renderer's trust boundary. Visual
    fact topics are allowed because the Fresh Finalizer re-opened the SHA-verified
    original images in the same finalization call. `final.txt` remains the
    deterministic trust/audit fallback.
    """

    conclusion: tuple[AnswerItem, ...]
    explanation: tuple[AnswerItem, ...]
    execution: tuple[AnswerItem, ...]
    recommendations: tuple[AnswerItem, ...]

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "conclusion": [item.to_dict() for item in self.conclusion],
            "explanation": [item.to_dict() for item in self.explanation],
            "execution": [item.to_dict() for item in self.execution],
            "recommendations": [item.to_dict() for item in self.recommendations],
        }


def _has_image_evidence(claim: Claim, catalog: EvidenceCatalog) -> bool:
    return any(
        catalog.get(ref).metadata.get("evidence_type") == "image"
        for ref in claim.evidence_refs
    )


def _raw_evidence_text(claim: Claim, catalog: EvidenceCatalog) -> str:
    rows: list[str] = []
    seen: set[str] = set()
    for ref in claim.evidence_refs:
        if ref in seen:
            continue
        seen.add(ref)
        raw = catalog.get(ref).raw.strip()
        if raw:
            rows.append(raw)
    return "；".join(rows)


def _safe_claim_text(claim: Claim, catalog: EvidenceCatalog) -> str:
    """Render one validated claim without silently upgrading model prose.

    The Claim validator proves structure/ref integrity, not free-form semantic
    entailment. For observed text/command facts we therefore use exact raw
    Evidence, as the deterministic renderer does. Other epistemic classes keep
    explicit labels so a hypothesis/unknown cannot appear as an observed fact.
    """

    if claim.kind is ClaimKind.FACT:
        if _has_image_evidence(claim, catalog):
            return claim.topic
        raw = _raw_evidence_text(claim, catalog)
        return raw or "已形成直接观察事实"

    if claim.relation is ClaimRelation.TEMPORAL_ASSOCIATION:
        raw = _raw_evidence_text(claim, catalog)
        subject = raw or "相关证据"
        return f"{subject}；当前仅支持时间关联，未证明因果。"

    if claim.relation is ClaimRelation.CAUSAL_HYPOTHESIS:
        return f"待验证假设：{claim.topic}"

    return f"尚不能确定：{claim.topic}"


def _item(
    claim: Claim,
    catalog: EvidenceCatalog,
    *,
    text: str | None = None,
    kind: str | None = None,
) -> AnswerItem:
    return AnswerItem(
        text=text if text is not None else _safe_claim_text(claim, catalog),
        claim_ids=(claim.id,),
        kind=kind or claim.kind.value,
    )


def _ordered_summary_claims(claims: ClaimSet) -> list[Claim]:
    by_id = claims.by_id()
    ordered: list[Claim] = []
    seen: set[str] = set()
    for claim_id in claims.summary_claim_ids:
        claim = by_id.get(claim_id)
        if claim is None or claim.id in seen:
            continue
        seen.add(claim.id)
        ordered.append(claim)
    for claim in claims.claims:
        if claim.id not in seen:
            ordered.append(claim)
    return ordered


def _has_action_verification_evidence(claim: Claim, catalog: EvidenceCatalog) -> bool:
    """Require an explicit trusted semantic marker for the execution section.

    A generic shell Tool Result is not equivalent to a business action. The
    Evidence producer/capability boundary must explicitly mark post-action state
    evidence before a claim may appear as an execution result.
    """

    return any(
        catalog.get(ref).metadata.get("evidence_role") == "action_verification"
        for ref in claim.evidence_refs
    )


def compose_product_answer(claims: ClaimSet, catalog: EvidenceCatalog) -> ProductAnswer:
    """Build the minimal result-first product answer from validated claims only.

    This projection cannot introduce new factual/causal content. Future language
    polishing may replace it only if the same Claim-bounded trust contract and
    deterministic fallback are preserved.
    """

    ordered = _ordered_summary_claims(claims)
    if not ordered:
        return ProductAnswer((), (), (), ())

    # Prefer one observed fact as the headline when available. If no observed
    # fact exists, the strongest available claim may still be shown, but its
    # text keeps the explicit hypothesis/unknown label.
    conclusion = next((claim for claim in ordered if claim.kind is ClaimKind.FACT), ordered[0])
    explanation_claims = [claim for claim in ordered if claim.id != conclusion.id][:4]

    execution_claims = [
        claim
        for claim in ordered
        if claim.kind is ClaimKind.FACT
        and _has_action_verification_evidence(claim, catalog)
    ][:4]

    unresolved = [
        claim
        for claim in ordered
        if claim.kind is ClaimKind.UNKNOWN
        or claim.relation is ClaimRelation.CAUSAL_HYPOTHESIS
    ][:3]

    return ProductAnswer(
        conclusion=(_item(conclusion, catalog),),
        explanation=tuple(_item(claim, catalog) for claim in explanation_claims),
        execution=tuple(_item(claim, catalog) for claim in execution_claims),
        recommendations=tuple(
            _item(
                claim,
                catalog,
                text=f"继续验证：{claim.topic}",
                kind="recommendation",
            )
            for claim in unresolved
        ),
    )
