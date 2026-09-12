from __future__ import annotations

from scopex.evidence.catalog import EvidenceCatalog
from scopex.finalizer.claims import ClaimKind, ClaimRelation, ClaimSet


_SCOPE_LABELS = {
    "event": "单事件",
    "time_window": "当前时间窗口",
    "component": "组件范围",
    "global": "全局",
    "unknown": "范围未知",
}


def render_claims(claims: ClaimSet, catalog: EvidenceCatalog) -> str:
    """Render validated claims without upgrading their epistemic strength.

    Model ``topic`` is intentionally ignored for observed facts and temporal
    associations. Facts expand exact runtime-owned evidence; temporal
    associations use fixed wording. This prevents free-text topic content from
    smuggling unsupported causal claims into the user-visible answer.
    """

    lines: list[str] = []
    used_refs: list[str] = []

    for claim in claims.claims:
        refs = list(claim.evidence_refs)
        for ref in refs:
            if ref not in used_refs:
                used_refs.append(ref)

        scope = _SCOPE_LABELS[claim.scope.value]
        if claim.kind is ClaimKind.FACT:
            lines.append(f"事实｜{scope}")
            for ref in refs:
                item = catalog.get(ref)
                lines.append(f"- [{ref}] {item.raw}")
            continue

        if claim.relation is ClaimRelation.TEMPORAL_ASSOCIATION:
            evidence = "、".join(refs)
            lines.append(
                f"推断｜时间关联｜{claim.confidence.value}｜{scope}\n"
                f"- {evidence} 在当前调查范围内存在时间关联；该结构不表示已证明因果。"
            )
            continue

        if claim.relation is ClaimRelation.CAUSAL_HYPOTHESIS:
            evidence = "、".join(refs)
            lines.append(
                f"假设｜因果未证实｜{claim.confidence.value}｜{scope}\n"
                f"- {claim.topic}（相关证据：{evidence}）"
            )
            continue

        lines.append(f"未知｜{scope}\n- {claim.topic}")

    if used_refs:
        lines.append("\n证据索引")
        for ref in used_refs:
            item = catalog.get(ref)
            lines.append(f"- {ref} [{item.source}] {item.raw}")

    return "\n".join(lines).strip()
