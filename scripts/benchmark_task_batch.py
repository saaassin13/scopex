#!/usr/bin/env python3
"""Measure complete task batches, including queue, tools and text reports.

Never starts/stops services, changes model parameters, or deletes task assets.
Run against an idle server with schedules disabled and explicitly read-only cases.
A timing improvement is not business-quality acceptance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import ipaddress
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

TERMINAL = {'COMPLETED', 'FAILED', 'CANCELLED'}


def endpoint(value):
    url = urlsplit(value)
    host = url.hostname or ''
    if host == 'localhost': host = '127.0.0.1'
    if url.scheme != 'http' or url.path not in {'', '/'} or url.query or url.fragment or url.username or url.password:
        raise ValueError('use credential-free local http://127.0.0.1:8787')
    if not ipaddress.ip_address(host).is_loopback:
        raise ValueError('benchmark API must be loopback; use an existing SSH tunnel')
    return host, url.port or 8787


def request(base, path, body=None):
    host, port = endpoint(base)
    conn = http.client.HTTPConnection(host, port, timeout=30)
    try:
        conn.request('POST' if body is not None else 'GET', path,
                     body=json.dumps(body).encode() if body is not None else None,
                     headers={'Content-Type': 'application/json'})
        response = conn.getresponse()
        raw = response.read(4 * 1024 * 1024)
        if response.status not in {200, 202}:
            raise ValueError(f'API {path}: HTTP {response.status}: {raw[:300]!r}')
        return json.loads(raw)
    finally: conn.close()


def load_cases(path):
    raw = Path(path).read_bytes()
    if len(raw) > 65536: raise ValueError('case file exceeds 64 KiB')
    value = json.loads(raw)
    cases = value.get('cases')
    if value.get('read_only') is not True or not value.get('dataset_id'):
        raise ValueError('case file must explicitly declare read_only=true and dataset_id')
    if not isinstance(cases, list) or not 2 <= len(cases) <= 16:
        raise ValueError('provide 2..16 fixed-window read-only cases')
    ids = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get('id'), str) or not case['id'] or case['id'] in ids:
            raise ValueError('case IDs must be distinct nonempty strings')
        if not isinstance(case.get('message'), str) or not 1 <= len(case['message']) <= 32768:
            raise ValueError('each case needs a bounded message')
        ids.add(case['id'])
    return value, hashlib.sha256(raw).hexdigest()


def run_batch(*, base_url, cases_file, expected_slots, timeout_s):
    manifest, digest = load_cases(cases_file)
    before = request(base_url, '/activity')
    if before['tasks']: raise ValueError('server must be idle, including paused/queued tasks')
    if before['max_active_tasks'] != expected_slots:
        raise ValueError('server task concurrency does not match --expected-slots')
    if len(manifest['cases']) > before['max_active_tasks'] + before['max_queued_tasks']:
        raise ValueError('batch exceeds configured active slots plus queue capacity')
    schedules = request(base_url, '/schedules')
    if any(row.get('enabled') for row in schedules.get('schedules', [])):
        raise ValueError('disable schedules for controlled benchmarking; this script never changes them')
    out = {'schema': 1, 'started_at': datetime.now(timezone.utc).isoformat(),
           'cases_sha256': digest, 'dataset_id': manifest['dataset_id'],
           'runtime': before, 'runs': [], 'all_delivered': False,
           'quality_acceptance': 'pending_manual_review', 'errors': []}
    start = time.monotonic()
    try:
        for case in manifest['cases']:
            task = request(base_url, '/runs', {'message': case['message']})
            out['runs'].append({'case_id': case['id'], 'task_id': task['id'],
                                'submitted_offset_s': time.monotonic() - start})
        pending = {row['task_id']: row for row in out['runs']}
        while pending and time.monotonic() - start < timeout_s:
            for task_id, row in tuple(pending.items()):
                task = request(base_url, '/tasks/' + task_id)
                if task['state'] not in TERMINAL: continue
                result = request(base_url, '/tasks/' + task_id + '/result')
                payload = result.get('result') or {}
                row.update(task=task, result=result, observed_finished_offset_s=time.monotonic()-start)
                row['delivered'] = (task['state'] == 'COMPLETED' and payload.get('version') == 2
                                    and (payload.get('report_meta') or {}).get('status') == 'complete'
                                    and bool(payload.get('report_text')))
                pending.pop(task_id)
            if pending: time.sleep(0.5)
        if pending:
            out['errors'].append('batch_timeout; unfinished task IDs retained; no automatic cancellation')
            out['unfinished_task_ids'] = list(pending)
        out['all_delivered'] = bool(out['runs']) and len(out['runs']) == len(manifest['cases']) and all(row.get('delivered') for row in out['runs'])
    except (OSError, ValueError) as exc:
        out['errors'].append(type(exc).__name__ + ': ' + str(exc)[:500])
    out['batch_elapsed_s'] = round(time.monotonic()-start, 3)
    return out


def compare(serial, parallel, *, quality_confirmed=False):
    for key in ('cases_sha256', 'dataset_id'):
        if not serial.get(key) or serial.get(key) != parallel.get(key):
            raise ValueError('batch inputs differ: ' + key)
    if not serial.get('all_delivered') or not parallel.get('all_delivered'):
        raise ValueError('cannot claim speedup from failed, missing or incomplete reports')
    if serial['runtime'].get('scopex_commit') != parallel['runtime'].get('scopex_commit'):
        raise ValueError('compare the same output code revision; do not mix report redesign with concurrency gain')
    if serial['runtime']['max_active_tasks'] != 1 or parallel['runtime']['max_active_tasks'] <= 1:
        raise ValueError('first batch must be serial and second batch parallel')
    a,b = serial['batch_elapsed_s'],parallel['batch_elapsed_s']
    if not isinstance(a,(int,float)) or not isinstance(b,(int,float)) or a <= 0 or b <= 0:
        raise ValueError('invalid batch duration')
    improvement=(a-b)/a
    return {'serial_s':a,'parallel_s':b,'speedup':round(a/b,3),
            'reduction_percent':round(improvement*100,2),
            'status':('PASS_OBSERVED_PAIR' if improvement>0 else 'NO_SPEEDUP') if quality_confirmed else 'TIMING_ONLY_QUALITY_PENDING',
            'note':'Repeat matched warm/cold-cache trials and inspect source coverage, model settings and production workload. One pair is not a throughput guarantee.'}


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base-url',default='http://127.0.0.1:8787')
    ap.add_argument('--cases',type=Path)
    ap.add_argument('--expected-slots',type=int,choices=(1,2,3,4))
    ap.add_argument('--timeout',type=int,default=1800)
    ap.add_argument('--out',type=Path)
    ap.add_argument('--execute-read-only',action='store_true',help='explicitly authorize the listed model/tool tasks')
    ap.add_argument('--compare',nargs=2,type=Path,metavar=('SERIAL','PARALLEL'))
    ap.add_argument('--quality-confirmed',action='store_true',help='only after human review of both batches')
    args=ap.parse_args(argv)
    if args.compare:
        value=compare(*(json.loads(p.read_text()) for p in args.compare),quality_confirmed=args.quality_confirmed)
        print(json.dumps(value,ensure_ascii=False,indent=2)); return 0
    if not args.execute_read_only or args.cases is None or args.out is None or args.expected_slots is None:
        ap.error('requires --execute-read-only --cases --expected-slots --out')
    if not 60 <= args.timeout <= 7200: ap.error('--timeout must be 60..7200')
    args.out.parent.mkdir(parents=True,exist_ok=True)
    # Reserve output before any side effect; never overwrite an earlier measurement.
    with args.out.open('x',encoding='utf-8') as f:
        value=run_batch(base_url=args.base_url,cases_file=args.cases,expected_slots=args.expected_slots,timeout_s=args.timeout)
        json.dump(value,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in value.items() if k not in {'runs','runtime'}},ensure_ascii=False,indent=2))
    return 0 if value['all_delivered'] else 2


if __name__ == '__main__':
    try: raise SystemExit(main())
    except (OSError,ValueError) as exc: print(type(exc).__name__+': '+str(exc),file=sys.stderr);raise SystemExit(2)
