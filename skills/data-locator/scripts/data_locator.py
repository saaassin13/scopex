#!/usr/bin/env python3
from __future__ import annotations

import argparse
from bisect import bisect_left
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
from typing import Any

CATALOG_DEFAULT = Path('/workspace/skills/data-locator/references/data-catalog.json')
TIME_FMT = '%Y-%m-%d %H:%M:%S'
LOG_RE = re.compile(r'^CowDisinfect-(?P<date>\d{8})-(?P<time>\d{6})\.log(?:\.(?P<rotation>\d+))?$')
MM_RE = re.compile(r'^(?P<stamp>\d{8}-\d{9})\.(?P<ext>jpg|jpeg|json|pcd)$', re.IGNORECASE)


def parse_time(value: str) -> datetime:
    try:
        return datetime.strptime(value, TIME_FMT)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f'invalid time {value!r}; expected YYYY-MM-DD HH:MM:SS') from exc


def hour_floor(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def hour_buckets(start: datetime, end: datetime, *, maximum: int) -> list[datetime]:
    if end <= start:
        raise ValueError('--end must be after --start')
    current = hour_floor(start)
    out = []
    while current < end:
        out.append(current)
        if len(out) > maximum:
            raise ValueError(f'time window exceeds max_hour_buckets={maximum}; narrow the request')
        current += timedelta(hours=1)
    return out


def load_catalog(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('schema') != 1 or not isinstance(value.get('sources'), dict):
        raise ValueError('invalid ScopeX data catalog')
    return value


def log_timestamp(name: str) -> tuple[datetime, int] | None:
    m = LOG_RE.fullmatch(name)
    if not m:
        return None
    ts = datetime.strptime(m.group('date') + m.group('time'), '%Y%m%d%H%M%S')
    return ts, int(m.group('rotation') or 0)


def log_groups(root: Path) -> list[tuple[datetime, list[tuple[int, str]]]]:
    grouped: dict[datetime, list[tuple[int, str]]] = {}
    if not root.is_dir():
        return []
    with os.scandir(root) as entries:
        for entry in entries:
            if not entry.is_file(follow_symlinks=False):
                continue
            parsed = log_timestamp(entry.name)
            if parsed is None:
                continue
            ts, rotation = parsed
            grouped.setdefault(ts, []).append((rotation, str(root / entry.name)))
    return [(ts, sorted(rows, key=lambda row: (row[0], row[1]))) for ts, rows in sorted(grouped.items())]


def locate_logs(root: Path, start: datetime, end: datetime, *, max_hours: int, max_files: int) -> dict[str, Any]:
    # Bound requested duration, but do not equate natural clock hours with log
    # file start hours: files may start at e.g. 10:23:36 and cover data past 11:00.
    hour_buckets(start, end, maximum=max_hours)
    groups = log_groups(root)
    matched: list[tuple[datetime, int, str]] = []
    intervals: list[dict[str, str]] = []
    for index, (group_start, files) in enumerate(groups):
        next_start = groups[index + 1][0] if index + 1 < len(groups) else group_start + timedelta(hours=1)
        group_end = max(next_start, group_start + timedelta(seconds=1))
        if group_start >= end or group_end <= start:
            continue
        intervals.append({
            'start': group_start.strftime(TIME_FMT),
            'end_exclusive': group_end.strftime(TIME_FMT),
        })
        for rotation, path in files:
            matched.append((group_start, rotation, path))
    matched.sort(key=lambda row: (row[0], row[1], row[2]))
    files = [row[2] for row in matched[:max_files]]
    return {
        'matching_count': len(matched),
        'files_truncated': len(matched) > max_files,
        'selection_mode': 'all_relevant_logs',
        'files': files,
        'selected_log_intervals': intervals,
    }


def multimodal_timestamp(name: str) -> datetime | None:
    m = MM_RE.fullmatch(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group('stamp'), '%Y%m%d-%H%M%S%f')
    except ValueError:
        return None


def _evenly_spaced(rows: list[tuple[datetime, str]], maximum: int) -> list[tuple[datetime, str]]:
    """Keep temporal coverage when a multimodal window exceeds its file budget."""
    if len(rows) <= maximum:
        return rows
    times = [row[0] for row in rows]
    span = times[-1] - times[0]
    targets = ([times[0] + span / 2] if maximum <= 1 else
               [times[0] + span * i / (maximum - 1) for i in range(maximum)])
    selected = set()
    for target in targets:
        index = bisect_left(times, target)
        neighbors = [i for i in (index - 1, index) if 0 <= i < len(rows)]
        selected.add(min(neighbors, key=lambda i: (abs(times[i] - target), i)))
    # A time gap may map several grid positions to one image. Do not fill the
    # allowance with a dense burst and imply uniform temporal coverage.
    return [rows[i] for i in sorted(selected)]


def locate_multimodal(root: Path, start: datetime, end: datetime, *, kind: str, max_hours: int, max_files: int) -> dict[str, Any]:
    buckets = hour_buckets(start, end, maximum=max_hours)
    matched: list[tuple[datetime, str]] = []
    scanned_dirs: list[str] = []
    accepted = {'jpg', 'jpeg'} if kind == 'jpg' else ({kind} if kind != 'all' else {'jpg', 'jpeg', 'json', 'pcd'})
    counts: dict[str, int] = {'jpg': 0, 'json': 0, 'pcd': 0}
    for bucket in buckets:
        hour_dir = root / bucket.strftime('%Y%m%d') / bucket.strftime('%H')
        if not hour_dir.is_dir():
            continue
        scanned_dirs.append(str(hour_dir))
        with os.scandir(hour_dir) as entries:
            for entry in entries:
                if not entry.is_file(follow_symlinks=False):
                    continue
                m = MM_RE.fullmatch(entry.name)
                if not m:
                    continue
                ext = m.group('ext').lower()
                normalized = 'jpg' if ext in {'jpg', 'jpeg'} else ext
                if normalized in counts:
                    counts[normalized] += 1
                if ext not in accepted and normalized not in accepted:
                    continue
                ts = multimodal_timestamp(entry.name)
                if ts is None or ts < start or ts >= end:
                    continue
                matched.append((ts, str(hour_dir / entry.name)))
    matched.sort(key=lambda row: (row[0], row[1]))
    truncated = len(matched) > max_files
    selected = _evenly_spaced(matched, max_files) if truncated else matched
    return {
        'matching_count': len(matched),
        'files_truncated': truncated,
        'selection_mode': 'evenly_spaced_time_sample' if truncated else 'all_matching_files',
        'files': [row[1] for row in selected],
        'selected_first_ts': selected[0][0].strftime('%Y-%m-%d %H:%M:%S.%f') if selected else None,
        'selected_last_ts': selected[-1][0].strftime('%Y-%m-%d %H:%M:%S.%f') if selected else None,
        'selected_count': len(selected),
        'largest_selected_gap_ms': max(((b[0] - a[0]).total_seconds() * 1000
                                        for a, b in zip(selected, selected[1:])), default=None),
        'scanned_hour_dirs': scanned_dirs,
        'directory_file_counts': counts,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description='Resolve bounded CowDisinfect files for one time window without recursive root scans.')
    ap.add_argument('--catalog', type=Path, default=CATALOG_DEFAULT)
    ap.add_argument('--source', required=True, choices=('cowdisinfect_logs', 'left_camera_multimodal'))
    ap.add_argument('--start', required=True, type=parse_time)
    ap.add_argument('--end', required=True, type=parse_time)
    ap.add_argument('--kind', choices=('all', 'jpg', 'json', 'pcd'), default='all')
    ap.add_argument('--max-files', type=int)
    args = ap.parse_args()
    if args.end <= args.start:
        ap.error('--end must be after --start')
    try:
        catalog = load_catalog(args.catalog)
        source = catalog['sources'][args.source]
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        ap.error(str(exc))
    root = Path(str(source['agent_path']))
    access = source.get('access') or {}
    max_hours = int(access.get('max_hour_buckets') or 24)
    default_max_files = int(access.get('max_files_per_operation') or 64)
    max_files = int(args.max_files or default_max_files)
    if max_files < 1 or max_files > default_max_files:
        ap.error(f'--max-files must be between 1 and catalog limit {default_max_files}')
    try:
        if args.source == 'cowdisinfect_logs':
            result = locate_logs(root, args.start, args.end, max_hours=max_hours, max_files=max_files)
        else:
            result = locate_multimodal(root, args.start, args.end, kind=args.kind, max_hours=max_hours, max_files=max_files)
    except ValueError as exc:
        ap.error(str(exc))
    payload = {
        'scopex_role': 'locator',
        'schema': 1,
        'source': args.source,
        'root': str(root),
        'window': {'start': args.start.strftime(TIME_FMT), 'end': args.end.strftime(TIME_FMT)},
        'kind': args.kind,
        **result,
    }
    print(json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
