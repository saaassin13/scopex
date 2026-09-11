#!/usr/bin/env python3
"""Run ONE real POC02-A task after a passed native preflight.

Native OpenClaw owns the agent loop and tools. A loopback recorder forwards the
unchanged HTTP body to the existing loopback model service, never synthesizing
successful replies. Python >=3.10, standard library. Private records only.
"""
from __future__ import annotations
import argparse
import copy
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 4 * 1024 * 1024


def load(text):
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out: raise ValueError('duplicate JSON key')
            out[k] = v
        return out
    def bad(_): raise ValueError('non-JSON numeric constant')
    return json.loads(text, object_pairs_hook=pairs, parse_constant=bad)


def read(path):
    path = Path(path)
    if path.is_symlink(): raise ValueError('symlink record rejected')
    with path.open('rb') as f: data = f.read(LIMIT + 1)
    if len(data) > LIMIT: raise ValueError('record too large')
    return load(data)


def save(path, obj):
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, allow_nan=False)


def sha(data): return hashlib.sha256(data).hexdigest()


def endpoint(url):
    u = urlsplit(url)
    host = u.hostname or ''
    if host == 'localhost': host = '127.0.0.1'
    try: local = ipaddress.ip_address(host).is_loopback
    except ValueError: local = False
    if (not local or u.scheme not in ('http', 'https') or u.username or u.password
            or u.query or u.fragment or u.path.rstrip('/') != '/v1'):
        raise ValueError('only a credential-free loopback /v1 endpoint is allowed')
    return u.scheme, host, u.port or (443 if u.scheme == 'https' else 80)


def connect(url, timeout):
    scheme, host, port = endpoint(url)
    cls = http.client.HTTPSConnection if scheme == 'https' else http.client.HTTPConnection
    return cls(host, port, timeout=timeout)


def bundle(preflight):
    pf = Path(preflight).resolve()
    result, cfg = read(pf / 'result.json'), read(pf / 'openclaw.json')
    if (result.get('status') != 'PREFLIGHT_PASS_NOT_MODEL_EVAL' or result.get('errors')
            or result.get('native_exec_marker_returned') is not True
            or result.get('sandbox', {}).get('problems') != []):
        raise ValueError('requires a completed, passed native preflight')
    ref = read(pf.parent / 'reference.json')
    endpoint(ref['base_url'])
    baseline = Path(ref['baseline_run'])
    old_cfg_bytes = (baseline / 'config.json').read_bytes()
    if sha(old_cfg_bytes) != ref['baseline_config_sha256']:
        raise ValueError('baseline configuration changed')
    old_cfg = load(old_cfg_bytes)
    if any(canonical(old_cfg.get(k)) != canonical(ref.get(k)) for k in
           ('model', 'base_url', 'request', 'sla_s', 'timeout_s')):
        raise ValueError('reference differs from saved baseline')
    cases = read(baseline / 'cases.json')
    if canonical(cases) != canonical(read(Path(ref['suite']) / 'cases.json')):
        raise ValueError('task suite changed')
    tools = [c for c in cases if c.get('mode') == 'tool']
    if len(tools) != 1: raise ValueError('requires one imported tool task')
    case = tools[0]
    if (sha(case['prompt'].encode()) != ref['prompt_sha256'] or
            sha(canonical(case['expected']).encode()) != ref['expected_sha256']):
        raise ValueError('prompt or expected value changed')
    return ref, cfg, case, old_cfg.get('api_key_env', 'SCOPEX_API_KEY')


def real_config(cfg, ref, runtime, out, url, token, agent_id):
    """Relocate an already validated config; keep all behavioral settings."""
    c = copy.deepcopy(cfg)
    provider = c['models']['providers']['vllm']
    provider['baseUrl'], provider['apiKey'] = url, token
    c['logging']['file'] = str(out / 'openclaw.log')
    defaults = c['agents']['defaults']
    if defaults['workspace'] != ref['staging_directory']:
        raise ValueError('preflight input path differs from reference')
    defaults['sandbox']['workspaceRoot'] = str(runtime / 'sandboxes')
    defaults['sandbox']['docker']['containerPrefix'] = 'sxreal-' + agent_id + '-'
    entries = c['agents']['entries']
    if len(entries) != 1: raise ValueError('requires single preflight agent')
    c['agents']['entries'] = {agent_id: next(iter(entries.values()))}
    return c


