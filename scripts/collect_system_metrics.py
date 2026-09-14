#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any


def _read_cpu_times() -> tuple[int, int]:
    first = Path('/proc/stat').read_text(encoding='utf-8').splitlines()[0].split()[1:]
    vals = [int(v) for v in first]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
    return sum(vals), idle


def cpu_percent(interval_s: float = 0.2) -> float | None:
    try:
        total0, idle0 = _read_cpu_times()
        time.sleep(interval_s)
        total1, idle1 = _read_cpu_times()
    except (OSError, ValueError, IndexError):
        return None
    dt = total1 - total0
    if dt <= 0:
        return None
    busy = dt - (idle1 - idle0)
    return round(max(0.0, min(100.0, busy * 100.0 / dt)), 2)


def meminfo() -> dict[str, float | None]:
    values: dict[str, int] = {}
    try:
        for line in Path('/proc/meminfo').read_text(encoding='utf-8').splitlines():
            if ':' not in line:
                continue
            key, raw = line.split(':', 1)
            token = raw.strip().split()[0]
            values[key] = int(token) * 1024
    except (OSError, ValueError, IndexError):
        return {'total_gb': None, 'available_gb': None, 'used_gb': None, 'swap_used_gb': None}

    total = values.get('MemTotal')
    available = values.get('MemAvailable')
    swap_total = values.get('SwapTotal', 0)
    swap_free = values.get('SwapFree', 0)
    used = None if total is None or available is None else total - available
    gb = 1024 ** 3
    return {
        'total_gb': round(total / gb, 3) if total is not None else None,
        'available_gb': round(available / gb, 3) if available is not None else None,
        'used_gb': round(used / gb, 3) if used is not None else None,
        'swap_used_gb': round((swap_total - swap_free) / gb, 3),
    }


def disk_snapshot(mounts: list[str]) -> list[dict[str, Any]]:
    rows = []
    for mount in mounts:
        try:
            usage = shutil.disk_usage(mount)
            rows.append({
                'mount': mount,
                'total_gb': round(usage.total / 1024 ** 3, 3),
                'used_gb': round(usage.used / 1024 ** 3, 3),
                'free_gb': round(usage.free / 1024 ** 3, 3),
                'used_percent': round(usage.used * 100.0 / usage.total, 2) if usage.total else None,
            })
        except OSError as exc:
            rows.append({'mount': mount, 'error': str(exc)})
    return rows


def _run_json_lines(cmd: list[str], timeout: float) -> tuple[list[dict[str, Any]], str | None]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env={**os.environ, 'LC_ALL': 'C'})
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], str(exc)
    if proc.returncode != 0:
        return [], (proc.stderr.strip() or f'exit={proc.returncode}')
    rows = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows, None


def gpu_snapshot() -> tuple[list[dict[str, Any]], str | None]:
    fields = [
        'index', 'name', 'utilization.gpu', 'memory.used', 'memory.total',
        'temperature.gpu', 'power.draw',
    ]
    cmd = ['nvidia-smi', '--query-gpu=' + ','.join(fields), '--format=csv,noheader,nounits']
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env={**os.environ, 'LC_ALL': 'C'})
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], str(exc)
    if proc.returncode != 0:
        return [], (proc.stderr.strip() or f'exit={proc.returncode}')
    rows = []
    for line in proc.stdout.splitlines():
        parts = [p.strip() for p in line.split(',')]
        if len(parts) != len(fields):
            continue

        def num(text: str) -> float | None:
            try:
                return float(text)
            except ValueError:
                return None

        rows.append({
            'index': parts[0], 'name': parts[1],
            'util_percent': num(parts[2]),
            'memory_used_mib': num(parts[3]),
            'memory_total_mib': num(parts[4]),
            'temperature_c': num(parts[5]),
            'power_w': num(parts[6]),
        })
    return rows, None


def docker_snapshot() -> tuple[list[dict[str, Any]], str | None]:
    return _run_json_lines(['docker', 'stats', '--no-stream', '--format', '{{json .}}'], 8)


def top_processes(sort_key: str, limit: int = 5) -> tuple[list[dict[str, Any]], str | None]:
    if sort_key not in {'cpu', 'rss'}:
        raise ValueError('invalid sort key')
    sort_arg = '-%cpu' if sort_key == 'cpu' else '-rss'
    cmd = ['ps', '-eo', 'pid=,comm=,%cpu=,%mem=,rss=', f'--sort={sort_arg}']
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env={**os.environ, 'LC_ALL': 'C'})
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], str(exc)
    if proc.returncode != 0:
        return [], (proc.stderr.strip() or f'exit={proc.returncode}')
    rows = []
    for line in proc.stdout.splitlines()[:limit]:
        parts = line.strip().split(None, 4)
        if len(parts) != 5:
            continue
        try:
            rows.append({
                'pid': int(parts[0]), 'command': parts[1],
                'cpu_percent': float(parts[2]), 'memory_percent': float(parts[3]),
                'rss_mib': round(int(parts[4]) / 1024, 2),
            })
        except ValueError:
            continue
    return rows, None


def collect(mounts: list[str]) -> dict[str, Any]:
    errors: dict[str, str] = {}
    gpu, gpu_err = gpu_snapshot()
    if gpu_err:
        errors['gpu'] = gpu_err
    docker, docker_err = docker_snapshot()
    if docker_err:
        errors['docker'] = docker_err
    top_cpu, cpu_err = top_processes('cpu')
    if cpu_err:
        errors['top_cpu'] = cpu_err
    top_mem, mem_err = top_processes('rss')
    if mem_err:
        errors['top_memory'] = mem_err

    try:
        load1, load5, load15 = os.getloadavg()
        load = {'load1': round(load1, 3), 'load5': round(load5, 3), 'load15': round(load15, 3)}
    except OSError:
        load = {'load1': None, 'load5': None, 'load15': None}

    return {
        'schema': 1,
        'mode': 'current_snapshot',
        'ts': datetime.now().astimezone().isoformat(timespec='seconds'),
        'cpu': {'util_percent': cpu_percent(), **load},
        'memory': meminfo(),
        'disks': disk_snapshot(mounts),
        'gpu': gpu,
        'docker': docker,
        'top_cpu': top_cpu,
        'top_memory': top_mem,
        'errors': errors,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description='Collect one current host resource snapshot as JSON facts.')
    ap.add_argument('--output-json', type=Path, help='optionally write/replace one current snapshot JSON file')
    ap.add_argument('--mount', action='append', default=[], help='filesystem mount to sample; repeatable')
    ap.add_argument('--pretty', action='store_true')
    args = ap.parse_args()

    row = collect(args.mount or ['/'])
    text = json.dumps(row, ensure_ascii=False, indent=2 if args.pretty else None, allow_nan=False)
    print(text)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        tmp = args.output_json.with_suffix(args.output_json.suffix + '.tmp')
        tmp.write_text(json.dumps(row, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        tmp.replace(args.output_json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
