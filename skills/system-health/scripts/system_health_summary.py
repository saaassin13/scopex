#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import statistics
from typing import Any


def parse_time(text: str) -> datetime:
    value = text.strip().replace('Z', '+00:00')
    for candidate in (value, value.replace(' ', 'T')):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f'invalid timestamp: {text}')


def in_window(ts: datetime, start: datetime | None, end: datetime | None) -> bool:
    if start is not None:
        if ts.tzinfo is not None and start.tzinfo is None:
            start = start.replace(tzinfo=ts.tzinfo)
        if ts.tzinfo is None and start.tzinfo is not None:
            ts = ts.replace(tzinfo=start.tzinfo)
        if ts < start:
            return False
    if end is not None:
        if ts.tzinfo is not None and end.tzinfo is None:
            end = end.replace(tzinfo=ts.tzinfo)
        if ts.tzinfo is None and end.tzinfo is not None:
            ts = ts.replace(tzinfo=end.tzinfo)
        if ts >= end:
            return False
    return True


def nums(rows: list[dict[str, Any]], getter) -> list[float]:
    out = []
    for row in rows:
        value = getter(row)
        if isinstance(value, (int, float)):
            out.append(float(value))
    return out


def stats(values: list[float], *, low=False) -> dict[str, float] | None:
    if not values:
        return None
    data = {
        'avg': round(statistics.fmean(values), 3),
        'min': round(min(values), 3),
        'max': round(max(values), 3),
    }
    data['worst'] = data['min'] if low else data['max']
    return data


def disk_series(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    by_mount: dict[str, list[float]] = {}
    for row in rows:
        for disk in row.get('disks') or []:
            if not isinstance(disk, dict):
                continue
            mount = disk.get('mount')
            free = disk.get('free_gb')
            if isinstance(mount, str) and isinstance(free, (int, float)):
                by_mount.setdefault(mount, []).append(float(free))
    return {mount: stats(values, low=True) for mount, values in by_mount.items() if values}


def gpu_series(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_gpu: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        for gpu in row.get('gpu') or []:
            if not isinstance(gpu, dict):
                continue
            idx = str(gpu.get('index', 'unknown'))
            slot = by_gpu.setdefault(idx, {'util': [], 'temp': [], 'memory': [], 'power': []})
            for key, out_key in (
                ('util_percent', 'util'), ('temperature_c', 'temp'),
                ('memory_used_mib', 'memory'), ('power_w', 'power'),
            ):
                value = gpu.get(key)
                if isinstance(value, (int, float)):
                    slot[out_key].append(float(value))
    result = {}
    for idx, values in by_gpu.items():
        result[idx] = {
            'util_percent': stats(values['util']),
            'temperature_c': stats(values['temp']),
            'memory_used_mib': stats(values['memory']),
            'power_w': stats(values['power']),
        }
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description='Summarize factual ScopeX host resource history.')
    ap.add_argument('history', nargs='?', type=Path, default=Path('/scopex-system-metrics/system_metrics.jsonl'))
    ap.add_argument('--start', type=parse_time)
    ap.add_argument('--end', type=parse_time)
    ap.add_argument('--limit', type=int, default=20000)
    args = ap.parse_args()

    if not args.history.is_file():
        ap.error(f'history file does not exist: {args.history}')

    rows: list[dict[str, Any]] = []
    bad_lines = 0
    with args.history.open(encoding='utf-8', errors='replace') as f:
        for line in f:
            if len(rows) >= args.limit:
                break
            try:
                row = json.loads(line)
                ts = parse_time(str(row['ts']))
            except (json.JSONDecodeError, KeyError, TypeError, argparse.ArgumentTypeError):
                bad_lines += 1
                continue
            if in_window(ts, args.start, args.end):
                rows.append(row)

    if not rows:
        print(json.dumps({'schema': 1, 'samples': 0, 'bad_lines': bad_lines}, ensure_ascii=False))
        return 1

    cpu = nums(rows, lambda r: (r.get('cpu') or {}).get('util_percent'))
    load1 = nums(rows, lambda r: (r.get('cpu') or {}).get('load1'))
    mem_avail = nums(rows, lambda r: (r.get('memory') or {}).get('available_gb'))
    mem_used = nums(rows, lambda r: (r.get('memory') or {}).get('used_gb'))

    result = {
        'schema': 1,
        'samples': len(rows),
        'bad_lines': bad_lines,
        'from': rows[0].get('ts'),
        'to': rows[-1].get('ts'),
        'cpu_util_percent': stats(cpu),
        'load1': stats(load1),
        'memory_available_gb': stats(mem_avail, low=True),
        'memory_used_gb': stats(mem_used),
        'disk_free_gb': disk_series(rows),
        'gpu': gpu_series(rows),
        'latest': rows[-1],
        'collector_errors': [
            {'ts': r.get('ts'), 'errors': r.get('errors')}
            for r in rows if r.get('errors')
        ][-20:],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