def config_signature(cfg):
    c = copy.deepcopy(cfg)
    c['logging']['file'] = '<audit>'
    p = c['models']['providers']['vllm']
    p['baseUrl'], p['apiKey'] = '<endpoint>', '<credential>'
    s = c['agents']['defaults']['sandbox']
    s['workspaceRoot'], s['docker']['containerPrefix'] = '<scratch>', '<prefix>'
    if len(c['agents']['entries']) != 1: raise ValueError('single agent required')
    c['agents']['entries'] = {'<id>': next(iter(c['agents']['entries'].values()))}
    return canonical(c)


def model_info(ref, key):
    con = connect(ref['base_url'], 10)
    try:
        headers = {'Authorization': 'Bearer ' + key} if key else {}
        con.request('GET', '/v1/models', headers=headers)
        resp = con.getresponse(); data = resp.read(LIMIT + 1)
        if resp.status != 200 or len(data) > LIMIT:
            raise ValueError('local /v1/models failed; no inference submitted')
        rows = [r for r in load(data).get('data', []) if r.get('id') == ref['model']]
        if len(rows) != 1: raise ValueError('served model ID not found uniquely')
        return {'id': rows[0]['id'], 'max_model_len': rows[0].get('max_model_len')}
    finally: con.close()


class Recorder(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, out, ref, key, token, native, gate, max_requests):
        super().__init__(('127.0.0.1', 0), Forwarder)
        self.out, self.ref, self.key, self.token = out, ref, key, token
        self.native, self.gate, self.max_requests = native, gate, max_requests
        self.records, self.connections = [], set()
        self.lock = threading.Lock()
        self.deadline = 0.0
        self.started = 0.0
        self.stopping = False

    def cancel(self):
        with self.lock:
            self.stopping = True
            connections = list(self.connections)
        for con in connections:
            try:
                if con.sock: con.sock.shutdown(socket.SHUT_RDWR)
                con.close()
            except OSError: pass


