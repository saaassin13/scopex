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
    factual content. Product wording is either an exact validated claim topic or
    a fixed non-factual label around one. `final.txt` remains the deterministic
    trust/audit fallback.
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


def _item(claim: Claim, *, text: str | None = None, kind: str | None = None) -> AnswerItem:
    return AnswerItem(
        text=text or claim.topic,
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

    The projection intentionally does not paraphrase factual/causal content.
    Future language polishing may replace this implementation only if it keeps
    the same Claim-bounded contract and preserves deterministic fallback.
    """

    ordered = _ordered_summary_claims(claims)
    if not ordered:
        return ProductAnswer((), (), (), ())

    conclusion_claims = ordered[:1]
    explanation_claims = ordered[1:5]

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
        conclusion=tuple(_item(claim) for claim in conclusion_claims),
        explanation=tuple(_item(claim) for claim in explanation_claims),
        execution=tuple(_item(claim) for claim in execution_claims),
        recommendations=tuple(
            _item(claim, text=f"继续验证：{claim.topic}", kind="recommendation")
            for claim in unresolved
        ),
    )
