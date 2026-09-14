#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import deque
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


def row(line_no: int, text: str, *, anchor: bool) -> dict[str, Any]:
    m = TS_RE.match(text)
    return {
        'line_no': line_no,
        'ts': m.group('ts') if m else None,
        'anchor': anchor,
        'raw': text,
    }


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
    if args.max_lines <= 0:
        ap.error('--max-lines must be positive')

    patterns: list[re.Pattern[str]] = []
    if args.regex:
        try:
            patterns = [re.compile(p) for p in args.keyword]
        except re.error as exc:
            ap.error(f'invalid regex: {exc}')

    if args.center:
        start = args.center - timedelta(seconds=args.window_s)
        end = args.center + timedelta(seconds=args.window_s)
    else:
        start, end = args.start, args.end

    def matches(text: str) -> bool:
        if not args.keyword:
            return True
        if args.regex:
            return any(pattern.search(text) is not None for pattern in patterns)
        lower = text.lower()
        return any(word.lower() in lower for word in args.keyword)

    output: list[dict[str, Any]] = []
    total_anchors = 0
    total_selected = 0
    global_truncated = False

    for path in args.logs:
        if total_selected >= args.max_lines:
            global_truncated = True
            break
        if not path.is_file():
            output.append({'source': str(path), 'error': 'file_not_found', 'anchors': 0, 'lines': [], 'truncated': False})
            continue

        before_buf: deque[tuple[int, str]] = deque(maxlen=max(0, args.before))
        selected: dict[int, dict[str, Any]] = {}
        anchors = 0
        pending_after = 0
        scan_truncated = False

        with path.open(encoding='utf-8', errors='replace') as f:
            for line_no, raw in enumerate(f, 1):
                text = raw.rstrip('\n')
                ts = line_time(text)
                in_time = True
                if start is not None and (ts is None or ts < start):
                    in_time = False
                if end is not None and (ts is None or ts >= end):
                    in_time = False
                is_anchor = in_time and matches(text)

                if is_anchor:
                    anchors += 1
                    total_anchors += 1
                    for prev_no, prev_text in before_buf:
                        if prev_no not in selected and total_selected + len(selected) < args.max_lines:
                            selected[prev_no] = row(prev_no, prev_text, anchor=False)
                    if line_no not in selected and total_selected + len(selected) < args.max_lines:
                        selected[line_no] = row(line_no, text, anchor=True)
                    elif line_no in selected:
                        selected[line_no]['anchor'] = True
                    pending_after = max(pending_after, max(0, args.after))
                elif pending_after > 0:
                    if line_no not in selected and total_selected + len(selected) < args.max_lines:
                        selected[line_no] = row(line_no, text, anchor=False)
                    pending_after -= 1

                before_buf.append((line_no, text))

                if total_selected + len(selected) >= args.max_lines:
                    scan_truncated = True
                    global_truncated = True
                    break

        rows = [selected[key] for key in sorted(selected)]
        total_selected += len(rows)
        output.append({
            'source': str(path),
            'anchors': anchors,
            'lines': rows,
            'truncated': scan_truncated,
        })

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
        'truncated': global_truncated,
        'sources': output,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if total_anchors else 1


if __name__ == '__main__':
    raise SystemExit(main())
