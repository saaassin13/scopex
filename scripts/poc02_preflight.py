#!/usr/bin/env python3
"""OpenClaw 2026.9.2 native preflight: private config, real wire capture, sandbox.
No vLLM call: the loopback receiver scripts one harmless native exec and a labelled synthetic answer.
This is setup verification, NEVER a model/Agent success score. Stdlib, Python 3.10+.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ['read', 'exec', 'process']
SYNTHETIC = 'SCOPEX_WIRE_CAPTURE_ONLY_NOT_A_MODEL_RESULT'
MARKER = 'SCOPEX_SANDBOX_WIRE_CHECK'
TOOL_ID = 'call_scopex_preflight'

class CheckError(Exception):
    pass

def loads(raw):
    def pairs(items):
        d = {}
        for k, v in items:
            if k in d:
                raise CheckError('duplicate JSON key')
            d[k] = v
        return d
    def bad(v):
        raise CheckError('non-JSON numeric constant')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad)

def read_json(path):
    path = Path(path)
    if path.stat().st_size > 4 * 1024 * 1024:
        raise CheckError('JSON file exceeds 4 MiB')
    return loads(path.read_text(encoding='utf-8'))

def dump(path, data):
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def check_input(ref):
    stage = Path(ref['staging_directory'])
    if not stage.is_absolute() or stage.is_symlink() or not stage.is_dir():
        raise CheckError('invalid prepared input directory')
    paths = list(stage.iterdir())
    if len(paths) != 1 or paths[0].name != 'input.log' or paths[0].is_symlink():
        raise CheckError('prepared directory must contain ONLY regular input.log')
    if not paths[0].is_file() or paths[0].stat().st_size > 262144:
        raise CheckError('input missing or exceeds original probe limit')
    if digest(paths[0]) != ref['input_sha256']:
        raise CheckError('prepared input hash changed')
    return stage.resolve()

def build_config(ref, runtime, out, endpoint, token, image_id, agent_id, uid, gid):
    request = ref.get('request', {})
    if request.get('chat_template_kwargs', {}).get('enable_thinking') is not False:
        raise CheckError('reference must explicitly disable thinking')
    if set(request) - {'temperature', 'top_p', 'top_k', 'min_p', 'seed', 'max_tokens',
                       'max_completion_tokens', 'presence_penalty', 'frequency_penalty',
                       'chat_template_kwargs', 'reasoning_effort'}:
        raise CheckError('unsupported reference request field')
    fields = [k for k in ('max_tokens', 'max_completion_tokens') if k in request]
    if len(fields) != 1 or type(request[fields[0]]) is not int or request[fields[0]] <= 0:
        raise CheckError('one positive integer output-token limit is required')
    model = ref['model']
    if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9._:/@+\-]{1,200}', model):
        raise CheckError('invalid served model ID')
    model_ref = 'vllm/' + model
    # v2026.9.2 / 3928bad uses agents.entries, NOT older agents.list examples.
    return {
        'logging': {'file': str(out / 'openclaw.log'), 'level': 'info'},
        'update': {'checkOnStart': False},
        'models': {'mode': 'replace', 'providers': {'vllm': {
            'baseUrl': endpoint, 'apiKey': token, 'api': 'openai-completions',
            'models': [{'id': model, 'name': 'ScopeX existing local model',
                'reasoning': True, 'input': ['text', 'image'],
                'contextWindow': 32768, 'maxTokens': request[fields[0]],
                'cost': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0},
                'compat': {'thinkingFormat': 'qwen-chat-template', 'maxTokensField': fields[0]}}]}}},
        'agents': {'defaults': {
            'model': {'primary': model_ref, 'fallbacks': []},
            'models': {model_ref: {'params': {'extra_body': request}, 'codeMode': False}},
            'workspace': ref['staging_directory'], 'skipBootstrap': True, 'skills': [],
            'startupContext': {'enabled': False}, 'contextInjection': 'never',
            'embeddedAgent': {'projectSettingsPolicy': 'ignore'},
            'thinkingDefault': 'off', 'timeoutSeconds': ref['timeout_s'],
            'compaction': {'enabled': False, 'memoryFlush': {'enabled': False}},
            'sandbox': {'mode': 'all', 'scope': 'session', 'workspaceAccess': 'ro',
                'workspaceRoot': str(runtime / 'sandboxes'),
                'docker': {'image': image_id, 'containerPrefix': 'sxpf-' + agent_id + '-',
                    'workdir': '/workspace', 'readOnlyRoot': True,
                    'tmpfs': ['/tmp', '/var/tmp', '/run'], 'network': 'none',
                    'user': f'{uid}:{gid}', 'capDrop': ['ALL'], 'pidsLimit': 256,
                    'memory': '512m', 'memorySwap': '512m', 'cpus': 1},
                'browser': {'enabled': False}}},
            'entries': {agent_id: {'default': True, 'skills': [],
                                   'memory': {'search': {'enabled': False}}}}},
        'tools': {'allow': TOOLS, 'elevated': {'enabled': False},
            'exec': {'host': 'sandbox', 'mode': 'full', 'timeoutSeconds': 30},
            'sandbox': {'tools': {'allow': TOOLS}},
            'toolSearch': False, 'codeMode': {'enabled': False}},
        'plugins': {'allow': ['vllm'], 'entries': {'vllm': {'enabled': True}},
                    'slots': {'memory': 'none'}},
    }

def check_wire(payload, ref):
    problems = []
    if payload.get('model') != ref['model']:
        problems.append('model mismatch')
    for key, value in ref['request'].items():
        # Absent preserve_thinking etc. are reported below, not silently dropped.
        if key == 'chat_template_kwargs':
            if not isinstance(payload.get(key), dict):
                problems.append('missing chat_template_kwargs')
            elif any(type(payload[key].get(k)) is not type(v) or payload[key].get(k) != v
                     for k, v in value.items()):
                problems.append('chat_template_kwargs mismatch')
        elif payload.get(key) != value or (value is None and key not in payload):
            problems.append(key + ' mismatch')
    if all(k in payload for k in ('max_tokens', 'max_completion_tokens')):
        problems.append('conflicting output-token limits')
    names = [x.get('function', {}).get('name') for x in payload.get('tools', [])]
    if set(names) != set(TOOLS) or len(names) != len(TOOLS):
        problems.append('unexpected or missing tool surface')
    if payload.get('tool_choice') not in (None, 'auto'):
        problems.append('tool_choice is forced')
    return {'model': payload.get('model'), 'stream': payload.get('stream'),
            'temperature': payload.get('temperature'), 'max_tokens': payload.get('max_tokens'),
            'max_completion_tokens': payload.get('max_completion_tokens'),
            'chat_template_kwargs': payload.get('chat_template_kwargs'),
            'tool_names': names, 'tool_choice': payload.get('tool_choice', 'omitted'),
            'message_count': len(payload.get('messages', [])),
            'message_chars': len(json.dumps(payload.get('messages', []), ensure_ascii=False)),
            'problems': problems}

class Receiver(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    def __init__(self, out, ref, token):
        super().__init__(('127.0.0.1', 0), Handler)
        self.out, self.ref, self.token = out, ref, token
        self.records = []
        self.lock = threading.Lock()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def send(self, value, status=200, mime='application/json'):
        data = value.encode() if isinstance(value, str) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_POST(self):
        self.connection.settimeout(10)
        if self.path != '/v1/chat/completions' or self.headers.get('Authorization') != 'Bearer ' + self.server.token:
            return self.send({'error': 'preflight endpoint only'}, 403)
        try:
            n = int(self.headers.get('Content-Length', '0'))
            if not 0 < n <= 2 * 1024 * 1024:
                return self.send({'error': 'invalid body size'}, 413)
            body = self.rfile.read(n)
            if len(body) != n:
                raise CheckError('short body')
            payload = loads(body)
            if not isinstance(payload, dict):
                raise CheckError('body must be object')
            with self.server.lock:
                i = len(self.server.records) + 1
                if i > 3:
                    return self.send({'error': 'preflight request limit'}, 409)
                detail = check_wire(payload, self.server.ref)
                self.server.records.append(detail)
                # No headers/auth persisted. Full body stays in private audit dir.
                dump(self.server.out / f'wire-{i:02d}-request.json', payload)
            if detail['problems']:
                return self.send({'error': 'preflight parameter mismatch; inspect local record'}, 422)
            if i == 1:
                reply = {'role': 'assistant', 'content': None, 'tool_calls': [
                    {'id': TOOL_ID, 'type': 'function', 'function': {'name': 'exec',
                     'arguments': json.dumps({'command': "printf 'SCOPEX_SANDBOX_WIRE_CHECK\\n'"})}}]}
                finish = 'tool_calls'
            else:
                returned = [m for m in payload.get('messages', [])
                            if m.get('role') == 'tool' and m.get('tool_call_id') == TOOL_ID]
                if not any(MARKER in json.dumps(m.get('content', ''), ensure_ascii=False) for m in returned):
                    return self.send({'error': 'native exec did not return the marker'}, 422)
                reply = {'role': 'assistant', 'content': SYNTHETIC}
                finish = 'stop'
            if payload.get('stream'):
                common = {'id': 'scopex-preflight', 'object': 'chat.completion.chunk',
                          'created': 0, 'model': payload.get('model')}
                delta = dict(reply)
                if 'tool_calls' in delta:
                    delta['tool_calls'] = [dict(reply['tool_calls'][0], index=0)]
                chunks = [{**common, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                          {**common, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]}]
                response = ''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n'
                return self.send(response, mime='text/event-stream')
            return self.send({'id': 'scopex-preflight', 'object': 'chat.completion',
                'created': 0, 'model': payload.get('model'), 'choices': [{'index': 0,
                'message': reply, 'finish_reason': finish}]})
        except (ValueError, KeyError, TypeError, OSError, CheckError):
            return self.send({'error': 'invalid preflight request'}, 400)

def run_command(argv, env, cwd, out, label, seconds=25):
    print('[preflight] ' + label, flush=True)
    with (out / (label + '.stdout.txt')).open('xb') as stdout, (out / (label + '.stderr.txt')).open('xb') as stderr:
        proc = subprocess.Popen(argv, env=env, cwd=cwd, stdout=stdout, stderr=stderr,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            code = proc.wait(timeout=seconds)
        except BaseException:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            raise
    if code != 0:
        raise CheckError(label + f' failed ({code}); see private {label}.stderr.txt')
    path = out / (label + '.stdout.txt')
    if path.stat().st_size > 2 * 1024 * 1024:
        raise CheckError(label + ' output too large')
    return path.read_text(encoding='utf-8', errors='replace')

def clean_env(runtime, config, docker_host):
    home = runtime / 'home'
    return {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(home),
            'LANG': 'C.UTF-8', 'NO_COLOR': '1', 'DOCKER_HOST': docker_host,
            'OPENCLAW_HOME': str(home), 'OPENCLAW_STATE_DIR': str(runtime / 'state'),
            'OPENCLAW_CONFIG_PATH': str(config), 'OPENCLAW_LOAD_SHELL_ENV': '0',
            'XDG_CONFIG_HOME': str(home / '.config'), 'XDG_CACHE_HOME': str(home / '.cache')}

def check_container(info, stage, scratch, prefix, image, uid):
    host = info.get('HostConfig', {})
    errors = []
    if not info.get('Name', '').lstrip('/').startswith(prefix):
        errors.append('not this test container')
    if info.get('Image') != image or host.get('NetworkMode') != 'none':
        errors.append('image or network mismatch')
    if host.get('Privileged') or not host.get('ReadonlyRootfs'):
        errors.append('root/privilege boundary mismatch')
    if 'ALL' not in [s.upper() for s in host.get('CapDrop', [])]:
        errors.append('capabilities not dropped')
    opts = host.get('SecurityOpt') or []
    if not any(s.startswith('no-new-privileges') for s in opts) or any('unconfined' in s for s in opts):
        errors.append('security options mismatch')
    if host.get('PidMode') == 'host' or host.get('IpcMode') == 'host' or host.get('Devices'):
        errors.append('host namespace/device exposed')
    if info.get('Config', {}).get('User', '').split(':')[0] != str(uid) or uid == 0:
        errors.append('sandbox user mismatch')
    found = set()
    mounts = []
    for m in info.get('Mounts', []):
        src, dst = Path(m.get('Source', '/')), m.get('Destination')
        mounts.append({k: m.get(k) for k in ('Type', 'Source', 'Destination', 'RW')})
        if m.get('Type') == 'tmpfs' and dst in ('/tmp', '/var/tmp', '/run'):
            continue
        valid = m.get('Type') == 'bind' and (
            (dst == '/agent' and src == stage and m.get('RW') is False) or
            (dst == '/workspace' and src.is_relative_to(scratch) and src != scratch))
        if not valid:
            errors.append('unexpected bind or volume')
        found.add(dst)
    if not {'/agent', '/workspace'} <= found:
        errors.append('required mounts absent')
    return {'container': info.get('Name'), 'mounts': mounts, 'problems': errors}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--openclaw-bin', type=Path, default=Path.home() / '.openclaw/bin/openclaw')
    args = parser.parse_args(argv)
    if not sys.platform.startswith('linux') or os.geteuid() == 0:
        raise CheckError('use normal user on Spark Linux, not sudo')
    os.umask(0o077)
    prepared = args.prepared.resolve()
    ref = read_json(prepared / 'reference.json')
    stage = check_input(ref)
    cli = args.openclaw_bin.absolute()
    if not cli.is_file() or not os.access(cli, os.X_OK):
        raise CheckError('known CLI not executable')
    docker = shutil.which('docker')
    if not docker:
        raise CheckError('existing Docker CLI required; no install performed')
    key = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    out = prepared / ('native-preflight-' + key)
    runtime = Path.home() / 'scopex-poc02-work' / ('native-' + key)
    out.mkdir(mode=0o700)
    for p in (runtime / 'home', runtime / 'state', runtime / 'sandboxes'):
        p.mkdir(parents=True, mode=0o700)
    result = {'status': 'PREFLIGHT_FAILED', 'synthetic_response': True, 'model_inference_calls': 0,
              'agent_task_executed': False, 'stage': 'docker_context', 'errors': []}
    server = thread = None
    containers = []
    prefix = ''
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG', 'XDG_RUNTIME_DIR') if k in os.environ}
    try:
        host = env.get('DOCKER_HOST', '')
        if env.get('DOCKER_CONTEXT') or not host:
            raw = run_command([docker, 'context', 'inspect', '--format', '{{json .Endpoints.docker.Host}}'], env, out, out, 'docker-context')
            host = loads(raw.strip())
        if not isinstance(host, str) or not host.startswith('unix://'):
            raise CheckError('only existing local Unix-socket Docker daemon allowed')
        config_path = out / 'openclaw.json'
        env = clean_env(runtime, config_path, host)
        image = loads(run_command([docker, 'image', 'inspect', 'openclaw-sandbox:bookworm-slim', '--format', '{{json .}}'], env, out, out, 'image-inspect'))
        if image.get('Architecture') != 'arm64' or image.get('Os') != 'linux' or image.get('Config', {}).get('Volumes'):
            raise CheckError('need existing Linux arm64 sandbox image without implicit volumes')
        token = secrets.token_urlsafe(24)
        server = Receiver(out, ref, token)
        agent_id = 'sx' + uuid.uuid4().hex[:10]
        prefix = 'sxpf-' + agent_id + '-'
        result['sandbox_prefix'] = prefix
        cfg = build_config(ref, runtime, out, f'http://127.0.0.1:{server.server_port}/v1', token,
                           image['Id'], agent_id, os.getuid(), os.getgid())
        dump(config_path, cfg)
        result['stage'] = 'config'
        version = run_command([str(cli), '--version'], env, runtime, out, 'version')
        if not re.search(r'2026\.9\.2\s+\(3928bad\)', version):
            raise CheckError('installed version changed; this profile targets 2026.9.2 (3928bad)')
        active = run_command([str(cli), 'config', 'file'], env, runtime, out, 'config-file')
        if str(config_path) not in [line.strip() for line in active.splitlines()]:
            raise CheckError('CLI did not confirm independent config path')
        run_command([str(cli), 'config', 'validate'], env, runtime, out, 'config-validate')
        result['stage'] = 'wire_capture'
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        run_command([str(cli), 'agent', '--local', '--agent', agent_id, '--session-id', str(uuid.uuid4()),
            '--thinking', 'off', '--timeout', '45', '--json', '--message',
            '这是运行时接线自检；只需打印固定测试标记后结束，不读取业务日志。'], env, runtime, out, 'wire-turn', 60)
        result['wire'] = list(server.records)
        if len(server.records) != 2 or any(r['problems'] for r in server.records):
            raise CheckError('wire fields/request count differ from reference; inspect wire summary')
        result['stage'] = 'sandbox'
        ids = run_command([docker, 'ps', '-aq', '--filter', 'name=' + prefix], env, out, out, 'sandbox-list').split()
        if len(ids) != 1 or not re.fullmatch(r'[0-9a-f]{12,64}', ids[0]):
            raise CheckError('expected exactly one fresh native sandbox')
        info = loads(run_command([docker, 'inspect', ids[0], '--format', '{{json .}}'], env, out, out, 'sandbox-inspect'))
        if not info.get('Name', '').lstrip('/').startswith(prefix):
            raise CheckError('refuse to operate on a container outside this test')
        containers.append(ids[0])
        boundary = check_container(info, stage, runtime / 'sandboxes', prefix, image['Id'], os.getuid())
        result['sandbox'] = boundary
        if boundary['problems']:
            raise CheckError('sandbox mount/privilege checks failed')
        canary = out / 'operator-only-canary'
        canary.write_text('TEST_ONLY_NOT_A_SECRET', encoding='utf-8')
        tests = 'set -eu; test -r /agent/input.log; test ! -e ' + shlex.quote(str(canary))
        tests += '; test ! -e ' + shlex.quote(str(ROOT)) + '; test ! -e /var/run/docker.sock; sha256sum /agent/input.log'
        sha_out = run_command([docker, 'exec', ids[0], '/bin/sh', '-c', tests], env, out, out, 'sandbox-boundary')
        if sha_out.split()[0] != ref['input_sha256']:
            raise CheckError('mounted input does not match reference')
        check_input(ref)
        result.update(status='PREFLIGHT_PASS_NOT_MODEL_EVAL', stage='complete',
                      native_exec_marker_returned=True)
    except (CheckError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        result['errors'].append(str(exc))
    except KeyboardInterrupt:
        result['errors'].append('operator interrupted preflight')
    finally:
        if server:
            if thread:
                server.shutdown()
                thread.join(timeout=3)
            result['wire'] = list(server.records)
            server.server_close()
        # Stop ONLY a confirmed fresh test container; never prune/remove prior ones.
        for cid in containers:
            try:
                run_command([docker, 'stop', '--time', '2', cid], env, out, out, 'stop-' + cid[:12], 10)
            except Exception:
                result['errors'].append('new sandbox cleanup failed: ' + cid)
                result['status'] = 'PREFLIGHT_FAILED'
        result['config_file'] = str(out / 'openclaw.json')
        result['runtime_directory'] = str(runtime)
        dump(out / 'result.json', result)
        lines = ['# POC02-A 原生运行时预检', '', '**' + result['status'] + '**',
                 '', '本地模拟响应；vLLM 推理调用 0；日志任务未执行。',
                 '原 Gateway / 原沙箱 / 模型服务未修改。独立临时沙箱可能已创建，已确认的新容器会停止但不删除。',
                 '', '```json', json.dumps(result, ensure_ascii=False, indent=2), '```',
                 '', '不要把此结果计入 Agent 正确率。原始 wire/CLI 日志仅留本机。']
        (out / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        print('\n'.join(lines))
        print('预检报告：', out / 'summary.md')
    return 0 if result['status'] == 'PREFLIGHT_PASS_NOT_MODEL_EVAL' else 1

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (CheckError, OSError, ValueError, KeyError) as exc:
        print('PREFLIGHT_ERROR:', str(exc), file=sys.stderr)
        sys.exit(2)
