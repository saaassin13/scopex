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


def _source_title(item: EvidenceItem) -> str:
    if item.metadata.get("evidence_type") in {"command_line", "command_output"}:
        title = item.metadata.get("title")
        command = item.metadata.get("command")
        if isinstance(title, str) and title:
            return title
        if isinstance(command, str) and command:
            return command
    return _source_name(item)


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


def _claim_has_image(claim, catalog: EvidenceCatalog) -> bool:
    return any(
        catalog.get(ref).metadata.get("evidence_type") == "image"
        for ref in claim.evidence_refs
    )


def _render_observed_facts(claims: ClaimSet, catalog: EvidenceCatalog) -> list[str]:
    """Render exact text/command facts and fresh visual observations safely.

    Text and command facts keep runtime-owned raw Evidence so model prose cannot
    silently rewrite them. Image bytes cannot be expanded as readable text; for
    a visual fact the topic comes from the Fresh Finalizer that re-opened the
    SHA-verified image in the same finalization call.
    """

    grouped: OrderedDict[str, list[EvidenceItem]] = OrderedDict()
    seen_refs: set[str] = set()
    visual_claims = []

    for claim in claims.claims:
        if claim.kind is not ClaimKind.FACT:
            continue
        if _claim_has_image(claim, catalog):
            visual_claims.append(claim)
            continue
        for ref in claim.evidence_refs:
            if ref in seen_refs:
                continue
            seen_refs.add(ref)
            item = catalog.get(ref)
            grouped.setdefault(item.source, []).append(item)

    lines: list[str] = []
    for _, items in grouped.items():
        title = _source_title(items[0])
        lines.append(f"直接观察｜{title}")
        for item in items:
            lines.append(f"- [{_compact_evidence_label(item)}] {item.raw}")

    for claim in visual_claims:
        image_refs = [
            ref for ref in claim.evidence_refs
            if catalog.get(ref).metadata.get("evidence_type") == "image"
        ]
        title = "、".join(_source_name(catalog.get(ref)) for ref in image_refs) or "图片"
        evidence = _refs_text(list(claim.evidence_refs), catalog)
        lines.append(f"视觉观察｜{title}")
        lines.append(f"- {claim.topic}（相关证据：{evidence}）")

    return lines


def render_claims(claims: ClaimSet, catalog: EvidenceCatalog) -> str:
    """Render validated claims without upgrading their epistemic strength."""

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
                f"- {evidence} 在当前调查范围内存在时间关联；该结构不表示已证明因果。"
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
