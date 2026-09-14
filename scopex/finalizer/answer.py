from __future__ import annotations

from dataclasses import dataclass
import json
import re

from scopex.evidence.catalog import EvidenceCatalog
from scopex.finalizer.claims import Claim, ClaimKind, ClaimRelation, ClaimSet


@dataclass(frozen=True, slots=True)
class AnswerItem:
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
    """Result-first projection over already validated Claims/Evidence."""

    conclusion: tuple[AnswerItem, ...]
    explanation: tuple[AnswerItem, ...]
    execution: tuple[AnswerItem, ...]
    recommendations: tuple[AnswerItem, ...]

    def to_dict(self) -> dict:
        return {
            "version": 3,
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
    "samples_in_window": "时间窗采样数",
    "valid_samples": "有效采样数",
    "invalid_samples": "无效/读取失败采样数",
    "median_sample_dt_ms": "采样中位间隔",
    "sampling_gap_count": "采样缺口候选数",
    "negative_jump_count": "raw 数值下降次数",
    "negative_jump_abs_p95_pulses": "raw 下降幅度P95",
    "negative_jump_abs_max_pulses": "raw 最大下降幅度",
    "negative_jump_outlier_candidate_count": "显著 raw 下降候选数",
    "large_negative_jump_candidate_count": "大幅 raw 下降候选数",
    "positive_delta_outlier_candidate_count": "显著正向跳变候选数",
    "flat_raw_candidate_count": "长时间不变候选数",
    "longest_flat_raw_ms": "最长 raw 不变持续时间",
    "free_gb": "剩余空间",
    "used_percent": "磁盘使用率",
    "available_gb": "可用内存",
    "util_percent": "利用率",
}
_RATE_KEYS = {"complete_four_nipple_rate", "nipple_recognition_rate", "selected_result_coverage_rate"}
_SCALAR_LINE = re.compile(r'^\s*"?([^"\s:]+)"?\s*:\s*(.+?)\s*,?\s*$')


def _has_image_evidence(claim: Claim, catalog: EvidenceCatalog) -> bool:
    return any(catalog.get(ref).metadata.get("evidence_type") == "image" for ref in claim.evidence_refs)


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


def _percent(value) -> str | None:
    if isinstance(value, (int, float)):
        return f"{float(value) * 100:.2f}%"
    return None


def _number(value, digits: int = 2) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return str(value)


def _humanize_business_facts(payload: dict) -> str | None:
    source = payload.get("source")
    if source == "nipple-recognition-analysis":
        summary = payload.get("summary")
        quality = payload.get("quality")
        if not isinstance(summary, dict):
            return None
        rows: list[str] = []
        total = summary.get("total_cows")
        if isinstance(total, (int, float)):
            rows.append(f"共统计 {int(total)} 头牛")
        complete = summary.get("complete_four_nipple_cows")
        complete_rate = _percent(summary.get("complete_four_nipple_rate"))
        if isinstance(complete, (int, float)):
            text = f"其中 {int(complete)} 头完整识别到 4 个乳头"
            if complete_rate:
                text += f"（{complete_rate}）"
            rows.append(text)
        rate = _percent(summary.get("nipple_recognition_rate"))
        if rate:
            rows.append(f"总体乳头识别率为 {rate}")
        distribution = summary.get("distribution_by_final_2d_count")
        if isinstance(distribution, dict) and distribution:
            ordered = []
            for key in ("4", "3", "2", "1", "0", "missing"):
                value = distribution.get(key)
                if isinstance(value, (int, float)) and value:
                    label = "缺最终结果" if key == "missing" else f"{key}个乳头"
                    ordered.append(f"{label} {int(value)} 头")
            if ordered:
                rows.append("最终2D结果分布：" + "，".join(ordered))
        if isinstance(quality, dict):
            unfinished = quality.get("unfinished_cycles_in_window")
            over = quality.get("over_four_2d_detections")
            notes = []
            if isinstance(unfinished, (int, float)) and unfinished:
                notes.append(f"未完成牛周期 {int(unfinished)}")
            if isinstance(over, (int, float)) and over:
                notes.append(f"超过4框的过检牛 {int(over)}")
            if notes:
                rows.append("数据质量：" + "，".join(notes))
        return "；".join(rows) if rows else None

    if source == "encoder-health":
        facts = payload.get("facts")
        if not isinstance(facts, dict):
            return None
        rows: list[str] = []
        samples = facts.get("samples_in_window")
        valid = facts.get("valid_samples")
        invalid = facts.get("invalid_samples")
        if isinstance(samples, (int, float)):
            text = f"时间窗内共 {int(samples)} 个编码器采样"
            if isinstance(valid, (int, float)):
                text += f"，有效 {int(valid)}"
            if isinstance(invalid, (int, float)):
                text += f"，无效/读取失败 {int(invalid)}"
            rows.append(text)
        median_dt = facts.get("median_sample_dt_ms")
        gap_count = facts.get("sampling_gap_count")
        if isinstance(median_dt, (int, float)):
            text = f"采样中位间隔 {_number(median_dt, 3)} ms"
            if isinstance(gap_count, (int, float)):
                text += f"，采样缺口候选 {int(gap_count)} 个"
            rows.append(text)
        negative = facts.get("negative_jump_count")
        if isinstance(negative, (int, float)):
            p95 = facts.get("negative_jump_abs_p95_pulses")
            maximum = facts.get("negative_jump_abs_max_pulses")
            text = f"观察到 raw 数值下降 {int(negative)} 次"
            if isinstance(p95, (int, float)):
                text += f"，下降幅度 P95 为 {_number(p95, 2)} pulse"
            if isinstance(maximum, (int, float)):
                text += f"，最大 {_number(maximum, 2)} pulse"
            rows.append(text)
        significant = facts.get("negative_jump_outlier_candidate_count")
        large = facts.get("large_negative_jump_candidate_count")
        positive = facts.get("positive_delta_outlier_candidate_count")
        flat = facts.get("flat_raw_candidate_count")
        candidates = []
        if isinstance(significant, (int, float)):
            candidates.append(f"显著 raw 下降候选 {int(significant)}")
        if isinstance(large, (int, float)):
            candidates.append(f"大幅 raw 下降候选 {int(large)}")
        if isinstance(positive, (int, float)):
            candidates.append(f"显著正向跳变候选 {int(positive)}")
        if isinstance(flat, (int, float)):
            candidates.append(f"长时间不变候选 {int(flat)}")
        if candidates:
            rows.append("候选事件：" + "，".join(candidates))
        return "；".join(rows) if rows else None

    facts = payload.get("facts") or payload.get("summary")
    if isinstance(facts, dict):
        rendered = []
        for key, value in facts.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                label = _LABELS.get(key, key)
                if key in _RATE_KEYS:
                    pct = _percent(value)
                    rendered.append(f"{label} {pct}" if pct else f"{label}：{value}")
                else:
                    rendered.append(f"{label}：{value}")
        return "；".join(rendered[:12]) if rendered else None
    return None


