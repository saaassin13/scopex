from __future__ import annotations

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


def _cpu_percent(interval_s: float = 0.15) -> float | None:
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


def _meminfo() -> dict[str, float | None]:
    values: dict[str, int] = {}
    try:
        for line in Path('/proc/meminfo').read_text(encoding='utf-8').splitlines():
            if ':' not in line:
                continue
            key, raw = line.split(':', 1)
            values[key] = int(raw.strip().split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        return {'total_gb': None, 'available_gb': None, 'used_gb': None, 'swap_used_gb': None}
    total = values.get('MemTotal')
    available = values.get('MemAvailable')
    used = None if total is None or available is None else total - available
    swap_total = values.get('SwapTotal', 0)
    swap_free = values.get('SwapFree', 0)
    gb = 1024 ** 3
    return {
        'total_gb': round(total / gb, 3) if total is not None else None,
        'available_gb': round(available / gb, 3) if available is not None else None,
        'used_gb': round(used / gb, 3) if used is not None else None,
        'swap_used_gb': round((swap_total - swap_free) / gb, 3),
    }


def _disk(mount: str) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(mount)
    except OSError as exc:
        return {'mount': mount, 'error': str(exc)}
    return {
        'mount': mount,
        'total_gb': round(usage.total / 1024 ** 3, 3),
        'used_gb': round(usage.used / 1024 ** 3, 3),
        'free_gb': round(usage.free / 1024 ** 3, 3),
        'used_percent': round(usage.used * 100.0 / usage.total, 2) if usage.total else None,
    }


def _gpu() -> tuple[list[dict[str, Any]], str | None]:
    fields = ['index', 'name', 'utilization.gpu', 'memory.used', 'memory.total', 'temperature.gpu', 'power.draw']
    cmd = ['nvidia-smi', '--query-gpu=' + ','.join(fields), '--format=csv,noheader,nounits']
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env={**os.environ, 'LC_ALL': 'C'})
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], str(exc)
    if proc.returncode != 0:
        return [], proc.stderr.strip() or f'exit={proc.returncode}'
    rows = []
    for line in proc.stdout.splitlines():
        parts = [p.strip() for p in line.split(',')]
        if len(parts) != len(fields):
            continue
        def num(value: str) -> float | None:
            try:
                return float(value)
            except ValueError:
                return None
        rows.append({
            'index': parts[0],
            'name': parts[1],
            'util_percent': num(parts[2]),
            'memory_used_mib': num(parts[3]),
            'memory_total_mib': num(parts[4]),
            'temperature_c': num(parts[5]),
            'power_w': num(parts[6]),
        })
    return rows, None


def _docker() -> tuple[list[dict[str, Any]], str | None]:
    try:
        proc = subprocess.run(
            ['docker', 'stats', '--no-stream', '--format', '{{json .}}'],
            capture_output=True,
            text=True,
            timeout=8,
            env={**os.environ, 'LC_ALL': 'C'},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], str(exc)
    if proc.returncode != 0:
        return [], proc.stderr.strip() or f'exit={proc.returncode}'
    rows = []
    for line in proc.stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows, None


def _top(sort_arg: str, limit: int = 5) -> tuple[list[dict[str, Any]], str | None]:
    try:
        proc = subprocess.run(
            ['ps', '-eo', 'pid=,comm=,%cpu=,%mem=,rss=', f'--sort={sort_arg}'],
            capture_output=True,
            text=True,
            timeout=5,
            env={**os.environ, 'LC_ALL': 'C'},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], str(exc)
    if proc.returncode != 0:
        return [], proc.stderr.strip() or f'exit={proc.returncode}'
    rows = []
    for line in proc.stdout.splitlines()[:limit]:
        parts = line.strip().split(None, 4)
        if len(parts) != 5:
            continue
        try:
            rows.append({
                'pid': int(parts[0]),
                'command': parts[1],
                'cpu_percent': float(parts[2]),
                'memory_percent': float(parts[3]),
                'rss_mib': round(int(parts[4]) / 1024, 2),
            })
        except ValueError:
            continue
    return rows, None


def collect_current_host_snapshot(*, mounts: tuple[str, ...] = ('/',)) -> dict[str, Any]:
    errors: dict[str, str] = {}
    gpu, gpu_error = _gpu()
    if gpu_error:
        errors['gpu'] = gpu_error
    docker, docker_error = _docker()
    if docker_error:
        errors['docker'] = docker_error
    top_cpu, top_cpu_error = _top('-%cpu')
    if top_cpu_error:
        errors['top_cpu'] = top_cpu_error
    top_memory, top_memory_error = _top('-rss')
    if top_memory_error:
        errors['top_memory'] = top_memory_error
    try:
        load1, load5, load15 = os.getloadavg()
        load = {'load1': round(load1, 3), 'load5': round(load5, 3), 'load15': round(load15, 3)}
    except OSError:
        load = {'load1': None, 'load5': None, 'load15': None}
    return {
        'schema': 1,
        'source': 'scopex_host_snapshot',
        'captured_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'cpu': {'util_percent': _cpu_percent(), 'count': os.cpu_count(), **load},
        'memory': _meminfo(),
        'disks': [_disk(mount) for mount in mounts],
        'gpu': gpu,
        'docker': docker,
        'top_cpu': top_cpu,
        'top_memory': top_memory,
        'errors': errors,
    }


def write_current_host_snapshot(path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = collect_current_host_snapshot()
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temp.replace(path)
    return path
