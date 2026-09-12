from __future__ import annotations

from collections import OrderedDict
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

_CONFIDENCE_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
    "unknown": "未知",
}


def _source_name(item: EvidenceItem) -> str:
    return Path(item.source).name or item.source


def _evidence_label(item: EvidenceItem) -> str:
    source = _source_name(item)
    line_number = item.metadata.get("line_number")
    if isinstance(line_number, int) and line_number > 0:
        return f"{item.ref} {source}:L{line_number}"
    return f"{item.ref} {source}"


def _compact_evidence_label(item: EvidenceItem) -> str:
    line_number = item.metadata.get("line_number")
    if isinstance(line_number, int) and line_number > 0:
        return f"{item.ref} · L{line_number}"
    return item.ref


def _refs_text(refs: list[str], catalog: EvidenceCatalog) -> str:
    return "、".join(_evidence_label(catalog.get(ref)) for ref in refs)


def _render_observed_facts(claims: ClaimSet, catalog: EvidenceCatalog) -> list[str]:
    """Group exact observed evidence by source without changing its meaning."""

    grouped: OrderedDict[str, list[EvidenceItem]] = OrderedDict()
    seen_refs: set[str] = set()

    for claim in claims.claims:
        if claim.kind is not ClaimKind.FACT:
            continue
        for ref in claim.evidence_refs:
            if ref in seen_refs:
                continue
            seen_refs.add(ref)
            item = catalog.get(ref)
            grouped.setdefault(item.source, []).append(item)

    lines: list[str] = []
    for source, items in grouped.items():
        title = Path(source).name or source
        lines.append(f"直接观察｜{title}")
        for item in items:
            lines.append(f"- [{_compact_evidence_label(item)}] {item.raw}")
    return lines


def render_claims(claims: ClaimSet, catalog: EvidenceCatalog) -> str:
    """Render validated claims without upgrading their epistemic strength.

    Observed facts keep exact runtime-owned evidence and are grouped by source so
    simple read/query tasks remain readable. Inference wording stays
    deterministic so presentation cannot silently strengthen model claims.
    """

    lines: list[str] = _render_observed_facts(claims, catalog)

    for claim in claims.claims:
        if claim.kind is ClaimKind.FACT:
            continue

        refs = list(claim.evidence_refs)
        scope = _SCOPE_LABELS[claim.scope.value]
        confidence = _CONFIDENCE_LABELS[claim.confidence.value]

        if claim.relation is ClaimRelation.TEMPORAL_ASSOCIATION:
            evidence = _refs_text(refs, catalog)
            lines.append(
                f"时间关联｜可信度：{confidence}｜范围：{scope}\n"
                f"- {evidence} 在当前调查范围内存在时间关联；这不表示已经证明因果。"
            )
            continue

        if claim.relation is ClaimRelation.CAUSAL_HYPOTHESIS:
            evidence = _refs_text(refs, catalog)
            lines.append(
                f"待验证假设｜可信度：{confidence}｜范围：{scope}\n"
                f"- {claim.topic}（相关证据：{evidence or '无直接证据'}）"
            )
            continue

        evidence = _refs_text(refs, catalog) if refs else "无直接证据"
        lines.append(
            f"尚不能确定｜范围：{scope}\n"
            f"- {claim.topic}（相关证据：{evidence}）"
        )

    return "\n".join(lines).strip()
