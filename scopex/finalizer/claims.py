from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ClaimKind(str, Enum):
    FACT = "fact"
    INFERENCE = "inference"
    UNKNOWN = "unknown"


class ClaimRelation(str, Enum):
    OBSERVED = "observed"
    TEMPORAL_ASSOCIATION = "temporal_association"
    CAUSAL_HYPOTHESIS = "causal_hypothesis"
    UNKNOWN = "unknown"


class ClaimScope(str, Enum):
    EVENT = "event"
    TIME_WINDOW = "time_window"
    COMPONENT = "component"
    GLOBAL = "global"
    UNKNOWN = "unknown"


class ClaimConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Claim:
    id: str
    kind: ClaimKind
    topic: str
    evidence_refs: tuple[str, ...]
    confidence: ClaimConfidence
    scope: ClaimScope
    relation: ClaimRelation


@dataclass(frozen=True, slots=True)
class ClaimSet:
    claims: tuple[Claim, ...]
    summary_claim_ids: tuple[str, ...]

    def by_id(self) -> dict[str, Claim]:
        return {claim.id: claim for claim in self.claims}


def claim_set_from_dict(value: dict) -> ClaimSet:
    """Convert an already-validated payload into typed claims."""

    claims = tuple(
        Claim(
            id=row["id"],
            kind=ClaimKind(row["kind"]),
            topic=row["topic"],
            evidence_refs=tuple(row["evidence_refs"]),
            confidence=ClaimConfidence(row["confidence"]),
            scope=ClaimScope(row["scope"]),
            relation=ClaimRelation(row["relation"]),
        )
        for row in value["claims"]
    )
    return ClaimSet(claims=claims, summary_claim_ids=tuple(value["summary_claim_ids"]))
