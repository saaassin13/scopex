#!/usr/bin/env python3
"""Read-only POC01 diagnostics. Never changes answers, grades, or run artifacts.

Python >= 3.10; standard library only; no network or model calls.
A complete JSON code fence may be unwrapped FOR DIAGNOSIS ONLY.
Original strict results remain authoritative for the original contract.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys


def strict_loads(text):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError('duplicate JSON key: ' + key)
            out[key] = value
        return out

    def invalid(value):
        raise ValueError('non-JSON constant: ' + value)

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


def same_json(left, right):
    # Matches poc01.exact_json semantics: bool != int; array order matters;
    # object key order and whitespace do not. Never repairs fields or values.
    encode = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False,
                                      allow_nan=False)
    return encode(left) == encode(right)


def classify_answer(answer, expected):
    """Return strict match, diagnosed value match, and wrapper flag.

    content_match=None means no unambiguous JSON could be extracted under
    the deliberately narrow rule; it does not claim the content is wrong.
    """
    if not isinstance(answer, str):
        return {'strict_match': False, 'content_match': None, 'wrapper': False}
    try:
        match = same_json(strict_loads(answer), expected)
        return {'strict_match': match, 'content_match': match, 'wrapper': False}
    except (ValueError, TypeError):
        pass
    fence = re.fullmatch(r'```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```',
                         answer.strip(), flags=re.IGNORECASE)
    if fence:
        try:
            match = same_json(strict_loads(fence.group(1)), expected)
            return {'strict_match': False, 'content_match': match, 'wrapper': True}
        except (ValueError, TypeError):
            pass
    return {'strict_match': False, 'content_match': None, 'wrapper': bool(fence)}


def diagnose(result, case):
    answer = classify_answer(result.get('answer'), case['expected'])
    reads = set(result.get('successful_reads') or [])
    reads_ok = case['mode'] not in ('tool', 'chain') or set(case['files']) <= reads
    listing_ok = (case['mode'] != 'chain' or
                  result.get('grade', {}).get('required_listing') is True)
    evidence_ok = reads_ok and listing_ok
    finished = result.get('status') in ('correct', 'wrong_answer_or_missing_evidence')
    content_ok = finished and evidence_ok and answer['content_match'] is True
    if not finished:
        category = 'not_completed'
    elif not evidence_ok:
        category = 'missing_evidence'
    elif answer['strict_match']:
        category = 'strict_match'
    elif answer['content_match'] is True:
        category = 'format_only'
    elif answer['content_match'] is False:
        category = 'content_mismatch'
    else:
        category = 'unparseable'
    strict_recheck = finished and evidence_ok and answer['strict_match']
    return {**answer, 'category': category, 'evidence_ok': evidence_ok,
            'content_and_evidence': content_ok,
            'record_consistent': bool(result.get('correct')) == strict_recheck}


def duplicate_calls(path):
    """Identical name+input repeats, NOT a blanket claim of wasted work."""
    if not path.is_file():
        return None
    counts = Counter()
    for line in path.read_text(encoding='utf-8').splitlines():
        event = strict_loads(line)
        if event.get('event') != 'tool_started':
            continue
        raw = event.get('arguments')
        if raw is None:
            return None
        try:
            args = json.dumps(strict_loads(raw), sort_keys=True, ensure_ascii=False)
        except (ValueError, TypeError):
            args = str(raw)
        counts[(str(event.get('name')), args)] += 1
    return sum(n - 1 for n in counts.values())


def cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def render_run(run):
    run = Path(run).resolve()
    cases = strict_loads((run / 'cases.json').read_text(encoding='utf-8'))
    results = strict_loads((run / 'results.json').read_text(encoding='utf-8'))
    if not isinstance(cases, list) or not isinstance(results, list):
        raise ValueError('cases.json / results.json must contain arrays')
    by_id = {case['id']: case for case in cases}
    if len(by_id) != len(cases):
        raise ValueError('duplicate case ids')
    plan_path = run / 'plan.json'
    plan = strict_loads(plan_path.read_text(encoding='utf-8')) if plan_path.is_file() else {}
    warmup = plan.get('warmup')
    lines = ['# POC01 只读诊断', '', '运行目录：' + str(run),
             '预热标记：' + str(warmup if warmup is not None else 'UNKNOWN'),
             '', '**不修改原答案、correct、within_sla 或原 summary.md。**',
             'format_only 仅表示去掉完整单一代码块后内容及读取证据符合；原纯 JSON 验收仍失败。',
             'unparseable 表示不能按本规则判断内容，不能直接归类为提取错误。', '',
             '| 用例 | 原状态 | 诊断分类 | 秒 | 轮数 | 工具数 | 同参额外调用 |',
             '|---|---|---|---:|---:|---:|---:|']
    counts = {}
    for index, result in enumerate(results, 1):
        name = result['case']
        detail = diagnose(result, by_id[name])
        folder = (run / f'{index:03d}-{name}').resolve()
        if not folder.is_relative_to(run):
            raise ValueError('invalid case path')
        repeated = duplicate_calls(folder / 'events.jsonl')
        values = (name, result.get('status'), detail['category'], result.get('wall_s'),
                  result.get('rounds'), result.get('tool_calls'),
                  repeated if repeated is not None else 'UNKNOWN')
        lines.append('| ' + ' | '.join(cell(v) for v in values) + ' |')
        if not detail['record_consistent']:
            lines.append('记录一致性警告：' + cell(name) + '；保留原分数并人工核对。')
        group = counts.setdefault(name, Counter())
        group['attempts'] += 1
        group['original_correct'] += result.get('correct') is True
        group['original_sla'] += result.get('within_sla') is True
        group['content_and_evidence'] += detail['content_and_evidence']
        group[detail['category']] += 1
    lines += ['', '| 用例 | 已尝试 | 原严格正确 | 原 SLA 内正确 | 内容+证据符合（诊断） | 仅格式 |',
              '|---|---:|---:|---:|---:|---:|']
    for name, group in sorted(counts.items()):
        n = group['attempts']
        lines.append('| ' + ' | '.join([cell(name), str(n)] +
                     [f'{group[key]}/{n}' for key in ('original_correct', 'original_sla',
                       'content_and_evidence', 'format_only')]) + ' |')
    planned = plan.get('attempts')
    if isinstance(planned, list):
        if len(results) > len(planned):
            raise ValueError('attempted results exceed recorded plan')
        lines += ['', f'计划 {len(planned)}；已尝试 {len(results)}；未运行 {len(planned)-len(results)}。']
    else:
        lines += ['', '计划缺失：不能断言测试矩阵完整。']
    lines += ['', 'strict_match=纯 JSON、内容和证据符合；format_only=仅代码块包装违约；',
              'content_mismatch=可解析但值/字段/顺序不符；missing_evidence=读取或列目录证据缺失；',
              'not_completed=超时/错误/中断等，不能用残留答案计成功。',
              '同参调用只描述轨迹；是否必要需结合工具返回及数据是否变化判断。',
              '以上为少量基础探针诊断，不是最终 Agent 完成率。原始答案和推理文本未打印。']
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', nargs='+', type=Path, help='one or more POC01 run directories')
    args = parser.parse_args(argv)
    code = 0
    for run in args.runs:
        try:
            print(render_run(run))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            print(f'ERROR: {run}: {exc}', file=sys.stderr)
            code = 2
    return code


if __name__ == '__main__':
    sys.exit(main())
