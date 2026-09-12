from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from scopex.evidence.catalog import EvidenceCatalog
from scopex.finalizer.client import FinalizerResponse, StreamingFinalizerClient
from scopex.finalizer.service import FinalizationResult, FinalizationService
from scopex.finalizer.validator import normalize_claim_payload


@dataclass(frozen=True, slots=True)
class StructuredFinalizerResult:
    transport: FinalizerResponse
    payload: dict[str, Any] | None
    parse_error: str | None
    finalization: FinalizationResult | None
    normalizations: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return (
            self.parse_error is None
            and self.finalization is not None
            and self.finalization.valid
            and self.transport.done_seen
            and bool(self.transport.finish_reasons)
            and self.transport.finish_reasons[-1] == "stop"
        )


def parse_structured_payload(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", raw, re.I)
    if fenced:
        raw = fenced.group(1).strip()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("structured finalizer output must be one JSON object")
    return value


def build_structured_prompts(user_request: str, catalog: EvidenceCatalog) -> tuple[str, str]:
    """Build a compact generic finalizer prompt with bounded output size."""

    evidence = "\n".join(
        f"{item.ref} | source={item.source} | {item.raw}"
        for item in catalog.items
    )
    system = """你是 ScopeX 证据校准器。调查已结束，没有工具。
只能依据证据目录输出一个紧凑 JSON；不要继续调查，不要输出自然语言报告。

claim 字段固定：id, kind, topic, evidence_refs, confidence, scope, relation。
取值：
- kind: fact | inference | unknown
- confidence: high | medium | low | unknown
- scope: event | time_window | component | global | unknown
- relation: observed | temporal_association | causal_hypothesis | unknown

kind 与 relation 必须严格匹配：
- observed -> fact
- temporal_association -> inference
- causal_hypothesis -> inference
- unknown -> unknown
不要输出 hypothesis、causal、observation 等其他 kind 值。

规则：
1. 只输出 3-6 个最重要 claim；topic 最多 24 个中文字符或约 48 个 ASCII 字符。
2. fact 必须 relation=observed 且只引用能直接支持该事实的精确证据；优先引用最少必要证据。
3. temporal_association 至少引用两个不同事件证据，只表示时间关联，不表示因果。
4. causal_hypothesis 只能 medium/low，明确是未证实假设。
5. unknown 的 confidence=unknown；可引用相关证据作为上下文。
6. 不把局部观察扩大成全局结论，不把常识/典型原因写成已观察事实。
7. summary_claim_ids 最多 4 个，只列最重要 claim。
8. 不复制日志全文到 topic，不增加额外字段。
9. 不要生成结构上重复的 claim：相同 fact 不要因 topic 换词重复；同一组证据的同类时间关联也只保留一个。

只输出 JSON 对象，可有或没有 json fence。"""
    user = f"""原任务：{user_request}

证据目录：
{evidence or '(empty)'}

严格输出：
{{"claims":[{{"id":"C1","kind":"fact|inference|unknown","topic":"短标签","evidence_refs":["E1"],"confidence":"high|medium|low|unknown","scope":"event|time_window|component|global|unknown","relation":"observed|temporal_association|causal_hypothesis|unknown"}}],"summary_claim_ids":["C1"]}}"""
    return system, user


class StructuredFinalizer:
    """One fresh no-tool model call followed by deterministic validation/rendering."""

    def __init__(
        self,
        client: StreamingFinalizerClient,
        *,
        model: str,
        max_tokens: int = 768,
        service: FinalizationService | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.service = service or FinalizationService()

    def run(self, *, user_request: str, catalog: EvidenceCatalog) -> StructuredFinalizerResult:
        system, user = build_structured_prompts(user_request, catalog)
        transport = self.client.complete(
            model=self.model,
            system_prompt=system,
            user_prompt=user,
            max_tokens=self.max_tokens,
            temperature=0,
        )

        if not transport.done_seen:
            return StructuredFinalizerResult(
                transport, None, "structured_finalizer_stream_incomplete", None
            )
        if not transport.finish_reasons:
            return StructuredFinalizerResult(
                transport, None, "structured_finalizer_missing_finish_reason", None
            )
        if transport.finish_reasons[-1] == "length":
            return StructuredFinalizerResult(
                transport, None, "structured_finalizer_truncated", None
            )
        if transport.finish_reasons[-1] != "stop":
            return StructuredFinalizerResult(
                transport,
                None,
                "structured_finalizer_finish_reason:" + transport.finish_reasons[-1],
                None,
            )

        try:
            raw_payload = parse_structured_payload(transport.content)
        except (ValueError, json.JSONDecodeError) as exc:
            return StructuredFinalizerResult(transport, None, str(exc), None)

        normalized, normalizations = normalize_claim_payload(raw_payload)
        if not isinstance(normalized, dict):
            return StructuredFinalizerResult(
                transport,
                None,
                "structured finalizer normalized payload must be one JSON object",
                None,
                normalizations,
            )
        finalization = self.service.finalize(normalized, catalog)
        return StructuredFinalizerResult(
            transport,
            normalized,
            None,
            finalization,
            normalizations,
        )
