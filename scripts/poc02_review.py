#!/usr/bin/env python3
"""Reassess an existing POC02-A record, without inference or modifying old files.

v2 separates replay safety, final delivery, content, paged-read evidence and SLA.
No shell, Docker, native runtime, HTTP, answer repair, or automatic retry.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import re
import sys
sys.dont_write_bytecode = True
import poc02_run as runner

LEGACY_ERROR = 'ValueError: CLI reported incomplete/aborted/fallback result'


def bounded_bytes(path, cap=8 * 1024 * 1024):
    path = Path(path)
    if path.is_symlink(): raise ValueError('symlink audit file rejected')
    with path.open('rb') as handle: raw = handle.read(cap + 1)
    if len(raw) > cap: raise ValueError('audit file exceeds size cap')
    return raw




def final_response(raw, mime):
    text = raw.decode('utf-8')
    if 'text/event-stream' in mime:
        data = [line[5:].strip() for line in text.splitlines() if line.startswith('data:')]
        if not data or data[-1] != '[DONE]': raise ValueError('SSE missing terminal DONE')
        events = [runner.load(row) for row in data[:-1]]
        stream = True
    else:
        events = [runner.load(text)]; stream = False
    chunks, finishes = [], []
    for event in events:
        if not isinstance(event, dict) or event.get('error'): raise ValueError('response error')
        for choice in event.get('choices', []):
            if choice.get('index', 0) != 0: raise ValueError('multiple-choice result needs review')
            part = choice.get('delta' if stream else 'message') or {}
            if part.get('tool_calls'): raise ValueError('last response still requests a tool')
            content = part.get('content')
            if content is not None and not isinstance(content, str): raise ValueError('non-text final response')
            if content: chunks.append(content)
            if choice.get('finish_reason') is not None: finishes.append(choice['finish_reason'])
    if finishes != ['stop']: raise ValueError('final response did not finish with stop')
    return ''.join(chunks)


def review(run):
    run = Path(run).resolve()
    original = runner.read(run / 'result.json')
    ref, cfg, case, _ = runner.bundle(Path(original['preflight']))
    source = bounded_bytes(Path(ref['staging_directory']) / 'input.log', 262144)
    if runner.sha(source) != ref['input_sha256'] or original.get('input_sha256') != ref['input_sha256']:
        raise ValueError('input hash mismatch')
    cli_bytes = bounded_bytes(run / 'agent.stdout.txt', runner.LIMIT)
    outcome = runner.cli_outcome(cli_bytes)
    blockers = list(outcome['blockers'])
    removed_legacy = []
    for error in original.get('errors') or []:
        if error == LEGACY_ERROR and outcome['flags']['replayInvalid'] is True and not outcome['blockers']:
            removed_legacy.append('legacy replayInvalid-as-failure predicate')
        else: blockers.append('original_wrapper_error_requires_review')
    if (original.get('synthetic_response') is not False or original.get('agent_task_attempted') is not True
            or original.get('returncode') != 0 or original.get('stop') is not None):
        blockers.append('process_not_cleanly_finished')
    if original.get('sandbox', {}).get('problems') != []: blockers.append('sandbox_not_verified')
    rows = original.get('wire')
    if not isinstance(rows, list) or not rows: raise ValueError('wire history missing')
    expected_ids = list(range(1, len(rows) + 1))
    if [r.get('index') for r in rows] != expected_ids: raise ValueError('wire indexes not contiguous')
    if len(list(run.glob('wire-*-request.json'))) != len(rows): raise ValueError('wire manifest count mismatch')
    payloads = []
    for row in rows:
        if (row.get('forwarded') is not True or row.get('http_status') != 200
                or row.get('response_complete') is not True or row.get('error_type')
                or row.get('wire', {}).get('problems') != []):
            blockers.append('transport_or_wire_not_complete')
        body = bounded_bytes(run / f'wire-{row["index"]:02d}-request.json', runner.LIMIT)
        if runner.sha(body) != row.get('request_sha256'): raise ValueError('request hash mismatch')
        payloads.append(runner.load(body))
    last = rows[-1]
    raw_bytes = bounded_bytes(run / f'wire-{last["index"]:02d}-response.bin')
    if len(raw_bytes) != last.get('response_bytes'): raise ValueError('last response length mismatch')
    wire_answer = final_response(raw_bytes, last.get('content_type', ''))
    answer = outcome['answer']
    if wire_answer != answer: blockers.append('raw_and_delivered_answers_differ')
    marks = runner.grade(answer, case['expected']) if isinstance(answer, str) else {
        'strict_match': False, 'content_match': None, 'format_only': False}
    coverage = runner.read_coverage(payloads[-1].get('messages', []), source)
    wall = original.get('wall_s')
    if type(wall) not in (int, float) or not math.isfinite(wall) or wall < 0: raise ValueError('invalid wall time')
    strict = not blockers and marks['strict_match'] and coverage['status'] == 'verified_paged_read'
    within = strict and wall <= ref['sla_s']
    status = ('REVIEW_BLOCKED' if blockers else 'PASS_SINGLE_CASE' if within else
              'CORRECT_OVER_SLA' if strict else 'EVIDENCE_REVIEW_REQUIRED' if coverage['status'] != 'verified_paged_read'
              else 'FORMAT_ONLY' if marks['format_only'] else 'NOT_PASSED')
    return {'assessment_version': 2, 'read_only': True, 'model_calls': 0,
            'original_status': original.get('status'), 'review_status': status,
            'original_records_unchanged': True, 'corrected_predicate': removed_legacy,
            'strict_correct': strict, 'within_sla': within, 'wall_s': wall, 'sla_s': ref['sla_s'],
            'grade': marks, 'runtime_flags': outcome['flags'], 'warnings': outcome['warnings'],
            'automatic_replay_allowed': False, 'blockers': blockers, 'evidence': coverage,
            'source_hashes': {'result': runner.sha(bounded_bytes(run / 'result.json')),
                              'cli_stdout': runner.sha(cli_bytes), 'last_response': runner.sha(raw_bytes),
                              'input': ref['input_sha256']}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args(argv)
    print('# POC02-A 分项复核（原记录不修改）\n')
    print(json.dumps(review(args.run), ensure_ascii=False, indent=2))
    # 0 = review executed, NOT necessarily a passed/SLA-successful model task.
    return 0


if __name__ == '__main__':
    try: sys.exit(main())
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        print('REVIEW_ERROR:', type(exc).__name__ + ':', str(exc), file=sys.stderr); sys.exit(2)
