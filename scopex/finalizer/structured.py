from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from scopex.evidence.catalog import EvidenceCatalog, EvidenceItem
from scopex.evidence.media import EvidenceMediaLoader
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
    image_evidence_refs: tuple[str, ...] = ()
    retry_count: int = 0

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


def _compact_preview(value: Any, *, max_chars: int = 320) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    one_line = value.replace("\r", "\\r").replace("\n", "\\n")
    if len(one_line) <= max_chars:
        return one_line
    return one_line[:max_chars] + "…"


def _evidence_prompt_line(item: EvidenceItem) -> str:
    evidence_type = item.metadata.get("evidence_type")
    if evidence_type == "file_line":
        line = item.metadata.get("line_number")
        return f"{item.ref} | line={line} | {item.raw}"
    if evidence_type == "command_line":
        line = item.metadata.get("line_number")
        return f"{item.ref} | line={line} | {item.raw}"
    if evidence_type == "image":
        digest = item.metadata.get("sha256")
        return (
            f"{item.ref} | type=image | source={item.source} | sha256={digest} | "
            "该图片作为同一 E ref 的多模态附件直接提供"
        )
    return f"{item.ref} | source={item.source} | {item.raw}"


def _group_key(item: EvidenceItem) -> tuple[Any, ...] | None:
    evidence_type = item.metadata.get("evidence_type")
    if evidence_type in {"file_line", "command_line"}:
        return evidence_type, item.tool_call_id, item.source
    return None


def _render_evidence_group(items: list[EvidenceItem]) -> str:
    first = items[0]
    evidence_type = first.metadata.get("evidence_type")
    rows = "\n".join(_evidence_prompt_line(item) for item in items)

    if evidence_type == "file_line":
        return (
            f"[evidence_block type=file_line source={first.source} "
            f"tool_call_id={first.tool_call_id or '-'}]\n"
            f"{rows}\n"
            "[/evidence_block]"
        )

    if evidence_type == "command_line":
        host = first.metadata.get("exec_host")
        command = first.metadata.get("command")
        result_digest = first.metadata.get("result_sha256")
        command_preview = _compact_preview(command)
        command_digest = None
        if isinstance(command, str) and command:
            command_digest = hashlib.sha256(command.encode("utf-8", errors="replace")).hexdigest()
        header = (
            f"[evidence_block type=command_line source={first.source} "
            f"tool_call_id={first.tool_call_id or '-'} host={host} "
            f"result_sha256={result_digest}"
        )
        if command_digest is not None:
            header += f" command_sha256={command_digest}"
        if command_preview is not None:
            header += " command_preview=" + json.dumps(command_preview, ensure_ascii=False)
        header += "]"
        return f"{header}\n{rows}\n[/evidence_block]"

    return rows


def build_evidence_prompt(catalog: EvidenceCatalog) -> str:
    """Render all Evidence refs losslessly while avoiding repeated tool metadata.

    Runtime Evidence remains line-granular for validation. The finalizer prompt is
    only a compact projection: contiguous lines from the same tool call/source
    share one metadata header, and long shell/Python commands are represented by
    a bounded preview plus a full SHA-256. No Evidence ref or raw evidence line is
    removed by this compaction.
    """

    blocks: list[str] = []
    pending: list[EvidenceItem] = []
    pending_key: tuple[Any, ...] | None = None

    def flush() -> None:
        nonlocal pending, pending_key
        if pending:
            blocks.append(_render_evidence_group(pending))
            pending = []
            pending_key = None

    for item in catalog.items:
        key = _group_key(item)
        if key is None:
            flush()
            blocks.append(_evidence_prompt_line(item))
            continue
        if pending and key != pending_key:
            flush()
        if not pending:
            pending_key = key
        pending.append(item)
    flush()
    return "\n".join(blocks)