class Forwarder(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def error_reply(self, status, message):
        body = json.dumps({'error': message}).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        s = self.server
        self.connection.settimeout(10)
        if (self.path != '/v1/chat/completions' or self.headers.get('Authorization') != 'Bearer ' + s.token):
            return self.error_reply(403, 'local recorder endpoint only')
        con = None; record = None; headers_sent = False
        try:
            n = int(self.headers.get('Content-Length', '0'))
            if self.headers.get('Transfer-Encoding') or not 0 < n <= LIMIT:
                return self.error_reply(413, 'invalid request length')
            body = self.rfile.read(n)
            if len(body) != n: raise ValueError('short request')
            payload = load(body)
            with s.lock:
                if s.stopping or time.monotonic() >= s.deadline or len(s.records) >= s.max_requests:
                    return self.error_reply(409, 'task deadline or request limit reached')
                idx = len(s.records) + 1
                record = {'index': idx, 'forwarded': False,
                          'start_s': round(time.monotonic() - s.started, 4),
                          'request_sha256': sha(body), 'wire': s.native.check_wire(payload, s.ref)}
                s.records.append(record)
            (s.out / f'wire-{idx:02d}-request.json').write_bytes(body)
            if record['wire']['problems']:
                raise ValueError('wire differs from baseline; request not forwarded')
            # Verify this run's real sandbox before the first model call.
            s.gate()
            left = s.deadline - time.monotonic()
            if left <= 0: raise TimeoutError('task deadline')
            con = connect(s.ref['base_url'], left)
            with s.lock:
                if s.stopping: raise TimeoutError('task stopped')
                s.connections.add(con)
            headers = {'Content-Type': 'application/json', 'Accept-Encoding': 'identity'}
            if s.key: headers['Authorization'] = 'Bearer ' + s.key
            print(f'[run] model request {idx}', flush=True)
            record['forwarded'] = True
            # Forward EXACT body bytes. No injected tools, answer, thinking, or schema.
            con.request('POST', '/v1/chat/completions', body=body, headers=headers)
            resp = con.getresponse()
            record['http_status'] = resp.status
            self.send_response(resp.status)
            mime = resp.getheader('Content-Type', 'application/octet-stream')
            record['content_type'] = mime
            self.send_header('Content-Type', mime)
            self.send_header('Connection', 'close'); self.end_headers()
            headers_sent = True; self.close_connection = True
            count = 0
            with (s.out / f'wire-{idx:02d}-response.bin').open('xb') as trace:
                while True:
                    if s.stopping or time.monotonic() >= s.deadline: raise TimeoutError('task deadline')
                    chunk = resp.read1(16384)
                    if not chunk: break
                    count += len(chunk)
                    if count > 8 * 1024 * 1024: raise ValueError('response record limit exceeded')
                    trace.write(chunk); self.wfile.write(chunk); self.wfile.flush()
            record['response_bytes'] = count
            record['response_complete'] = True
        except Exception as exc:
            if record is not None:
                record['error_type'] = type(exc).__name__
                record['error_message'] = str(exc)[:200]
            if not headers_sent:
                try: self.error_reply(502, 'recorder blocked/failed; inspect local audit')
                except OSError: pass
        finally:
            if record is not None:
                record['end_s'] = round(time.monotonic() - s.started, 4)
                save(s.out / f'wire-{record["index"]:02d}-meta.json', record)
            if con:
                with s.lock: s.connections.discard(con)
                con.close()



def response_metadata(path, mime):
    raw = Path(path).read_bytes()
    try:
        if 'text/event-stream' in mime:
            events = [load(line[5:].strip()) for line in raw.decode('utf-8').splitlines()
                      if line.startswith('data:') and line[5:].strip() != '[DONE]']
        else:
            events = [load(raw)]
        usage = None; finish = []
        for e in events:
            if isinstance(e.get('usage'), dict): usage = e['usage']
            for c in e.get('choices', []):
                if c.get('finish_reason') is not None: finish.append(c['finish_reason'])
        return {'usage': usage, 'finish_reasons': finish}
    except (ValueError, UnicodeError, TypeError, AttributeError):
        return {'usage': None, 'finish_reasons': [], 'parse': 'unknown_raw_record_kept'}


def cli_outcome(stdout):
    """2026.9.2: replay safety is NOT the task-completion predicate.

    Preserve replayInvalid as a do-not-auto-replay warning. Genuine errors,
    aborts, fallback, missing/erroneous payloads and pending states still block.
    HTTP completion, evidence, input hashes and SLA are checked separately.
    """
    obj = load(stdout)
    if not isinstance(obj, dict): raise ValueError('unrecognized CLI JSON envelope')
    meta = obj.get('meta')
    if meta is None: meta = {}
    if not isinstance(meta, dict): raise ValueError('CLI meta must be object')
    trace = meta.get('executionTrace')
    if trace is None: trace = {}
    if not isinstance(trace, dict): raise ValueError('CLI executionTrace must be object')
    flags = {k: meta.get(k) for k in ('error', 'aborted', 'replayInvalid', 'livenessState', 'timeoutPhase')}
    flags['fallbackUsed'] = trace.get('fallbackUsed')
    blockers = []
    for key in ('aborted', 'replayInvalid'):
        if meta.get(key) is not None and type(meta[key]) is not bool:
            blockers.append('invalid_' + key + '_type')
    if trace.get('fallbackUsed') is not None and type(trace['fallbackUsed']) is not bool:
        blockers.append('invalid_fallbackUsed_type')
    if meta.get('error') is not None: blockers.append('error')
    if meta.get('aborted'): blockers.append('aborted')
    if trace.get('fallbackUsed'): blockers.append('fallbackUsed')
    if meta.get('timeoutPhase') or meta.get('timedOut'): blockers.append('timeout')
    if meta.get('livenessState') in ('abandoned', 'blocked', 'paused'):
        blockers.append('nonterminal_or_failed_liveness')
    rows = obj.get('payloads')
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ValueError('CLI payloads array missing or malformed')
    if any(r.get('isError') for r in rows): blockers.append('error_payload')
    texts = [r['text'] for r in rows if isinstance(r.get('text'), str)
             and r['text'].strip() and not r.get('isReasoning')]
    if not texts: blockers.append('no_final_visible_answer')
    return {'flags': flags, 'blockers': blockers,
            'automatic_replay_allowed': False,
            'warnings': ['replay_unsafe_do_not_auto_retry'] if meta.get('replayInvalid') is True else [],
            'answer': texts[-1] if texts else None, 'visible_payloads': len(texts)}


def extract_answer(stdout):
    outcome = cli_outcome(stdout)
    if outcome['blockers']:
        raise ValueError('CLI completion blocked: ' + ', '.join(outcome['blockers']))
    # Last visible payload only; never search earlier outputs for a passing answer.
    return outcome['answer'], outcome['visible_payloads']


FILTER_FIRST_GUIDANCE = """执行策略补充（不改变上面的筛选条件和输出要求）：
这是有明确筛选条件的文件任务。优先考虑使用现有命令或临时脚本在文件侧筛选，
只把匹配记录及必要上下文带回模型；只有需要了解格式时才少量抽样，不默认逐页读取全文。
具体工具、命令及步骤由你选择，不提供预制答案。取证时保留来源和可核对的位置。
如需分页，只按工具明确返回的续读位置或已验证的行数继续；已到文件末尾就停止续读。
取得足够证据并核对字段后交付；只有遗漏、冲突或校验疑点时才追加检查。
不要为了减少调用而跳过必要核对，不要猜测未读取的内容，仍需严格遵守原输出格式。"""


def task_text(prompt, strategy='baseline'):
    task = prompt + '\n需要读取的文件：/agent/input.log\n'
    if strategy == 'filter-first': task += '\n' + FILTER_FIRST_GUIDANCE + '\n'
    elif strategy != 'baseline': raise ValueError('unknown strategy')
    return task


def grade(answer, expected):
    try:
        match = canonical(load(answer)) == canonical(expected)
        return {'strict_match': match, 'content_match': match, 'format_only': False}
    except (ValueError, TypeError): pass
    m = re.fullmatch(r'```(?:json)?\s*\n([\s\S]*?)\n```', answer.strip(), re.I)
    if m:
        try:
            match = canonical(load(m[1])) == canonical(expected)
            return {'strict_match': False, 'content_match': match, 'format_only': match}
        except (ValueError, TypeError): pass
    return {'strict_match': False, 'content_match': None, 'format_only': False}


def text_content(value):
    if isinstance(value, str): return value
    if isinstance(value, list) and all(isinstance(p, dict) and
            p.get('type') == 'text' and isinstance(p.get('text'), str) for p in value):
        return '\n'.join(p['text'] for p in value)
    return None


def read_coverage(messages, source):
    """Exact source-line/offset alignment, not substring search across pages.

    Newline encodings are normalized by splitlines; spaces and duplicate source
    lines are retained. Only linked native read returns count. Exec is not run,
    interpreted as Python, or accepted merely for mentioning the file path.
    """
    lines = source.decode('utf-8').splitlines()
    calls, returned, conflicts = {}, {}, []
    for message in messages:
        if not isinstance(message, dict): raise ValueError('invalid transcript message')
        if message.get('role') == 'assistant':
            for call in message.get('tool_calls') or []:
                cid, func = call.get('id'), call.get('function')
                if not isinstance(cid, str) or not isinstance(func, dict):
                    raise ValueError('invalid tool call')
                if cid in calls and calls[cid] != func: conflicts.append('changed_call_for_same_id')
                calls[cid] = func
        if message.get('role') == 'tool':
            cid = message.get('tool_call_id')
            if not isinstance(cid, str): continue
            if cid in returned and returned[cid] != message: conflicts.append('changed_result_for_same_id')
            returned[cid] = message
    covered, pages = set(), []
    for cid, func in calls.items():
        if func.get('name') != 'read': continue
        try: args = load(func.get('arguments', '{}'))
        except (ValueError, TypeError): continue
        if not isinstance(args, dict) or args.get('path', args.get('file_path')) != '/agent/input.log': continue
        offset = args.get('offset', 1)
        if type(offset) is not int or offset < 1: continue
        page = {'id': cid, 'offset': offset, 'matched_lines': 0, 'past_eof': offset > len(lines)}
        result = returned.get(cid, {})
        text = text_content(result.get('content'))
        if result.get('isError') or text is None or page['past_eof']:
            pages.append(page); continue
        actual = text.splitlines()
        count = 0
        while count < len(actual) and offset - 1 + count < len(lines):
            if actual[count] != lines[offset - 1 + count]: break
            count += 1
        tail = '\n'.join(actual[count:]).strip()
        footer = re.fullmatch(r'\[Read output capped at [^\]\n]+ for this call\. Use offset=(\d+) to continue\.\]', tail)
        # A known cap footer must agree with the exact contiguous source prefix.
        valid_tail = not tail or bool(footer and int(footer[1]) == offset + count)
        if valid_tail:
            covered.update(range(offset, offset + count)); page['matched_lines'] = count
        else:
            page['unrecognized_tail_or_alignment'] = True
        pages.append(page)
    complete = bool(lines) and len(covered) == len(lines) and not conflicts
    return {'status': 'verified_paged_read' if complete else 'review_required',
            'source_lines': len(lines), 'covered_lines': len(covered), 'pages': pages,
            'transcript_conflicts': conflicts,
            'scope': 'recorded native read evidence, not OS syscall auditing; exec still needs review'}


def evidence(out, source):
    """Verify complete native read coverage (one call or pages); exec needs review.

    This is runtime transcript evidence, not an OS syscall audit. No inference
    from tool-call count alone. Never promote an ambiguous script to success.
    """
    calls, results = {}, {}
    all_messages = []
    for file in sorted(Path(out).glob('wire-*-request.json')):
        messages = read(file).get('messages', [])
        all_messages.extend(messages)
        for m in messages:
            if m.get('role') == 'assistant':
                for c in m.get('tool_calls') or []:
                    calls[c['id']] = c.get('function', {})
            elif m.get('role') == 'tool': results[m.get('tool_call_id')] = m.get('content')
    verified = []; rows = []; counts = Counter()
    raw_lines = [line for line in source.decode('utf-8').splitlines() if line.strip()]
    for cid, func in calls.items():
        try: args = load(func.get('arguments', '{}'))
        except (ValueError, TypeError): args = {}
        name = func.get('name')
        counts[(name, canonical(args))] += 1
        value = results.get(cid)
        content = value if isinstance(value, str) else '\n'.join(
            part.get('text', '') for part in (value or []) if isinstance(part, dict)) if isinstance(value, list) else ''
        path = args.get('path', args.get('file_path')) if isinstance(args, dict) else None
        full_read = (name == 'read' and path == '/agent/input.log' and raw_lines
                     and all(line in content for line in raw_lines))
        if full_read: verified.append(cid)
        rows.append({'id': cid, 'name': name, 'arguments': args, 'has_return': cid in results,
                     'full_input_seen_in_read_result': bool(full_read)})
    save(Path(out) / 'tool-trace.json', rows)
    coverage = read_coverage(all_messages, source)
    proved = coverage['status'] == 'verified_paged_read'
    return {'status': ('verified_full_read' if proved and verified else
                       'verified_paged_read' if proved else 'review_required' if calls else 'missing'),
            'read_coverage': coverage,

            'tool_calls_seen': len(calls), 'tool_names': [r['name'] for r in rows],
            'same_argument_extra_calls': sum(n - 1 for n in counts.values()),
            'note': 'exec, partial reads and structured script output require operator evidence review'}


def run_task(cli, cfg_path, env, runtime, out, agent_id, message, seconds, server):
    task = out / 'task.txt'; task.write_text(message, encoding='utf-8')
    start = time.monotonic(); server.started = start; server.deadline = start + seconds
    code = None; stop = None
    with (out / 'agent.stdout.txt').open('xb') as stdout, (out / 'agent.stderr.txt').open('xb') as stderr:
        proc = subprocess.Popen([str(cli), 'agent', '--local', '--agent', agent_id,
            '--session-id', str(uuid.uuid4()), '--thinking', 'off', '--timeout', str(seconds),
            '--json', '--message-file', str(task)], env=env, cwd=runtime, stdout=stdout,
            stderr=stderr, stdin=subprocess.DEVNULL, start_new_session=True)
        try: code = proc.wait(timeout=seconds)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            stop = 'timeout' if isinstance(exc, subprocess.TimeoutExpired) else 'interrupted'
            server.cancel()
            os.killpg(proc.pid, signal.SIGTERM)
            try: proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL); proc.wait()
        finally: server.cancel()
    return {'returncode': code, 'stop': stop, 'wall_s': round(time.monotonic() - start, 4),
            'upstream_cancellation': 'unconfirmed_do_not_autoretry' if stop else 'not_requested'}


