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
    "invalid_samples": "无效/读取失败采样数",
    "median_sample_dt_ms": "采样中位间隔",
    "sampling_gap_count": "采样缺口数",
    "anomaly_event_count": "显著异常事件数",
    "reverse_glitch_candidate_count": "回退-恢复毛刺数",
    "reverse_interval_candidate_count": "连续回退区间数",
    "reverse_step_candidate_count": "单步显著回退数",
    "positive_spike_candidate_count": "异常正向跳变数",
    "flat_count_candidate_count": "长时间不变候选数",
    "cpu_util_percent": "CPU利用率",
    "cpu_count": "CPU逻辑核数",
    "memory_total_gb": "内存总量",
    "memory_used_gb": "已用内存",
    "memory_available_gb": "可用内存",
    "disk_root_free_gb": "根盘剩余空间",
    "disk_root_used_percent": "根盘使用率",
    "gpu_util_percent_max": "GPU最高利用率",
    "gpu_count": "GPU数量",
    "free_gb": "剩余空间",
    "used_percent": "磁盘使用率",
    "available_gb": "可用内存",
    "util_percent": "利用率",
}
_RATE_KEYS = {"complete_four_nipple_rate", "nipple_recognition_rate", "selected_result_coverage_rate"}
_SCALAR_LINE = re.compile(r'^\s*"?([^"\s:]+)"?\s*:\s*(.+?)\s*,?\s*$')


def _has_image_evidence(claim: Claim, catalog: EvidenceCatalog) -> bool:
    return any(catalog.get(ref).metadata.get("evidence_type") == "image" for ref in claim.evidence_refs)


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


