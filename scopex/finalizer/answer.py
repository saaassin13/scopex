from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping

from scopex.evidence.catalog import EvidenceCatalog, EvidenceItem
from scopex.finalizer.claims import Claim, ClaimKind, ClaimRelation, ClaimSet
from scopex.finalizer.client import FinalizerResponse, StreamingFinalizerClient


_SELECTION_FIELDS = {
    "conclusion_claim_ids",
    "explanation_claim_ids",
    "execution_claim_ids",
    "recommendation_claim_ids",
}


@dataclass(frozen=True, slots=True)
class ProductAnswerItem:
    claim_id: str
    text: str
    kind: str
    relation: str
    confidence: str
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "text": self.text,
            "kind": self.kind,
            "relation": self.relation,
            "confidence": self.confidence,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class ProductAnswer:
    conclusion: tuple[ProductAnswerItem, ...]
    explanation: tuple[ProductAnswerItem, ...]
    execution: tuple[ProductAnswerItem, ...]
    recommendation: tuple[ProductAnswerItem, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "conclusion": [item.to_dict() for item in self.conclusion],
            "explanation": [item.to_dict() for item in self.explanation],
            "execution": [item.to_dict() for item in self.execution],
            "recommendation": [item.to_dict() for item in self.recommendation],
        }


@dataclass(frozen=True, slots=True)
class AnswerCompositionResult:
    transport: FinalizerResponse
    selection: dict[str, list[str]] | None
    parse_error: str | None
    errors: tuple[str, ...]
    answer: ProductAnswer | None

    @property
    def valid(self) -> bool:
        return (
            self.parse_error is None
            and not self.errors
            and self.answer is not None
            and self.transport.done_seen
            and bool(self.transport.finish_reasons)
            and self.transport.finish_reasons[-1] == "stop"
        )

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "parse_error": self.parse_error,
            "finish_reasons": list(self.transport.finish_reasons),
            "elapsed_s": self.transport.elapsed_s,
            "usage": self.transport.usage,
            "selection": self.selection,
            "answer": self.answer.to_dict() if self.answer is not None else None,
        }


def _empty_transport() -> FinalizerResponse:
    return FinalizerResponse(
        content="",
        headers_s=0.0,
        first_content_s=None,
        elapsed_s=0.0,
        finish_reasons=(),
        done_seen=False,
        usage=None,
    )