def _humanize_raw(raw: str) -> str:
    text = raw.strip()
    if not text:
        return text
    if "No such file or directory" in text and ("scopex-host" in text or "scopex-system-metrics" in text):
        return "宿主机资源快照当前不可用，不能据此判断设备资源状态。"
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and payload.get("scopex_role") == "business_facts":
            rendered = _humanize_business_facts(payload)
            if rendered:
                return rendered

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


def _humanized_claim_evidence_text(claim: Claim, catalog: EvidenceCatalog, *, max_rows: int = 3) -> str:
    items = []
    seen_refs: set[str] = set()
    for ref in claim.evidence_refs:
        if ref in seen_refs:
            continue
        seen_refs.add(ref)
        item = catalog.get(ref)
        if item.raw.strip():
            items.append(item)

    structured = [item for item in items if item.metadata.get("evidence_type") == "structured_business_facts"]
    selected = structured[:1] if structured else items

    rendered: list[str] = []
    seen_text: set[str] = set()
    for item in selected:
        text = _humanize_raw(item.raw)
        if not text or text in seen_text:
            continue
        seen_text.add(text)
        rendered.append(text)
        if len(rendered) >= max_rows:
            break
    return "；".join(rendered)


def _safe_claim_text(claim: Claim, catalog: EvidenceCatalog) -> str:
    if claim.kind is ClaimKind.FACT:
        if _has_image_evidence(claim, catalog):
            return claim.topic
        text = _humanized_claim_evidence_text(claim, catalog)
        return text if text else "已形成直接观察事实"
    if claim.relation is ClaimRelation.TEMPORAL_ASSOCIATION:
        subject = _humanized_claim_evidence_text(claim, catalog)
        return f"{subject or '相关证据'}；当前仅支持时间关联，未证明因果。"
    if claim.relation is ClaimRelation.CAUSAL_HYPOTHESIS:
        return f"待验证假设：{claim.topic}"
    return f"尚不能确定：{claim.topic}"


def _item(claim: Claim, catalog: EvidenceCatalog, *, text: str | None = None, kind: str | None = None) -> AnswerItem:
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
    return any(catalog.get(ref).metadata.get("evidence_role") == "action_verification" for ref in claim.evidence_refs)


def compose_product_answer(claims: ClaimSet, catalog: EvidenceCatalog) -> ProductAnswer:
    ordered = _ordered_summary_claims(claims)
    if not ordered:
        return ProductAnswer((), (), (), ())

    conclusion_claim = next((claim for claim in ordered if claim.kind is ClaimKind.FACT), ordered[0])
    conclusion_item = _item(conclusion_claim, catalog)

    explanation_items: list[AnswerItem] = []
    seen_text = {conclusion_item.text}
    for claim in ordered:
        if claim.id == conclusion_claim.id:
            continue
        item = _item(claim, catalog)
        if not item.text or item.text in seen_text:
            continue
        seen_text.add(item.text)
        explanation_items.append(item)
        if len(explanation_items) >= 4:
            break

    execution_claims = [
        claim for claim in ordered
        if claim.kind is ClaimKind.FACT and _has_action_verification_evidence(claim, catalog)
    ][:4]
    unresolved = [
        claim for claim in ordered
        if claim.kind is ClaimKind.UNKNOWN or claim.relation is ClaimRelation.CAUSAL_HYPOTHESIS
    ][:3]
    return ProductAnswer(
        conclusion=(conclusion_item,),
        explanation=tuple(explanation_items),
        execution=tuple(_item(claim, catalog) for claim in execution_claims),
        recommendations=tuple(
            _item(claim, catalog, text=f"继续验证：{claim.topic}", kind="recommendation")
            for claim in unresolved
        ),
    )
