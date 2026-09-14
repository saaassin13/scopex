from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from scopex.evidence.catalog import EvidenceCatalog, EvidenceItem
from scopex.finalizer.claims import Claim, ClaimKind, ClaimRelation, ClaimSet
from scopex.finalizer.client import FinalizerResponse, StreamingFinalizerClient


@dataclass(frozen=True, slots=True)
class ReportItem:
    text: str
    claim_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "claim_ids": list(self.claim_ids),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class ProductReport:
    conclusion: ReportItem
    facts: tuple[ReportItem, ...]
    possibilities: tuple[ReportItem, ...]
    next_steps: tuple[ReportItem, ...]
    limitations: tuple[ReportItem, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "conclusion": self.conclusion.to_dict(),
            "facts": [item.to_dict() for item in self.facts],
            "possibilities": [item.to_dict() for item in self.possibilities],
            "next_steps": [item.to_dict() for item in self.next_steps],
            "limitations": [item.to_dict() for item in self.limitations],
        }


@dataclass(frozen=True, slots=True)
class ReportComposerResult:
    transport: FinalizerResponse
    report: ProductReport | None
    errors: tuple[str, ...] = ()
    parse_error: str | None = None

    @property
    def valid(self) -> bool:
        return (
            self.report is not None
            and not self.errors
            and self.parse_error is None
            and self.transport.done_seen
            and bool(self.transport.finish_reasons)
            and self.transport.finish_reasons[-1] == "stop"
        )


def _parse_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```json") and raw.endswith("```"):
        raw = raw[7:-3].strip()
    elif raw.startswith("```") and raw.endswith("```"):
        raw = raw[3:-3].strip()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("report composer output must be one JSON object")
    return value