def parse_answer_selection(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", raw, re.I)
    if fenced:
        raw = fenced.group(1).strip()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("answer composer output must be one JSON object")
    return value


def build_answer_prompts(user_request: str, claims: ClaimSet) -> tuple[str, str]:
    """Build an IDs-only presentation prompt from validated claims.

    Claim topics are included only as routing hints. For text/command facts they
    are not trusted user-facing prose; the composer is therefore forbidden from
    returning any prose at all. Runtime materializes selected claim IDs using
    the same epistemic rules as the deterministic renderer.
    """

    rows = [
        {
            "id": claim.id,
            "kind": claim.kind.value,
            "topic": claim.topic,
            "confidence": claim.confidence.value,
            "scope": claim.scope.value,
            "relation": claim.relation.value,
            "evidence_refs": list(claim.evidence_refs),
        }
        for claim in claims.claims
    ]
    system = """你是 ScopeX 最终答案编排器，不是诊断 Agent。没有工具，也不允许继续调查。
输入已经是 Validated Claims。你唯一的任务是把 claim ID 分配到产品展示区；绝不能复述、改写或新增任何事实、原因、结论、动作或建议文本。

严格规则：
1. 只输出 JSON，且顶层字段只能是 conclusion_claim_ids、explanation_claim_ids、execution_claim_ids、recommendation_claim_ids。
2. 每个字段的值只能是 claim ID 数组，不得输出任何自然语言字段。
3. conclusion_claim_ids 必须 1-2 个，且只能来自 summary_claim_ids。
4. explanation_claim_ids 最多 4 个，用于补充关键依据或限定条件。
5. execution_claim_ids 最多 4 个，只能选择 kind=fact 的 claim；只有当 claim 标签明显描述已执行动作、动作结果或动作后状态时才放这里。
6. recommendation_claim_ids 最多 3 个，只能选择 relation=causal_hypothesis 或 kind=unknown 的 claim；它表示“下一步优先验证什么”，不是授权执行动作。
7. 同一个 claim ID 最多出现在一个展示区；summary_claim_ids 中的每个 ID 都必须至少被选择一次。
8. 不要根据常识补充流程，不要设计新的调查步骤，不要把假设升级成事实。

只输出 JSON 对象。"""
    user = (
        f"原任务：{user_request}\n\n"
        "Validated Claims（topic 仅用于分类，禁止复制到输出）：\n"
        + json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
        + "\n\nsummary_claim_ids："
        + json.dumps(list(claims.summary_claim_ids), ensure_ascii=False)
        + "\n\n严格输出示例："
        + '{"conclusion_claim_ids":["C1"],"explanation_claim_ids":["C2"],'
        + '"execution_claim_ids":[],"recommendation_claim_ids":[]}'
    )
    return system, user


def validate_answer_selection(payload: Any, claims: ClaimSet) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, Mapping):
        return ["top_level_object"]
    if set(payload) != _SELECTION_FIELDS:
        errors.append("top_level_fields")

    by_id = claims.by_id()
    selected_by_field: dict[str, list[str]] = {}
    limits = {
        "conclusion_claim_ids": (1, 2),
        "explanation_claim_ids": (0, 4),
        "execution_claim_ids": (0, 4),
        "recommendation_claim_ids": (0, 3),
    }

    for field, (minimum, maximum) in limits.items():
        value = payload.get(field)
        if (
            not isinstance(value, list)
            or not all(isinstance(item, str) for item in value)
            or len(value) != len(set(value))
            or not minimum <= len(value) <= maximum
        ):
            errors.append(field)
            selected_by_field[field] = []
            continue
        rows = list(value)
        selected_by_field[field] = rows
        if any(cid not in by_id for cid in rows):
            errors.append(field + ".unknown_claim")

    conclusion = selected_by_field.get("conclusion_claim_ids", [])
    summary_ids = set(claims.summary_claim_ids)
    if any(cid not in summary_ids for cid in conclusion):
        errors.append("conclusion_claim_ids.not_summary")

    execution = selected_by_field.get("execution_claim_ids", [])
    if any(by_id[cid].kind is not ClaimKind.FACT for cid in execution if cid in by_id):
        errors.append("execution_claim_ids.not_fact")

    recommendation = selected_by_field.get("recommendation_claim_ids", [])
    for cid in recommendation:
        claim = by_id.get(cid)
        if claim is None:
            continue
        if not (
            claim.kind is ClaimKind.UNKNOWN
            or claim.relation is ClaimRelation.CAUSAL_HYPOTHESIS
        ):
            errors.append("recommendation_claim_ids.not_uncertain")
            break

    all_selected = [
        cid
        for field in _SELECTION_FIELDS
        for cid in selected_by_field.get(field, [])
    ]
    if len(all_selected) != len(set(all_selected)):
        errors.append("duplicate_claim_across_sections")
    if not summary_ids.issubset(set(all_selected)):
        errors.append("summary_claim_ids_missing")

    return errors