def build_structured_prompts(user_request: str, catalog: EvidenceCatalog) -> tuple[str, str]:
    """Build a compact generic finalizer prompt with bounded metadata overhead."""

    evidence = build_evidence_prompt(catalog)
    system = """你是 ScopeX 证据校准器。调查已结束，没有工具。
只能依据证据目录和本次直接附加的图片证据输出一个紧凑 JSON；不要继续调查，不要输出自然语言报告。

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
1. 只输出 1-5 个最重要 claim；topic 最多 24 个中文字符或约 48 个 ASCII 字符。
2. fact 必须 relation=observed 且只引用能直接支持该事实的精确证据；每个 evidence_refs 最多 4 个，优先引用最少必要证据。
3. temporal_association 引用 2-4 个不同事件证据，只表示时间关联，不表示因果。
4. causal_hypothesis 只能 medium/low，明确是未证实假设；evidence_refs 最多 4 个。
5. unknown 的 confidence=unknown；可引用最多 4 个相关证据作为上下文，也可以没有直接证据。
6. 不把局部观察扩大成全局结论，不把常识/典型原因写成已观察事实。
7. summary_claim_ids 最多 4 个，只列最重要 claim。
8. 不复制日志全文到 topic，不增加额外字段。
9. 不要生成结构上重复的 claim：同一组 evidence_refs 的同类 fact 不要仅因 topic 换词重复；同一组证据的同类时间关联也只保留一个。
10. type=image 的 Evidence 已以原图直接附加。视觉 fact 必须基于你本次亲自看到的图片内容并引用对应图片 E ref；不要假定调查 Agent 之前的图片描述正确，因为这些描述不是证据。
11. type=command_line 的 fact 只能陈述该行输出直接支持的信息；可以为同一次命令的不同输出行生成不同 fact，但不要把命令输出推断成未观察到的原因。
12. 如果多张图片呈现与原任务相关的明显不同状态、质量或内容差异，优先按图片或证据子集分别生成视觉 fact；不要把有意义的差异压缩成一个宽泛的场景描述。只有图片内容实质相同时才合并。
13. evidence_block 只是为了压缩重复的工具元数据；块内每个 E ref 仍是独立、精确的 Evidence。command_preview 可能被截断，完整命令只以 command_sha256 保持身份；不要根据被截断的命令内容推断额外事实。
14. 不要为了“覆盖全部证据”而枚举大量 E ref。每个 claim 只选择最直接的 1-4 个；Evidence 数量很多时仍然保持输出紧凑。
15. 整个输出只允许一个 JSON 对象，不要附加解释、Markdown、证据原文或第二份报告。

只输出 JSON 对象，可有或没有 json fence。"""
    user = f"""原任务：{user_request}

证据目录：
{evidence or '(empty)'}

严格输出：
{{"claims":[{{"id":"C1","kind":"fact|inference|unknown","topic":"短标签","evidence_refs":["E1"],"confidence":"high|medium|low|unknown","scope":"event|time_window|component|global|unknown","relation":"observed|temporal_association|causal_hypothesis|unknown"}}],"summary_claim_ids":["C1"]}}"""
    return system, user


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


_LENGTH_RETRY_SUFFIX = """

[长度恢复]
上一次结构化 JSON 因输出长度限制被截断。本次不是新调查，也没有新证据；只把同一批 Evidence 重写成更短的合法 JSON。
- 最多 4 个 claim；
- 每个 topic 最多 20 个中文字符或约 40 个 ASCII 字符；
- 每个 evidence_refs 最多 3 个，只保留最直接证据；
- summary_claim_ids 最多 3 个；
- 不复制任何证据原文，不输出解释文本。
[/长度恢复]
"""


