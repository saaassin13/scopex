#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_SNAPSHOT = Path('/scopex-host/current.json')


def num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def main() -> int:
    ap = argparse.ArgumentParser(description='Render one current ScopeX host snapshot as compact business facts.')
    ap.add_argument('--snapshot', type=Path, default=DEFAULT_SNAPSHOT)
    args = ap.parse_args()

    if not args.snapshot.is_file():
        ap.error(f'host snapshot does not exist: {args.snapshot}')
    try:
        payload = json.loads(args.snapshot.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        ap.error(str(exc))
    if not isinstance(payload, dict) or payload.get('source') != 'scopex_host_snapshot':
        ap.error('invalid ScopeX host snapshot')

    cpu = payload.get('cpu') if isinstance(payload.get('cpu'), dict) else {}
    memory = payload.get('memory') if isinstance(payload.get('memory'), dict) else {}
    disks = payload.get('disks') if isinstance(payload.get('disks'), list) else []
    root_disk = next((row for row in disks if isinstance(row, dict) and row.get('mount') == '/'), None)
    if root_disk is None:
        root_disk = next((row for row in disks if isinstance(row, dict)), {})
    gpu_rows = [row for row in (payload.get('gpu') or []) if isinstance(row, dict)]

    gpu_utils = [num(row.get('util_percent')) for row in gpu_rows]
    gpu_utils = [value for value in gpu_utils if value is not None]
    gpu_mem_used = [num(row.get('memory_used_mib')) for row in gpu_rows]
    gpu_mem_used = [value for value in gpu_mem_used if value is not None]
    gpu_mem_total = [num(row.get('memory_total_mib')) for row in gpu_rows]
    gpu_mem_total = [value for value in gpu_mem_total if value is not None]

    facts = {
        'captured_at': payload.get('captured_at'),
        'cpu_util_percent': num(cpu.get('util_percent')),
        'cpu_count': cpu.get('count'),
        'load1': num(cpu.get('load1')),
        'load5': num(cpu.get('load5')),
        'load15': num(cpu.get('load15')),
        'memory_total_gb': num(memory.get('total_gb')),
        'memory_used_gb': num(memory.get('used_gb')),
        'memory_available_gb': num(memory.get('available_gb')),
        'swap_used_gb': num(memory.get('swap_used_gb')),
        'disk_root_total_gb': num(root_disk.get('total_gb')) if isinstance(root_disk, dict) else None,
        'disk_root_used_gb': num(root_disk.get('used_gb')) if isinstance(root_disk, dict) else None,
        'disk_root_free_gb': num(root_disk.get('free_gb')) if isinstance(root_disk, dict) else None,
        'disk_root_used_percent': num(root_disk.get('used_percent')) if isinstance(root_disk, dict) else None,
        'gpu_count': len(gpu_rows),
        'gpu_util_percent_max': max(gpu_utils) if gpu_utils else None,
        'gpu_memory_used_mib_total': sum(gpu_mem_used) if gpu_mem_used else None,
        'gpu_memory_total_mib_total': sum(gpu_mem_total) if gpu_mem_total else None,
        'docker_container_count': len(payload.get('docker') or []) if isinstance(payload.get('docker'), list) else None,
    }
    errors = payload.get('errors') if isinstance(payload.get('errors'), dict) else {}
    result = {
        'scopex_role': 'business_facts',
        'schema': 1,
        'source': 'system-health',
        'facts': facts,
        'quality': {
            'collector_errors': errors,
            'gpu_available': bool(gpu_rows),
            'root_disk_available': bool(root_disk),
        },
    }
    print(json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
