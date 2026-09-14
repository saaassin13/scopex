#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
from typing import Any

TS_RE = re.compile(r'^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}:\d{3})')
TS_FMT = '%Y-%m-%d %H:%M:%S:%f'


def parse_time(text: str) -> datetime:
    try:
        return datetime.strptime(text, TS_FMT)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f'invalid timestamp: {text}') from exc


def line_time(text: str) -> datetime | None:
    m = TS_RE.match(text)
    if not m:
        return None
    try:
        return datetime.strptime(m.group('ts'), TS_FMT)
    except ValueError:
        return None


def keyword_match(text: str, keywords: list[str], regex: bool) -> bool:
    if not keywords:
        return True
    if regex:
        return any(re.search(pattern, text) is not None for pattern in keywords)
    lower = text.lower()
    return any(word.lower() in lower for word in keywords)


def main() -> int:
    ap = argparse.ArgumentParser(description='Extract bounded raw log context by time and/or keyword without diagnosing it.')
    ap.add_argument('logs', nargs='+', type=Path)
    ap.add_argument('--center', type=parse_time)
    ap.add_argument('--window-s', type=float, default=5.0)
    ap.add_argument('--start', type=parse_time)
    ap.add_argument('--end', type=parse_time)
    ap.add_argument('--keyword', action='append', default=[])
    ap.add_argument('--regex', action='store_true')
    ap.add_argument('--before', type=int, default=3)
    ap.add_argument('--after', type=int, default=3)
    ap.add_argument('--max-lines', type=int, default=200)
    args = ap.parse_args()

    if args.center and (args.start or args.end):
        ap.error('use --center or --start/--end, not both')
    if not args.center and not args.start and not args.end and not args.keyword:
        ap.error('at least one time boundary or --keyword is required')
    if args.center:
        start = args.center - timedelta(seconds=args.window_s)
        end = args.center + timedelta(seconds=args.window_s)
    else:
        start, end = args.start, args.end

    output: list[dict[str, Any]] = []
    total_anchors = 0
    total_selected = 0
    for path in args.logs:
        if not path.is_file():
            output.append({'source': str(path), 'error': 'file_not_found', 'lines': []})
            continue
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
        anchors: list[int] = []
        for i, text in enumerate(lines):
            ts = line_time(text)
            if start is not None and (ts is None or ts < start):
                continue
            if end is not None and (ts is None or ts >= end):
                continue
            if not keyword_match(text, args.keyword, args.regex):
                continue
            anchors.append(i)
        total_anchors += len(anchors)
        selected: set[int] = set()
        for i in anchors:
            lo = max(0, i - max(0, args.before))
            hi = min(len(lines), i + max(0, args.after) + 1)
            selected.update(range(lo, hi))
        rows = []
        for i in sorted(selected):
            if total_selected >= args.max_lines:
                break
            rows.append({
                'line_no': i + 1,
                'ts': TS_RE.match(lines[i]).group('ts') if TS_RE.match(lines[i]) else None,
                'anchor': i in anchors,
                'raw': lines[i],
            })
            total_selected += 1
        output.append({
            'source': str(path),
            'anchors': len(anchors),
            'lines': rows,
            'truncated': total_selected >= args.max_lines,
        })
        if total_selected >= args.max_lines:
            break

    result = {
        'schema': 1,
        'query': {
            'start': start.strftime(TS_FMT) if start else None,
            'end': end.strftime(TS_FMT) if end else None,
            'keywords': args.keyword,
            'regex': args.regex,
            'before': args.before,
            'after': args.after,
            'max_lines': args.max_lines,
        },
        'anchors': total_anchors,
        'selected_lines': total_selected,
        'sources': output,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if total_anchors else 1


if __name__ == '__main__':
    raise SystemExit(main())
