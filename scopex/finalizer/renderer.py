from __future__ import annotations

from pathlib import Path

from scopex.evidence.catalog import EvidenceCatalog, EvidenceItem
from scopex.finalizer.claims import ClaimKind, ClaimRelation, ClaimSet


_SCOPE_LABELS = {
    "event": "单事件",
    "time_window": "当前时间窗口",
    "component": "组件范围",
    "global": "全局",
    "unknown": "范围未知",
}


def _evidence_label(item: EvidenceItem) -> str:
    source = Path(item.source).name or item.source
    line_number = item.metadata.get("line_number")
    if isinstance(line_number, int) and line_number > 0:
        return f"{item.ref} {source}:L{line_number}"
    return f"{item.ref} {source}"


def _refs_text(refs: list[str], catalog: EvidenceCatalog) -> str:
    return "、".join(_evidence_label(catalog.get(ref)) for ref in refs)


def render_claims(claims: ClaimSet, catalog: EvidenceCatalog) -> str:
    """Render validated claims without upgrading their epistemic strength.

    Model ``topic`` is intentionally ignored for observed facts and temporal
    associations. Facts expand exact runtime-owned evidence; temporal
    associations use fixed wording. Each evidence item is rendered at the claim
    site, so a second full evidence appendix is unnecessary and would only
    duplicate user-visible content.
    """

    lines: list[str] = []

    for claim in claims.claims:
        refs = list(claim.evidence_refs)
        scope = _SCOPE_LABELS[claim.scope.value]

        if claim.kind is ClaimKind.FACT:
            lines.append(f"事实｜{scope}")
            for ref in refs:
                item = catalog.get(ref)
                lines.append(f"- [{_evidence_label(item)}] {item.raw}")
            continue

        if claim.relation is ClaimRelation.TEMPORAL_ASSOCIATION:
            evidence = _refs_text(refs, catalog)
            lines.append(
                f"推断｜时间关联｜{claim.confidence.value}｜{scope}\n"
                f"- {evidence} 在当前调查范围内存在时间关联；该结构不表示已证明因果。"
            )
            continue

        if claim.relation is ClaimRelation.CAUSAL_HYPOTHESIS:
            evidence = _refs_text(refs, catalog)
            lines.append(
                f"假设｜因果未证实｜{claim.confidence.value}｜{scope}\n"
                f"- {claim.topic}（相关证据：{evidence or '无直接证据'}）"
            )
            continue

        evidence = _refs_text(refs, catalog) if refs else "无直接证据"
        lines.append(f"未知｜{scope}\n- {claim.topic}（相关证据：{evidence}）")

    return "\n".join(lines).strip()