def _dedupe_text(items: list[EvidenceItem]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for item in items:
        raw = item.raw.strip()
        if not raw or raw in seen:
            continue
        seen.add(raw)
        rows.append(raw)
    return rows


def _materialized_text(claim: Claim, catalog: EvidenceCatalog) -> str:
    items = [catalog.get(ref) for ref in claim.evidence_refs]

    if claim.kind is ClaimKind.FACT:
        if any(item.metadata.get("evidence_type") == "image" for item in items):
            # Existing trust semantics: image fact prose is allowed only because
            # Fresh Finalizer re-opened the SHA-verified original image.
            return claim.topic.strip()
        # Existing trust semantics: text/command fact topic is NOT publishable;
        # publish the exact claim-grade Evidence instead.
        rows = _dedupe_text(items)
        return "；".join(rows) if rows else "已验证事实（无可展示文本）"

    if claim.relation is ClaimRelation.TEMPORAL_ASSOCIATION:
        rows = _dedupe_text(items)
        evidence_text = "；".join(rows) if rows else "相关证据"
        return evidence_text + "。这些证据在当前调查范围内存在时间关联，但尚不能据此证明因果。"

    if claim.relation is ClaimRelation.CAUSAL_HYPOTHESIS:
        return claim.topic.strip() + "（待验证假设）"

    return claim.topic.strip() + "（尚不能确定）"


def _materialize_item(claim: Claim, catalog: EvidenceCatalog) -> ProductAnswerItem:
    return ProductAnswerItem(
        claim_id=claim.id,
        text=_materialized_text(claim, catalog),
        kind=claim.kind.value,
        relation=claim.relation.value,
        confidence=claim.confidence.value,
        evidence_refs=claim.evidence_refs,
    )


def materialize_product_answer(
    selection: Mapping[str, list[str]],
    claims: ClaimSet,
    catalog: EvidenceCatalog,
) -> ProductAnswer:
    by_id = claims.by_id()

    def section(field: str) -> tuple[ProductAnswerItem, ...]:
        return tuple(_materialize_item(by_id[cid], catalog) for cid in selection[field])

    return ProductAnswer(
        conclusion=section("conclusion_claim_ids"),
        explanation=section("explanation_claim_ids"),
        execution=section("execution_claim_ids"),
        recommendation=section("recommendation_claim_ids"),
    )


class ConstrainedAnswerComposer:
    """One no-tool model call that may only organize already validated claim IDs."""

    def __init__(
        self,
        client: StreamingFinalizerClient,
        *,
        model: str,
        max_tokens: int = 256,
    ) -> None:
        self.client = client
        self.model = model
        self.max_tokens = max_tokens

    def run(
        self,
        *,
        user_request: str,
        claims: ClaimSet,
        catalog: EvidenceCatalog,
    ) -> AnswerCompositionResult:
        system, user = build_answer_prompts(user_request, claims)
        try:
            transport = self.client.complete(
                model=self.model,
                system_prompt=system,
                user_prompt=user,
                max_tokens=self.max_tokens,
                temperature=0,
            )
        except (OSError, ValueError) as exc:
            return AnswerCompositionResult(
                _empty_transport(),
                None,
                "answer_composer_transport_error:" + str(exc),
                (),
                None,
            )

        if not transport.done_seen:
            return AnswerCompositionResult(
                transport,
                None,
                "answer_composer_stream_incomplete",
                (),
                None,
            )
        if not transport.finish_reasons:
            return AnswerCompositionResult(
                transport,
                None,
                "answer_composer_missing_finish_reason",
                (),
                None,
            )
        if transport.finish_reasons[-1] == "length":
            return AnswerCompositionResult(
                transport,
                None,
                "answer_composer_truncated",
                (),
                None,
            )
        if transport.finish_reasons[-1] != "stop":
            return AnswerCompositionResult(
                transport,
                None,
                "answer_composer_finish_reason:" + transport.finish_reasons[-1],
                (),
                None,
            )

        try:
            payload = parse_answer_selection(transport.content)
        except (ValueError, json.JSONDecodeError) as exc:
            return AnswerCompositionResult(transport, None, str(exc), (), None)

        errors = tuple(validate_answer_selection(payload, claims))
        if errors:
            return AnswerCompositionResult(transport, None, None, errors, None)

        selection = {field: list(payload[field]) for field in _SELECTION_FIELDS}
        answer = materialize_product_answer(selection, claims, catalog)
        return AnswerCompositionResult(transport, selection, None, (), answer)