def main(argv=None, native=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--preflight', type=Path, required=True)
    ap.add_argument('--strategy', choices=['baseline', 'filter-first'], default='baseline',
                    help='explicit prompt-only intervention; default preserves the original task')
    ap.add_argument('--openclaw-bin', type=Path, default=Path.home() / '.openclaw/bin/openclaw')
    args = ap.parse_args(argv)
    if not sys.platform.startswith('linux') or os.geteuid() == 0:
        raise ValueError('run as ordinary Spark user, not sudo')
    if native is None: import poc02_preflight as native
    os.umask(0o077)
    pf = args.preflight.resolve(); ref, old, case, key_env = bundle(pf)
    stage = native.check_input(ref)
    cli = args.openclaw_bin.absolute()
    if not cli.is_file() or not os.access(cli, os.X_OK): raise ValueError('known CLI not executable')
    docker = shutil.which('docker')
    if not docker: raise ValueError('existing Docker required')
    if not re.fullmatch(r'[A-Z_][A-Z0-9_]*', key_env): raise ValueError('invalid key environment name')
    key = os.environ.get(key_env, '')
    if '\n' in key or '\r' in key: raise ValueError('invalid local API key')
    lock = (pf.parent / 'real-task.lock').open('a')
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: raise ValueError('another ScopeX real task is running')
    tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    out = pf.parent / ('real-a-' + tag); out.mkdir(mode=0o700)
    runtime = Path.home() / 'scopex-poc02-work' / ('real-' + tag)
    for d in ('home', 'state', 'sandboxes'): (runtime / d).mkdir(parents=True, mode=0o700)
    result = {'status': 'SETUP_FAILED', 'synthetic_response': False, 'agent_task_attempted': False,
              'stage': 'setup', 'errors': [], 'strict_correct': False, 'within_sla': False}
    server = thread = None; owned = []; gate_passed = False; env = {}; image_id = None
    try:
        metadata_env = {k: os.environ[k] for k in ('PATH', 'HOME', 'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG') if k in os.environ}
        host = metadata_env.get('DOCKER_HOST', '')
        if metadata_env.get('DOCKER_CONTEXT') or not host:
            host = load(native.run_command([docker, 'context', 'inspect', '--format', '{{json .Endpoints.docker.Host}}'], metadata_env, out, out, 'real-docker-context'))
        if not host.startswith('unix://'): raise ValueError('local Docker socket required')
        config_path = out / 'openclaw.json'
        env = native.clean_env(runtime, config_path, host)
        image_id = old['agents']['defaults']['sandbox']['docker']['image']
        native.run_command([docker, 'image', 'inspect', image_id, '--format', '{{.Id}}'], env, out, out, 'real-image-check')
        info = model_info(ref, key); result['served_model'] = info
        declared = old['models']['providers']['vllm']['models'][0]['contextWindow']
        if info['max_model_len'] is not None and info['max_model_len'] < declared:
            raise ValueError('served context shorter than preflight declaration')
        agent_id = 'sx' + uuid.uuid4().hex[:10]; token = secrets.token_urlsafe(24)
        prefix = 'sxreal-' + agent_id + '-'; result['sandbox_prefix'] = prefix
        def gate():
            nonlocal gate_passed
            if gate_passed: return
            ids = native.run_command([docker, 'ps', '-aq', '--filter', 'name=' + prefix], env, out, out, 'real-sandbox-list').split()
            if len(ids) != 1 or not re.fullmatch(r'[0-9a-f]{12,64}', ids[0]):
                raise ValueError('one native sandbox must exist before first inference')
            item = load(native.run_command([docker, 'inspect', ids[0], '--format', '{{json .}}'], env, out, out, 'real-sandbox-inspect'))
            if not item.get('Name', '').lstrip('/').startswith(prefix) or item.get('Image') != image_id:
                raise ValueError('not this run sandbox')
            owned.append(ids[0])
            boundary = native.check_container(item, stage, runtime / 'sandboxes', prefix, image_id, os.getuid())
            result['sandbox'] = boundary
            if boundary['problems']: raise ValueError('sandbox boundary mismatch')
            checks = 'set -eu; test -r /agent/input.log; test ! -e ' + shlex.quote(str(ROOT))
            checks += '; test ! -e /var/run/docker.sock; sha256sum /agent/input.log'
            actual = native.run_command([docker, 'exec', ids[0], '/bin/sh', '-c', checks], env, out, out, 'real-boundary-read')
            if actual.split()[0] != ref['input_sha256']: raise ValueError('container input hash mismatch')
            gate_passed = True
        server = Recorder(out, ref, key, token, native, gate, 6)
        cfg = real_config(old, ref, runtime, out, f'http://127.0.0.1:{server.server_port}/v1', token, agent_id)
        # Match the source-validated config, not arbitrary edits made after preflight.
        original_id = next(iter(old['agents']['entries']))
        expected_cfg = native.build_config(ref, Path(read(pf/'result.json')['runtime_directory']), pf,
            old['models']['providers']['vllm']['baseUrl'], old['models']['providers']['vllm']['apiKey'],
            image_id, original_id, os.getuid(), os.getgid())
        if config_signature(old) != config_signature(expected_cfg) or config_signature(cfg) != config_signature(old):
            raise ValueError('behavioral configuration changed since preflight')
        save(config_path, cfg)
        version = native.run_command([str(cli), '--version'], env, runtime, out, 'real-version')
        if not re.search(r'2026\.9\.2\s+\(3928bad\)', version): raise ValueError('OpenClaw version changed')
        active = native.run_command([str(cli), 'config', 'file'], env, runtime, out, 'real-config-file')
        if str(config_path) not in [l.strip() for l in active.splitlines()]: raise ValueError('config path mismatch')
        native.run_command([str(cli), 'config', 'validate'], env, runtime, out, 'real-config-validate')
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        task = task_text(case['prompt'], args.strategy)
        result.update(stage='real_agent', status='NOT_COMPLETED', agent_task_attempted=True,
                      preflight=str(pf), input_sha256=ref['input_sha256'],
                      request_limit=6, sla_s=ref['sla_s'], timeout_s=ref['timeout_s'],
                      assessment_version=2, strategy=args.strategy,
                      task_sha256=sha(task.encode('utf-8')),
                      original_prompt_sha256=ref['prompt_sha256'])
        print('[run] starting ONE real OpenClaw task; raw output stays local', flush=True)
        timing = run_task(cli, config_path, env, runtime, out, agent_id, task, ref['timeout_s'], server)
        result.update(timing)
        trace = evidence(out, (stage / 'input.log').read_bytes()); result['evidence'] = trace
        if timing['stop'] or timing['returncode'] != 0:
            result['status'] = 'NOT_COMPLETED'
        else:
            cli_text = (out / 'agent.stdout.txt').read_text(encoding='utf-8')
            outcome = cli_outcome(cli_text)
            result['runtime_flags'] = {k: v for k, v in outcome.items()
                                       if k not in ('answer', 'visible_payloads')}
            answer, payload_count = extract_answer(cli_text)
            (out / 'answer.txt').write_text(answer, encoding='utf-8')
            marks = grade(answer, case['expected']); result['grade'] = marks
            result['visible_payloads'] = payload_count
            wire_ok = bool(server.records) and all(r.get('forwarded') and r.get('http_status') == 200
                and r.get('response_complete') and not r.get('error_type') for r in server.records)
            valid = wire_ok and gate_passed
            proved = trace['status'] in ('verified_full_read', 'verified_paged_read')
            result['strict_correct'] = valid and proved and marks['strict_match']
            result['within_sla'] = result['strict_correct'] and timing['wall_s'] <= ref['sla_s']
            result['status'] = ('PASS_SINGLE_CASE' if result['within_sla'] else
                'CORRECT_OVER_SLA' if result['strict_correct'] else
                'EVIDENCE_REVIEW_REQUIRED' if valid and trace['status'] == 'review_required' else
                'FORMAT_ONLY' if valid and proved and marks['format_only'] else 'NOT_PASSED')
        native.check_input(ref)
        result['stage'] = 'complete'
    except Exception as exc:
        result['errors'].append(type(exc).__name__ + ': ' + str(exc)[:300])
    except KeyboardInterrupt:
        result['errors'].append('operator interrupted'); result['status'] = 'INTERRUPTED'
    finally:
        if server:
            server.cancel()
            if thread: server.shutdown(); thread.join(timeout=3)
            server.server_close()
            result['wire'] = list(server.records)
            result['model_request_attempts'] = sum(bool(r['forwarded']) for r in server.records)
            for row in result['wire']:
                response = out / f'wire-{row["index"]:02d}-response.bin' if 'index' in row else None
                if response and response.is_file():
                    row['response'] = response_metadata(response, row.get('content_type', ''))
        for cid in owned:
            try: native.run_command([docker, 'stop', '--time', '2', cid], env, out, out, 'stop-' + cid[:12], 10)
            except Exception: result['errors'].append('owned sandbox stop failed: ' + cid)
        if result['errors']:
            result['strict_correct'] = False; result['within_sla'] = False
            if result['status'] in ('PASS_SINGLE_CASE', 'CORRECT_OVER_SLA'): result['status'] = 'NOT_PASSED'
        result['audit_directory'], result['runtime_directory'] = str(out), str(runtime)
        save(out / 'result.json', result)
        summary = '# POC02-A 真实模型单次任务\n\n```json\n' + json.dumps(result, ensure_ascii=False, indent=2) + '\n```\n'
        summary += '\n未修复答案或改变原评分；exec/分页读取证据不明确时需人工复核。原始日志和请求留本机。\n'
        (out / 'summary.md').write_text(summary, encoding='utf-8')
        print(summary); print('报告：', out / 'summary.md')
        lock.close()
    return 0 if result['within_sla'] else 1


if __name__ == '__main__':
    try: sys.exit(main())
    except (ValueError, OSError, KeyError) as exc:
        print('RUN_SETUP_ERROR:', str(exc), file=sys.stderr); sys.exit(2)
