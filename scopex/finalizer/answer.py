from __future__ import annotations

from dataclasses import dataclass
import json
import re

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

    It may deterministically reformat a raw validated scalar into a clearer
    product sentence, but it never calls a model/tool or introduces an
    unsupported factual/causal claim. `final.txt` remains the trust fallback.
    """

    conclusion: tuple[AnswerItem, ...]
    explanation: tuple[AnswerItem, ...]
    execution: tuple[AnswerItem, ...]
    recommendations: tuple[AnswerItem, ...]

    def to_dict(self) -> dict:
        return {
            "version": 2,
            "conclusion": [item.to_dict() for item in self.conclusion],
            "explanation": [item.to_dict() for item in self.explanation],
            "execution": [item.to_dict() for item in self.execution],
            "recommendations": [item.to_dict() for item in self.recommendations],
        }


_LABELS = {
    "total_cows": "统计牛数",
    "cows_with_final_2d_result": "有最终2D识别结果的牛数",
    "complete_four_nipple_cows": "完整识别4个乳头的牛数",
    "complete_four_nipple_rate": "完整四乳头识别率",
    "nipple_recognition_rate": "乳头识别率",
    "raw_2d_detections": "原始2D乳头框总数",
    "capped_2d_detections": "计入指标的2D乳头框总数",
    "expected_nipples": "理论乳头总数",
    "invalid_samples": "无效编码器采样数",
    "sampling_gaps": "采样间隔异常数",
    "negative_jumps": "编码器回退事件数",
    "free_gb": "剩余空间",
    "used_percent": "磁盘使用率",
    "available_gb": "可用内存",
    "util_percent": "利用率",
}
_RATE_KEYS = {"complete_four_nipple_rate", "nipple_recognition_rate", "selected_result_coverage_rate"}
_SCALAR_LINE = re.compile(r'^\s*"?([^"\s:]+)"?\s*:\s*(.+?)\s*,?\s*$')


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


def _scalar(value: str):
    text = value.strip().rstrip(",")
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text.strip('"')


def _humanize_raw(raw: str) -> str:
    """Reformat common deterministic business scalars without semantic invention."""
    text = raw.strip()
    if not text:
        return text
    if "No such file or directory" in text and ("scopex-host" in text or "scopex-system-metrics" in text):
        return "宿主机资源快照当前不可用，不能据此判断设备资源状态。"

    parts = [part.strip() for part in text.split("；") if part.strip()]
    rendered: list[str] = []
    changed = False
    for part in parts:
        match = _SCALAR_LINE.match(part)
        if match is None:
            rendered.append(part)
            continue
        key, raw_value = match.groups()
        value = _scalar(raw_value)
        if key.isdigit() and isinstance(value, (int, float)):
            rendered.append(f"{int(value)} 头牛最终识别到 {key} 个乳头")
            changed = True
            continue
        label = _LABELS.get(key)
        if label is None:
            rendered.append(part)
            continue
        if key in _RATE_KEYS and isinstance(value, (int, float)):
            rendered.append(f"{label}为 {float(value) * 100:.2f}%")
        elif key == "total_cows" and isinstance(value, (int, float)):
            rendered.append(f"共统计 {int(value)} 头牛")
        elif key == "complete_four_nipple_cows" and isinstance(value, (int, float)):
            rendered.append(f"其中 {int(value)} 头完整识别到 4 个乳头")
        elif key in {"free_gb", "available_gb"} and isinstance(value, (int, float)):
            rendered.append(f"{label} {float(value):.2f} GB")
        elif key == "used_percent" and isinstance(value, (int, float)):
            rendered.append(f"{label} {float(value):.2f}%")
        else:
            rendered.append(f"{label}：{value}")
        changed = True
    return "；".join(rendered) if changed else text


def _safe_claim_text(claim: Claim, catalog: EvidenceCatalog) -> str:
    if claim.kind is ClaimKind.FACT:
        if _has_image_evidence(claim, catalog):
            return claim.topic
        raw = _raw_evidence_text(claim, catalog)
        return _humanize_raw(raw) if raw else "已形成直接观察事实"

    if claim.relation is ClaimRelation.TEMPORAL_ASSOCIATION:
        raw = _raw_evidence_text(claim, catalog)
        subject = _humanize_raw(raw) if raw else "相关证据"
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
    return any(
        catalog.get(ref).metadata.get("evidence_role") == "action_verification"
        for ref in claim.evidence_refs
    )


def compose_product_answer(claims: ClaimSet, catalog: EvidenceCatalog) -> ProductAnswer:
    ordered = _ordered_summary_claims(claims)
    if not ordered:
        return ProductAnswer((), (), (), ())

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
            _item(claim, catalog, text=f"继续验证：{claim.topic}", kind="recommendation")
            for claim in unresolved
        ),
    )
