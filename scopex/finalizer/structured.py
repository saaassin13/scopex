from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from scopex.evidence.catalog import EvidenceCatalog
from scopex.finalizer.client import FinalizerResponse, StreamingFinalizerClient
from scopex.finalizer.service import FinalizationResult, FinalizationService


@dataclass(frozen=True, slots=True)
class StructuredFinalizerResult:
    transport: FinalizerResponse
    payload: dict[str, Any] | None
    parse_error: str | None
    finalization: FinalizationResult | None

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
    """Build the generic product prompt; contains no device-specific expected answer."""

    evidence = "\n".join(
        f"{item.ref} | source={item.source} | {item.raw}"
        for item in catalog.items
    )
    system = """你是 ScopeX 的证据校准器。调查阶段已经结束，不存在任何工具。
只能依据证据目录输出结构化 claims；不要继续调查、请求额外信息或输出自然语言报告。

每个 claim 字段固定：
- id: C1, C2 ...
- kind: fact | inference | unknown
- topic: 简短主题标签，不用于替代原始事实证据
- evidence_refs: 只能引用目录中的 E 编号
- confidence: high | medium | low | unknown
- scope: event | time_window | component | global | unknown
- relation: observed | temporal_association | causal_hypothesis | unknown

约束：
1. fact 必须是证据直接观察到的内容，relation=observed，并引用直接证据。
2. temporal_association 只表示时间关联，至少引用两个证据；不能自动升级成因果。
3. causal_hypothesis 必须明确为未证实假设，只能 medium/low。
4. unknown 的 confidence 必须是 unknown；可引用相关证据说明未知事项的上下文。
5. 证据只覆盖其 source/scope 所能支持的范围，不把局部观察扩大成全局结论。
6. 不得把常识、典型原因或概率经验写成已经观察到的事实。

只输出一个 JSON 对象，可有或没有 Markdown json fence，不要额外解释。"""
    user = f"""【原任务】
{user_request}

【调查证据目录】
{evidence or '(empty)'}

输出结构：
{{
  "claims": [
    {{
      "id": "C1",
      "kind": "fact|inference|unknown",
      "topic": "short topic",
      "evidence_refs": ["E1"],
      "confidence": "high|medium|low|unknown",
      "scope": "event|time_window|component|global|unknown",
      "relation": "observed|temporal_association|causal_hypothesis|unknown"
    }}
  ],
  "summary_claim_ids": ["C1"]
}}

只包含对原任务有用的 claims。"""
    return system, user


class StructuredFinalizer:
    """One fresh no-tool model call followed by deterministic runtime validation/rendering."""

    def __init__(
        self,
        client: StreamingFinalizerClient,
        *,
        model: str,
        max_tokens: int = 512,
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
        try:
            payload = parse_structured_payload(transport.content)
        except (ValueError, json.JSONDecodeError) as exc:
            return StructuredFinalizerResult(transport, None, str(exc), None)
        finalization = self.service.finalize(payload, catalog)
        return StructuredFinalizerResult(transport, payload, None, finalization)