class StructuredFinalizer:
    """Fresh no-tool finalization with one bounded length-recovery retry."""

    def __init__(
        self,
        client: StreamingFinalizerClient,
        *,
        model: str,
        max_tokens: int = 768,
        service: FinalizationService | None = None,
        media_loader: EvidenceMediaLoader | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.service = service or FinalizationService()
        self.media_loader = media_loader
        self.truncation_retry_max_tokens = (
            None if max_tokens >= 4096 else min(4096, max(1024, max_tokens * 2))
        )

    def _complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
        image_inputs: tuple[tuple[str, str], ...],
    ) -> FinalizerResponse:
        return self.client.complete(
            model=self.model,
            system_prompt=system,
            user_prompt=user,
            max_tokens=max_tokens,
            temperature=0,
            image_inputs=image_inputs,
        )

    def run(self, *, user_request: str, catalog: EvidenceCatalog) -> StructuredFinalizerResult:
        system, user = build_structured_prompts(user_request, catalog)
        image_inputs: tuple[tuple[str, str], ...] = ()
        image_evidence_refs: tuple[str, ...] = ()
        if self.media_loader is not None:
            try:
                images = self.media_loader.load(catalog)
            except (OSError, ValueError) as exc:
                return StructuredFinalizerResult(
                    _empty_transport(),
                    None,
                    str(exc),
                    None,
                )
            image_evidence_refs = tuple(image.ref for image in images)
            image_inputs = tuple(
                (
                    f"{image.ref} source={image.source} sha256={image.sha256}",
                    image.data_url,
                )
                for image in images
            )

        try:
            transport = self._complete(
                system=system,
                user=user,
                max_tokens=self.max_tokens,
                image_inputs=image_inputs,
            )
        except (OSError, ValueError) as exc:
            return StructuredFinalizerResult(
                _empty_transport(),
                None,
                "structured_finalizer_transport_error:" + str(exc),
                None,
                image_evidence_refs=image_evidence_refs,
            )

        retry_count = 0
        if (
            transport.done_seen
            and transport.finish_reasons
            and transport.finish_reasons[-1] == "length"
            and self.truncation_retry_max_tokens is not None
        ):
            retry_count = 1
            try:
                transport = self._complete(
                    system=system + _LENGTH_RETRY_SUFFIX,
                    user=user,
                    max_tokens=self.truncation_retry_max_tokens,
                    image_inputs=image_inputs,
                )
            except (OSError, ValueError) as exc:
                return StructuredFinalizerResult(
                    transport,
                    None,
                    "structured_finalizer_retry_transport_error:" + str(exc),
                    None,
                    image_evidence_refs=image_evidence_refs,
                    retry_count=retry_count,
                )

        if not transport.done_seen:
            return StructuredFinalizerResult(
                transport,
                None,
                "structured_finalizer_stream_incomplete",
                None,
                image_evidence_refs=image_evidence_refs,
                retry_count=retry_count,
            )
        if not transport.finish_reasons:
            return StructuredFinalizerResult(
                transport,
                None,
                "structured_finalizer_missing_finish_reason",
                None,
                image_evidence_refs=image_evidence_refs,
                retry_count=retry_count,
            )
        if transport.finish_reasons[-1] == "length":
            return StructuredFinalizerResult(
                transport,
                None,
                "structured_finalizer_truncated",
                None,
                image_evidence_refs=image_evidence_refs,
                retry_count=retry_count,
            )
        if transport.finish_reasons[-1] != "stop":
            return StructuredFinalizerResult(
                transport,
                None,
                "structured_finalizer_finish_reason:" + transport.finish_reasons[-1],
                None,
                image_evidence_refs=image_evidence_refs,
                retry_count=retry_count,
            )

        try:
            raw_payload = parse_structured_payload(transport.content)
        except (ValueError, json.JSONDecodeError) as exc:
            return StructuredFinalizerResult(
                transport,
                None,
                str(exc),
                None,
                image_evidence_refs=image_evidence_refs,
                retry_count=retry_count,
            )

        normalized, normalizations = normalize_claim_payload(raw_payload)
        if not isinstance(normalized, dict):
            return StructuredFinalizerResult(
                transport,
                None,
                "structured finalizer normalized payload must be one JSON object",
                None,
                normalizations,
                image_evidence_refs,
                retry_count,
            )
        finalization = self.service.finalize(normalized, catalog)
        return StructuredFinalizerResult(
            transport,
            normalized,
            None,
            finalization,
            normalizations,
            image_evidence_refs,
            retry_count,
        )