def _bounded_raw(raw: str, *, limit: int = 1400) -> str:
    text = raw.strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _structured_business_content(item: EvidenceItem) -> str | None:
    if item.metadata.get("evidence_type") != "structured_business_facts":
        return None
    try:
        payload = json.loads(item.raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("scopex_role") != "business_facts":
        return None

    keep: dict[str, Any] = {}
    for key in ("scopex_role", "schema", "source", "window", "facts", "summary", "quality", "candidate_events_total"):
        if key in payload:
            keep[key] = payload[key]
    candidates = payload.get("top_candidates")
    if isinstance(candidates, list):
        # Concrete timestamps/counts around the strongest bounded candidates are
        # exactly what the user report needs. Keep a small set instead of a raw
        # prefix that might truncate them away after the large facts object.
        keep["top_candidates"] = candidates[:6]
    return json.dumps(keep, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _composer_evidence_content(item: EvidenceItem) -> str:
    structured = _structured_business_content(item)
    if structured is not None:
        return structured if len(structured) <= 6000 else structured[:6000] + "…"
    return _bounded_raw(item.raw)


def _composer_input(user_request: str, claims: ClaimSet, catalog: EvidenceCatalog) -> str:
    by_ref = {item.ref: item for item in catalog.items}
    evidence_refs: list[str] = []
    for claim in claims.claims:
        for ref in claim.evidence_refs:
            if ref not in evidence_refs:
                evidence_refs.append(ref)

    claim_rows = [
        {
            "id": claim.id,
            "kind": claim.kind.value,
            "relation": claim.relation.value,
            "confidence": claim.confidence.value,
            "scope": claim.scope.value,
            "topic": claim.topic,
            "evidence_refs": list(claim.evidence_refs),
        }
        for claim in claims.claims
    ]
    evidence_rows = []
    for ref in evidence_refs:
        item = by_ref.get(ref)
        if item is None:
            continue
        evidence_rows.append({
            "ref": item.ref,
            "type": item.metadata.get("evidence_type"),
            "role": item.metadata.get("evidence_role"),
            "source": item.source,
            "content": _composer_evidence_content(item),
        })

    payload = {
        "user_request": user_request,
        "summary_claim_ids": list(claims.summary_claim_ids),
        "claims": claim_rows,
        "referenced_evidence": evidence_rows,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


_SYSTEM_PROMPT = """你是 ScopeX 的业务结果编辑器，不是诊断 Agent。调查已经结束，你没有工具，也不能继续调查。

你的唯一任务：把已经验证过的 Claims 与它们引用的 Evidence 整理成用户可读的中文业务报告。

严格规则：
1. 不新增任何事实、时间、数字、单位、设备状态或根因；只能重组输入中的 Claims/Evidence。
2. observed + fact 才能进入 facts（事实依据）。
3. temporal_association / inference 只能写成“相关、可能、提示”等非因果措辞。
4. causal_hypothesis 必须明确“可能/待验证”，不能写成已确认根因。
5. unknown 不得包装成确定结论，可用于 limitations 或 next_steps。
6. 不展示 JSON 字段名、Python、Skill、命令、工具调用、Evidence 内部结构或模型调查过程。
7. 优先直接回答用户问题，再给最关键的事实依据；不要机械罗列所有字段。
8. 可以为了人类阅读做常规四舍五入、百分比/GB/ms 等格式化，但不得改变数量级、含义或制造输入中没有的数字。
9. 每个条目必须引用真实 claim_ids；evidence_refs 只能从这些 Claim 已有的 evidence_refs 中选择。
10. next_steps 只能围绕已有 inference/unknown/causal_hypothesis 的验证方向，不得凭空增加故障结论或执行动作。
11. 如果证据不足，直接说明限制，不要补常识答案。
12. 对结构化 business_facts，优先把最重要的统计和 top_candidates 写成人话；不要复述字段名。
13. 只输出一个 JSON 对象，不输出 Markdown 或额外解释。

固定 schema：
{
  "version":1,
  "conclusion":{"text":"...","claim_ids":["C1"],"evidence_refs":["E1"]},
  "facts":[{"text":"...","claim_ids":["C1"],"evidence_refs":["E1"]}],
  "possibilities":[{"text":"...","claim_ids":["C2"],"evidence_refs":["E2"]}],
  "next_steps":[{"text":"...","claim_ids":["C2"],"evidence_refs":[]}],
  "limitations":[{"text":"...","claim_ids":["C3"],"evidence_refs":[]}]
}

数量限制：facts 最多6条，possibilities 最多4条，next_steps 最多4条，limitations 最多3条。"""


def _text(value: Any, *, field: str, maximum: int) -> tuple[str | None, str | None]:
    if not isinstance(value, str) or not value.strip():
        return None, f"{field}.text_required"
    text = value.strip()
    if len(text) > maximum:
        return None, f"{field}.text_too_long"
    return text, None


def _ids(value: Any, *, field: str, maximum: int) -> tuple[tuple[str, ...] | None, str | None]:
    if not isinstance(value, list) or not value or len(value) > maximum:
        return None, f"{field}.claim_ids_invalid"
    rows: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or item in rows:
            return None, f"{field}.claim_ids_invalid"
        rows.append(item)
    return tuple(rows), None


def _refs(value: Any, *, field: str, maximum: int = 4) -> tuple[tuple[str, ...] | None, str | None]:
    if value is None:
        return (), None
    if not isinstance(value, list) or len(value) > maximum:
        return None, f"{field}.evidence_refs_invalid"
    rows: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or item in rows:
            return None, f"{field}.evidence_refs_invalid"
        rows.append(item)
    return tuple(rows), None


def _validate_item(
    value: Any,
    *,
    field: str,
    claims_by_id: dict[str, Claim],
    allowed: str,
    maximum_text: int = 500,
) -> tuple[ReportItem | None, list[str]]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return None, [f"{field}.object_required"]
    text, error = _text(value.get("text"), field=field, maximum=maximum_text)
    if error:
        errors.append(error)
    claim_ids, error = _ids(value.get("claim_ids"), field=field, maximum=3)
    if error:
        errors.append(error)
    evidence_refs, error = _refs(value.get("evidence_refs"), field=field)
    if error:
        errors.append(error)
    if errors or text is None or claim_ids is None or evidence_refs is None:
        return None, errors

    claims: list[Claim] = []
    for claim_id in claim_ids:
        claim = claims_by_id.get(claim_id)
        if claim is None:
            errors.append(f"{field}.unknown_claim:{claim_id}")
        else:
            claims.append(claim)
    if errors:
        return None, errors

    allowed_refs = {ref for claim in claims for ref in claim.evidence_refs}
    for ref in evidence_refs:
        if ref not in allowed_refs:
            errors.append(f"{field}.evidence_not_in_claim:{ref}")

    if allowed == "facts":
        if any(claim.kind is not ClaimKind.FACT or claim.relation is not ClaimRelation.OBSERVED for claim in claims):
            errors.append(f"{field}.fact_requires_observed")
    elif allowed in {"possibilities", "unresolved"}:
        if any(claim.kind is ClaimKind.FACT and claim.relation is ClaimRelation.OBSERVED for claim in claims):
            errors.append(f"{field}.requires_non_observed_claim")

    if errors:
        return None, errors
    return ReportItem(text=text, claim_ids=claim_ids, evidence_refs=evidence_refs), []


def validate_report_payload(payload: Any, claims: ClaimSet) -> tuple[ProductReport | None, tuple[str, ...]]:
    if not isinstance(payload, dict):
        return None, ("report.object_required",)
    if payload.get("version") != 1:
        return None, ("report.version",)
    claims_by_id = claims.by_id()
    errors: list[str] = []

    conclusion, row_errors = _validate_item(
        payload.get("conclusion"), field="conclusion", claims_by_id=claims_by_id, allowed="any", maximum_text=600
    )
    errors.extend(row_errors)

    sections: dict[str, tuple[ReportItem, ...]] = {}
    limits = {"facts": 6, "possibilities": 4, "next_steps": 4, "limitations": 3}
    semantics = {"facts": "facts", "possibilities": "possibilities", "next_steps": "unresolved", "limitations": "unresolved"}
    for name, limit in limits.items():
        value = payload.get(name)
        if not isinstance(value, list) or len(value) > limit:
            errors.append(f"{name}.list_invalid")
            continue
        items: list[ReportItem] = []
        for index, row in enumerate(value):
            item, row_errors = _validate_item(
                row,
                field=f"{name}[{index}]",
                claims_by_id=claims_by_id,
                allowed=semantics[name],
            )
            errors.extend(row_errors)
            if item is not None:
                items.append(item)
        sections[name] = tuple(items)

    if errors or conclusion is None:
        return None, tuple(errors)
    return ProductReport(
        conclusion=conclusion,
        facts=sections.get("facts", ()),
        possibilities=sections.get("possibilities", ()),
        next_steps=sections.get("next_steps", ()),
        limitations=sections.get("limitations", ()),
    ), ()


class ConstrainedReportComposer:
    """One no-tool model call that rewrites validated Claims into readable business prose."""

    def __init__(
        self,
        client: StreamingFinalizerClient,
        *,
        model: str,
        max_tokens: int = 1024,
    ) -> None:
        self.client = client
        self.model = model
        self.max_tokens = max_tokens

    def run(self, *, user_request: str, claims: ClaimSet, catalog: EvidenceCatalog) -> ReportComposerResult:
        try:
            transport = self.client.complete(
                model=self.model,
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=_composer_input(user_request, claims, catalog),
                max_tokens=self.max_tokens,
                temperature=0,
            )
        except Exception as exc:
            empty = FinalizerResponse("", 0.0, None, 0.0, (), False, None)
            return ReportComposerResult(empty, None, (f"transport:{type(exc).__name__}",), str(exc)[:500])

        if not transport.done_seen or not transport.finish_reasons or transport.finish_reasons[-1] != "stop":
            return ReportComposerResult(transport, None, ("report_transport_incomplete",), None)
        try:
            payload = _parse_object(transport.content)
        except (ValueError, json.JSONDecodeError) as exc:
            return ReportComposerResult(transport, None, (), f"report_parse:{type(exc).__name__}:{str(exc)[:300]}")
        report, errors = validate_report_payload(payload, claims)
        return ReportComposerResult(transport, report, errors, None)
