"""Small, business-agnostic labels from the native answer, not a second judge.

Task text remains the sole per-run business criterion. This module validates the
footer's envelope only: it cannot certify the model's semantic correctness.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

VERSION = 1
STATUSES = frozenset({'not_assessed', 'pending', 'normal', 'abnormal', 'needs_review'})
VERDICTS = frozenset({'normal', 'abnormal', 'needs_review'})
PUSH_DECISIONS = frozenset({'suggested', 'none', 'undecided'})
START = '<scopex_result>'
END = '</scopex_result>'
MAX_SUMMARY = 240
MAX_FOOTER = 1024

# A universal output envelope, NOT a business template or hidden threshold.
INSTRUCTION = '''\n\n[ScopeX optional result label; version 1]
先完成本次任务并正常回答；在同一次最终回答末尾单独附加一行：
<scopex_result>{"status":"normal|abnormal|needs_review","summary":"一句简短理由"}</scopex_result>
status 只能选一个值（不要输出竖线）：normal=在所要求范围内未见异常，abnormal=有依据满足本任务的异常条件，needs_review=无法确定/必要信息或判据不明确。
异常条件来自用户任务说明及有效的后续指令，数据语义沿用已加载 Skill；不发明阈值，不另做一次调查，不重新打印证据，不调用额外工具来填写标签。
多个检查项：只要已有依据满足用户的异常组合条件即可 abnormal；没有已确定异常且有必要项无法判断则 needs_review；只有所要求项均有足够观察且未触发才 normal。没有业务判定目标时用 needs_review。不要将“不知道物理原因”自动等同于“无法判断已有现象是否满足条件”。
严格区分“任一/同时”“超过/达到”与当前/持续条件；影响结论的歧义或必要数据缺失不得猜测，summary 指明需复核原因。采样结论不外推全部时段。
summary 用中文且不超过240字，只概括本次结论；标签不是置信度证明，也不是推送命令。正文和这一个末尾标签保持一致，不在正文中重复或演示这个标签。
'''


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


def parse_footer(text: str, *, require_body: bool = True) -> tuple[dict[str, str] | None, str | None]:
    """Accept exactly one bounded terminal envelope; never keyword-classify prose.

    Returns (payload, exact footer suffix). Invalid/missing metadata is not a
    failed native answer and must never trigger a format-repair model request.
    """
    if not isinstance(text, str) or text.count(START) != 1 or text.count(END) != 1:
        return None, None
    start = text.index(START)
    footer = text[start:]
    if len(footer) > MAX_FOOTER or not footer.rstrip().endswith(END):
        return None, None
    if start and text[start - 1] != '\n':
        return None, None
    body = text[:start].strip()
    if require_body and not body:
        return None, None
    # Do not interpret a quoted/code-example footer as the final assessment.
    if len(re.findall(r'^\s*```', text[:start], re.MULTILINE)) % 2:
        return None, None
    raw = footer.rstrip()[len(START):-len(END)]
    try:
        value = json.loads(raw, object_pairs_hook=_unique_pairs)
    except (ValueError, RecursionError):
        return None, None
    if not isinstance(value, dict) or set(value) != {'status', 'summary'}:
        return None, None
    status, summary = value['status'], value['summary']
    if not isinstance(status, str) or status not in VERDICTS:
        return None, None
    if not isinstance(summary, str) or not summary.strip() or len(summary) > MAX_SUMMARY:
        return None, None
    if any(ord(c) < 32 for c in summary) or START in summary or END in summary:
        return None, None
    return {'status': status, 'summary': summary.strip()}, footer


def record(status: str, summary: str, *, source: str = 'system', reason: str = '',
           model_calls: int = 0, answer: str = '', request: str = '') -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError('unsupported assessment status')
    return {
        'version': VERSION, 'status': status, 'summary': summary[:MAX_SUMMARY],
        'source': source, 'reason': reason, 'model_calls': model_calls,
        'push_decision': 'suggested' if status == 'abnormal' else
                         'undecided' if status in {'not_assessed', 'pending'} else 'none',
        'delivery_status': 'not_connected',
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'answer_sha256': digest(answer) if answer else None,
        'request_sha256': digest(request) if request else None,
        'semantic_validation': False,
    }


def from_native(answer: str, *, complete: bool, no_data: bool, request: str) -> tuple[dict, str | None]:
    payload, footer = parse_footer(answer)
    if not complete:
        value = record('needs_review', '任务执行未完整结束；已有正文不能作为已完成的异常判定。',
                       reason='execution_incomplete', answer=answer, request=request)
    elif no_data:
        value = record('needs_review', '指定窗口没有可用数据，不能据此认定设备正常或故障。',
                       reason='no_data', answer=answer, request=request)
    elif payload is None:
        value = record('needs_review', '原任务未给出有效的结果标签；正文已保留，未自动追加模型调用。',
                       reason='label_missing_or_invalid', answer=answer, request=request)
    else:
        value = record(payload['status'], payload['summary'], source='native',
                       answer=answer, request=request)
    return value, footer


def task_summary(task: dict) -> dict:
    """Read only task metadata, never report text/evidence or the model."""
    metadata = task.get('metadata') or {}
    saved = metadata.get('assessment')
    state = task.get('state')
    if isinstance(saved, dict) and saved.get('version') == VERSION and saved.get('status') in STATUSES:
        value = dict(saved)
        if state in {'FAILED', 'CANCELLED'} and value['status'] in {'normal', 'abnormal', 'pending'}:
            value = record('needs_review', '任务执行中断或失败，需要复核。', reason='execution_incomplete')
            value['updated_at'] = saved.get('updated_at')
            return value
        return value
    if metadata.get('assessment_enabled') is True:
        if state in {'COMPLETED', 'FAILED', 'CANCELLED'}:
            value = record('needs_review', '本次未形成可用结果判定。', reason='assessment_unavailable')
        else:
            value = record('pending', '随原任务的最终回答生成判定，不增加第二次报告调用。')
    else:
        value = record('not_assessed', '本次未开启自动判定；可在结束后主动评估。')
    # Computed display status is not a newly performed assessment.
    value['updated_at'] = None
    return value