def _short_time(value: object) -> str:
    text = str(value or "")
    if " " in text:
        return text.split(" ", 1)[1]
    return text


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
        rows: list[str] = []
        facts = payload.get("facts")
        if isinstance(facts, dict):
            samples = facts.get("samples_in_window")
            invalid = facts.get("invalid_samples")
            median_dt = facts.get("median_sample_dt_ms")
            if isinstance(samples, (int, float)):
                text = f"时间窗内共 {int(samples)} 个编码器采样"
                if isinstance(invalid, (int, float)):
                    text += f"，无效/读取失败 {int(invalid)}"
                if isinstance(median_dt, (int, float)):
                    text += f"，采样中位间隔 {_number(median_dt, 3)} ms"
                rows.append(text)
            gaps = facts.get("sampling_gap_count")
            if isinstance(gaps, (int, float)) and gaps:
                rows.append(f"采样缺口 {int(gaps)} 处")

        status_labels = {
            "confirmed_raw_anomaly": "已确认 raw 数据异常",
            "no_anomaly_evidence_in_completed_checks": "已完成的检查未发现 raw 数据异常证据",
            "inconclusive": "本次检查未能给出确定结论",
            "no_raw_data": "时间窗内没有 raw 编码器读数",
            "batch_inspected": "批量候选检查完成",
        }
        status_text = status_labels.get(payload.get("status")) if isinstance(payload.get("status"), str) else None
        totals = []
        for key, label in (("confirmed_total", "确认"), ("unresolved_total", "未决"), ("unseen_total", "未查看")):
            value = payload.get(key)
            if isinstance(value, (int, float)) and value:
                totals.append(f"{label} {int(value)}")
        candidates_total = payload.get("candidates_total")
        if isinstance(candidates_total, (int, float)) and candidates_total:
            rows.append(f"筛出 {int(candidates_total)} 个候选" + (f"（{'，'.join(totals)}）" if totals else ""))
        elif totals:
            rows.append("，".join(totals))
        region = payload.get("region")
        if isinstance(region, str) and region:
            start = _short_time(payload.get("start"))
            end = _short_time(payload.get("end"))
            rows.append(f"检查区间 {region} {start}–{end}".rstrip() + (f"：{status_text}" if status_text else ""))
        elif status_text:
            rows.append(status_text)

        candidates = payload.get("candidates")
        if isinstance(candidates, list):
            kind_labels = {
                "profile_rate_candidate": "速率超限",
                "profile_range_candidate": "计数越界",
                "local_rate_deviation": "局部速率偏离",
                "rate_transition": "速率变化",
                "counter_or_acquisition_context": "控制事件边界",
            }
            review_labels = {
                "confirmed_raw_anomaly": "已确认异常",
                "unresolved": "未决",
                "compatible_motion": "相容运动",
                "no_anomaly_evidence": "无异常证据",
                "unseen": "待查看",
            }
            details = []
            for event in candidates:
                if not isinstance(event, dict):
                    continue
                when = f"{_short_time(event.get('start'))}–{_short_time(event.get('end'))}"
                kinds = [kind_labels.get(kind, kind) for kind in event.get("kinds", [])[:2]] or ["候选"]
                metrics = event.get("metrics") if isinstance(event.get("metrics"), dict) else {}
                measures = []
                for key, label in (("excess", "超限"), ("deviation", "偏离"), ("shift", "变化")):
                    value = metrics.get(key)
                    if isinstance(value, (int, float)) and value:
                        measures.append(f"{label} {_number(value, 1)} 倍")
                peak = event.get("peak_rate_counts_s")
                if isinstance(peak, (int, float)):
                    measures.append(f"峰值 {_number(peak, 1)} counts/s")
                review = review_labels.get(event.get("review_status"), "待查看")
                piece = f"{when} {event.get('id', '?')} {'/'.join(kinds)}"
                if measures:
                    piece += f"（{'，'.join(measures)}）"
                details.append(piece + f"：{review}")
                if len(details) >= 4:
                    break
            if details:
                suffix = ""
                if isinstance(candidates_total, (int, float)) and candidates_total > len(details):
                    suffix = f"（共 {int(candidates_total)} 个，此处列前 {len(details)} 个）"
                rows.append("候选：" + "；".join(details) + suffix)

        checks = payload.get("checks")
        if isinstance(checks, list) and checks:
            check_labels = {"verified_rate_limit": "速率上限检查", "verified_count_range": "计数范围检查",
                            "motion_explanation": "运动解释"}
            outcome_labels = {"violated": "违反", "insufficient": "证据不足",
                              "compatible_transition": "相容变化", "no_contradiction_detected": "无矛盾"}
            outcomes = []
            for check in checks:
                if isinstance(check, dict):
                    name = check_labels.get(check.get("check"), check.get("check"))
                    outcome = outcome_labels.get(check.get("outcome"), check.get("outcome"))
                    outcomes.append(f"{name}{outcome}")
            if outcomes:
                rows.append("检查：" + "，".join(outcomes))

        reasons = payload.get("inconclusive_reasons")
        if isinstance(reasons, list) and reasons:
            reason_labels = {
                "unreviewed_candidates": "存在未查看候选",
                "unresolved_checks": "存在未决检查",
                "incomplete_or_uncertain_data": "数据覆盖不完整或存在不确定因素",
                "insufficient_local_reference": "局部参考不足",
                "fewer_than_three_raw_samples": "raw 样本少于 3 个",
            }
            texts = [reason_labels.get(reason, reason) for reason in reasons if isinstance(reason, str)]
            if texts:
                rows.append("未定原因：" + "、".join(texts))
        coverage = payload.get("coverage")
        if isinstance(coverage, dict):
            notes = []
            edges = coverage.get("uncovered_edges")
            if isinstance(edges, list) and edges:
                notes.append(f"请求窗口 {len(edges)} 端未全覆盖")
            gaps_total = coverage.get("internal_gap_count")
            if isinstance(gaps_total, (int, float)) and gaps_total:
                notes.append(f"内部缺口 {int(gaps_total)} 处")
            if notes:
                rows.append("覆盖：" + "，".join(notes))
        return "；".join(rows) if rows else None

    if source == "system-health":
        facts = payload.get("facts")
        if not isinstance(facts, dict):
            return None
        rows = []
        cpu = facts.get("cpu_util_percent")
        if isinstance(cpu, (int, float)):
            rows.append(f"当前 CPU 利用率 {_number(cpu, 2)}%")
        mem_used = facts.get("memory_used_gb")
        mem_available = facts.get("memory_available_gb")
        mem_total = facts.get("memory_total_gb")
        if isinstance(mem_used, (int, float)) or isinstance(mem_available, (int, float)):
            text = "当前内存"
            if isinstance(mem_used, (int, float)):
                text += f"已用 {_number(mem_used, 2)} GB"
            if isinstance(mem_available, (int, float)):
                text += f"，可用 {_number(mem_available, 2)} GB"
            if isinstance(mem_total, (int, float)):
                text += f" / 总计 {_number(mem_total, 2)} GB"
            rows.append(text)
        disk_used = facts.get("disk_root_used_percent")
        disk_free = facts.get("disk_root_free_gb")
        if isinstance(disk_used, (int, float)) or isinstance(disk_free, (int, float)):
            text = "根磁盘"
            if isinstance(disk_used, (int, float)):
                text += f"已用 {_number(disk_used, 2)}%"
            if isinstance(disk_free, (int, float)):
                text += f"，剩余 {_number(disk_free, 2)} GB"
            rows.append(text)
        gpu = facts.get("gpu_util_percent_max")
        gpu_count = facts.get("gpu_count")
        if isinstance(gpu, (int, float)):
            rows.append(f"当前 GPU 最高利用率 {_number(gpu, 2)}%" + (f"（{int(gpu_count)} 个 GPU）" if isinstance(gpu_count, (int, float)) else ""))
        captured = facts.get("captured_at")
        if isinstance(captured, str) and captured:
            rows.append(f"采样时间 {captured}")
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
