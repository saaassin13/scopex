#!/usr/bin/env python3
"""Prepare POC02-A inputs and inspect local runtime availability; never run an agent.

Python >= 3.10, standard library. No inference/API call, package installation,
container creation/exec, production config read/write, or scoring change.
A staged directory is NOT a security sandbox. See docs/poc/02-openclaw-runbook.md.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import urllib.parse
import uuid

ROOT = Path(__file__).resolve().parents[1]
MAX_JSON = 4 * 1024 * 1024
MAX_LOG = 256 * 1024


class PrepareError(Exception):
    pass


def loads(text):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise PrepareError('duplicate JSON key')
            out[key] = value
        return out
    def invalid(value):
        raise PrepareError('non-JSON numeric constant')
    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


def plain_path(path):
    """Reject symlinks in all existing components, not just the leaf."""
    path = Path(os.path.abspath(Path(path).expanduser()))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise PrepareError('symbolic-link paths are not accepted')
    return path


def read_bytes(path, cap):
    path = plain_path(path)
    if not path.is_file() or path.stat().st_size > cap:
        raise PrepareError(f'missing file or size limit exceeded: {path.name}')
    with path.open('rb') as handle:
        data = handle.read(cap + 1)
    if len(data) > cap:
        raise PrepareError(f'size limit exceeded: {path.name}')
    return data


def read_json(path):
    return loads(read_bytes(path, MAX_JSON).decode('utf-8'))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def validate_reference(cfg):
    """Export only non-secret, explicitly supported baseline fields."""
    if not isinstance(cfg, dict):
        raise PrepareError('baseline config must be an object')
    raw = cfg.get('base_url')
    if not isinstance(raw, str):
        raise PrepareError('baseline base_url is missing')
    url = urllib.parse.urlsplit(raw)
    host = url.hostname or ''
    try:
        loopback = host == 'localhost' or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if (not loopback or url.scheme not in ('http', 'https') or url.username or
            url.password or url.query or url.fragment or url.path.rstrip('/') != '/v1'):
        raise PrepareError('baseline URL must be loopback /v1, without credentials')
    model = cfg.get('model', '')
    if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,199}', model):
        raise PrepareError('baseline served model ID is invalid')
    request = cfg.get('request')
    if not isinstance(request, dict):
        raise PrepareError('baseline request settings are missing')
    template = request.get('chat_template_kwargs')
    if not isinstance(template, dict) or template.get('enable_thinking') is not False:
        raise PrepareError('baseline must explicitly request enable_thinking=false')
    safe = {}
    numeric = {'temperature', 'top_p', 'top_k', 'min_p', 'seed', 'max_tokens',
               'max_completion_tokens', 'presence_penalty', 'frequency_penalty'}
    for key, value in request.items():
        if key in numeric and type(value) in (int, float):
            safe[key] = value
        elif key == 'chat_template_kwargs' and all(
                k in {'enable_thinking', 'preserve_thinking'} and type(v) is bool
                for k, v in value.items()):
            safe[key] = dict(value)
        elif key == 'reasoning_effort' and value in (None, 'none', 'minimal', 'low', 'medium', 'high', 'xhigh'):
            safe[key] = value
        else:
            raise PrepareError('unsupported baseline request field; review locally before exporting')
    limits = {}
    for key in ('sla_s', 'timeout_s'):
        if type(cfg.get(key)) is not int or cfg[key] <= 0:
            raise PrepareError('baseline SLA/timeout must be positive integers')
        limits[key] = cfg[key]
    if limits['sla_s'] > limits['timeout_s']:
        raise PrepareError('baseline SLA exceeds timeout')
    return {'model': model, 'base_url': raw, 'request': safe, **limits}


def load_baseline(run, suite):
    """Reuse a recorded successful pair, and verify current source bytes."""
    run, suite = plain_path(run), plain_path(suite)
    saved = read_json(run / 'cases.json')
    current = read_json(suite / 'cases.json')
    if canonical(saved) != canonical(current):
        raise PrepareError('suite differs from saved baseline; do not silently replace tasks')
    if (not isinstance(saved, list) or len(saved) != 2 or
            not all(isinstance(c, dict) for c in saved) or
            {c.get('mode') for c in saved} != {'direct', 'tool'}):
        raise PrepareError('use the imported real-log suite containing one direct/tool pair')
    direct = next(c for c in saved if c['mode'] == 'direct')
    tool = next(c for c in saved if c['mode'] == 'tool')
    for case in saved:
        if case.get('files') != ['input.log'] or case.get('root') != 'data':
            raise PrepareError('only the imported single-file real-log pair is supported')
    if (direct.get('prompt') != tool.get('prompt') or
            canonical(direct.get('expected')) != canonical(tool.get('expected')) or
            not isinstance(tool.get('prompt'), str) or not tool['prompt'].strip() or
            'expected' not in direct or 'expected' not in tool):
        raise PrepareError('paired requirements and independent expected values must match')
    cfg_bytes = read_bytes(run / 'config.json', MAX_JSON)
    reference = validate_reference(loads(cfg_bytes.decode('utf-8')))
    results = read_json(run / 'results.json')
    plan = read_json(run / 'plan.json')
    if (not isinstance(results, list) or not results or
            not all(isinstance(r, dict) for r in results) or
            not isinstance(plan, dict) or plan.get('warmup') is not False or
            plan.get('attempts') != [r.get('case') for r in results]):
        raise PrepareError('baseline must be a complete non-warmup run')
    ids = {c['id'] for c in saved}
    if {r.get('case') for r in results} != ids:
        raise PrepareError('baseline must include both direct and tool results')
    data = read_bytes(suite / 'data/input.log', MAX_LOG)
    data.decode('utf-8')
    if b'\0' in data:
        raise PrepareError('input must be UTF-8 text without NUL')
    for result in results:
        if (result.get('status') != 'correct' or result.get('correct') is not True or
                result.get('within_sla') is not True):
            raise PrepareError('selected reference run is not entirely strict/SLA successful')
        if result.get('input_sha256', {}).get('input.log') != sha(data):
            raise PrepareError('log bytes do not match the recorded baseline SHA256')
    reference.update({
        'baseline_config_sha256': sha(cfg_bytes),
        'input_sha256': sha(data), 'input_bytes': len(data),
        'prompt_sha256': sha(tool['prompt'].encode('utf-8')),
        'expected_sha256': sha(canonical(tool['expected']).encode('utf-8')),
        'baseline_attempts': len(results),
    })
    return reference, data, tool['prompt']


def clean_help_env(home):
    home = str(home)
    # In particular do not inherit API keys, proxy settings, production profile,
    # config/state paths, NODE_OPTIONS, NODE_PATH, or provider credentials.
    return {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': home,
            'LANG': 'C.UTF-8', 'NO_COLOR': '1',
            'XDG_CONFIG_HOME': home + '/config', 'XDG_CACHE_HOME': home + '/cache',
            'OPENCLAW_STATE_DIR': home + '/state',
            'OPENCLAW_CONFIG_PATH': home + '/state/openclaw.json',
            'OPENCLAW_WORKSPACE_DIR': home + '/workspace'}


def capture(argv, env, cwd, seconds=12):
    """No shell. Bound time; output is read with a cap and is never printed raw."""
    try:
        with tempfile.TemporaryFile() as handle:
            proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=handle,
                                    stderr=subprocess.STDOUT, start_new_session=True)
            timed_out = False
            try:
                proc.wait(timeout=seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            except BaseException:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                raise
            handle.seek(0)
            raw = handle.read(65537)
        return {'returncode': proc.returncode, 'timeout': timed_out,
                'truncated': len(raw) > 65536,
                'text': raw[:65536].decode('utf-8', errors='replace')}
    except OSError as exc:
        return {'returncode': None, 'timeout': False, 'truncated': False,
                'text': '', 'error_type': type(exc).__name__}


def inventory(folder, cli_path=None):
    home = folder / 'help-home'
    home.mkdir(mode=0o700)
    env = clean_help_env(home)
    found = str(Path(cli_path).expanduser().absolute()) if cli_path else shutil.which('openclaw')
    info = {'machine': platform.machine(), 'python': platform.python_version(),
            'host_cli_found': bool(found), 'host_cli': found, 'help': {},
            'docker': {'status': 'not_found'}, 'config_loaded': False}
    if found:
        for name, args in [('version', ['--version']), ('root', ['--help']),
                           ('agent', ['agent', '--help']), ('config', ['config', '--help']),
                           ('sandbox', ['sandbox', '--help'])]:
            print('[prepare] inspect host OpenClaw ' + name, flush=True)
            info['help'][name] = capture([found, *args], env, home)
    docker = shutil.which('docker')
    if docker:
        # Docker context lookup reads local CLI configuration only. Refuse remote
        # daemons: a remote Docker host would not prove isolation on this Spark.
        denv = {k: os.environ[k] for k in ('PATH', 'HOME', 'DOCKER_HOST', 'DOCKER_CONTEXT',
                                          'DOCKER_CONFIG', 'XDG_RUNTIME_DIR') if k in os.environ}
        host = os.environ.get('DOCKER_HOST', '')
        context = capture([docker, 'context', 'inspect', '--format',
                           '{{json .Endpoints.docker.Host}}'], denv, folder)
        # DOCKER_CONTEXT overrides DOCKER_HOST when both are supplied.
        if os.environ.get('DOCKER_CONTEXT') or not host:
            try:
                host = loads(context['text'].strip()) if context['returncode'] == 0 else ''
            except (ValueError, PrepareError):
                host = ''
        if not isinstance(host, str) or not host.startswith('unix://'):
            info['docker'] = {'status': 'remote_or_unknown_context_not_queried'}
        else:
            print('[prepare] inspect local Docker container/image names', flush=True)
            formats = {
                'containers': ['ps', '-a', '--no-trunc', '--format',
                    '{"id":"{{.ID}}","name":"{{.Names}}","image":"{{.Image}}","state":"{{.State}}"}'],
                'images': ['image', 'ls', '--no-trunc', '--format',
                    '{"id":"{{.ID}}","repository":"{{.Repository}}","tag":"{{.Tag}}"}'],
            }
            dinfo = {'status': 'local_context', 'containers': [], 'images': []}
            for kind, args in formats.items():
                result = capture([docker, *args], denv, folder)
                dinfo[kind + '_ok'] = result['returncode'] == 0 and not result['truncated']
                if dinfo[kind + '_ok']:
                    try:
                        rows = [loads(line) for line in result['text'].splitlines() if line.strip()]
                        dinfo[kind] = [r for r in rows if any(
                            word in canonical(r).lower() for word in ('openclaw', 'vllm'))][:20]
                    except (ValueError, PrepareError):
                        dinfo[kind + '_ok'] = False
            info['docker'] = dinfo
    return info


def render_summary(reference, info, stage):
    version = info['help'].get('version', {})
    text = version.get('text', '') if version.get('returncode') == 0 else ''
    match = re.search(r'(?<![\d.])v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.\-]+)?', text)
    lines = ['# POC02-A 准备检查', '', '**PREPARED_NOT_RUN：没有运行 Agent 或请求模型。**', '',
             f"- 平台：{info['machine']}；Python：{info['python']}",
             f"- 主机 OpenClaw CLI：{'找到' if info['host_cli_found'] else '未在 PATH 找到，不代表未安装'}",
             f"- CLI 版本：{match.group(0) if match else 'UNKNOWN'}",
             f"- Docker 检查：{info['docker']['status']}",
             f"- 基线模型 ID：{reference['model']}",
             f"- 基线请求 enable_thinking：{reference['request']['chat_template_kwargs']['enable_thinking']}",
             f"- 基线本批尝试：{reference['baseline_attempts']}；输入字节：{reference['input_bytes']}",
             '- 输入与基线 SHA256：相符；复制后 SHA256：相符',
             '- 待挂载目录：仅 input.log；不含 expected、历史答案、Skill 或解析脚本', '',
             '| 本机帮助项 | 返回码 | 超时 | 检测到的选项（仅帮助文字） |',
             '|---|---:|---|---|']
    options = {'root': ['--profile'], 'agent': ['--local', '--agent', '--session-id', '--thinking', '--json', '--timeout'],
               'config': ['validate', 'get'], 'sandbox': ['explain', 'list']}
    for name, wanted in options.items():
        result = info['help'].get(name, {})
        present = [flag for flag in wanted if flag in result.get('text', '')]
        lines.append(f"| {name} | {result.get('returncode', 'UNKNOWN')} | {result.get('timeout', 'UNKNOWN')} | {', '.join(present) or 'UNKNOWN'} |")
    lines += ['', '## 相关 Docker 项（只按名称筛选，可能漏掉自定义名称）']
    for kind in ('containers', 'images'):
        rows = info['docker'].get(kind, [])
        lines.append(f"{kind} 查询完整：{info['docker'].get(kind + '_ok', 'UNKNOWN')}；候选数：{len(rows)}")
        for row in rows:
            # Names/images only, never command arguments, environments or mounts.
            safe = {k: v for k, v in row.items() if k != 'id'}
            lines.append('    ' + json.dumps(safe, ensure_ascii=False))
    lines += ['', '## 尚未通过的启动门',
              '1. 当前安装版本的独立配置与入口尚未验证；没有读取或修改原 OpenClaw 配置。',
              '2. 文件访问硬隔离尚未验证：本次只是分离材料，chmod 和 workspace 不是沙箱。',
              '3. OpenClaw 实际出站 model / enable_thinking / 采样参数尚未核对。',
              '4. 原 POC01 格式问题与部分重复次数欠账保留，不宣告整体通过。', '',
              '先提供本 summary.md；不要把 inventory.json、reference.json、task.txt、生产日志或原始配置公开上传。',
              '不要直接在默认 OpenClaw 会话执行 task.txt；下一步先完成隔离和实际请求检查。', '']
    return '\n'.join(lines)


def prepare(run, suite, out, stage, cli_path=None, inventory_fn=inventory):
    run, suite, out, stage = map(plain_path, (run, suite, out, stage))
    if out.exists() or stage.exists():
        raise PrepareError('output/staging directory exists; no overwrite')
    # The future mounted directory must not include the repo, suite or records.
    for private in (ROOT.resolve(), run, suite, out):
        if stage.is_relative_to(private) or private.is_relative_to(stage):
            raise PrepareError('staging directory must be disjoint from repository and grading data')
    reference, data, prompt = load_baseline(run, suite)
    out.mkdir(parents=True, mode=0o700)
    stage.mkdir(parents=True, mode=0o700)
    with (stage / 'input.log').open('xb') as handle:
        handle.write(data)
    (stage / 'input.log').chmod(0o444)
    if sha(read_bytes(stage / 'input.log', MAX_LOG)) != reference['input_sha256']:
        raise PrepareError('copied file hash mismatch')
    # No task/answer/material manifest is placed inside the future mount.
    (out / 'task.txt').write_text(prompt + '\n需要读取的文件：/workspace/input.log\n', encoding='utf-8')
    reference.update({'baseline_run': str(run), 'suite': str(suite),
                      'staging_directory': str(stage), 'isolation_verified': False,
                      'wire_request_verified': False, 'agent_run_started': False})
    write_json(out / 'reference.json', reference)
    info = inventory_fn(out, cli_path)
    write_json(out / 'inventory.json', info)
    summary = render_summary(reference, info, stage)
    (out / 'summary.md').write_text(summary, encoding='utf-8')
    return summary


def main(argv=None):
    if sys.version_info < (3, 10) or os.name != 'posix':
        raise PrepareError('requires Python >= 3.10 on Spark Linux')
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-run', required=True, type=Path)
    parser.add_argument('--suite', type=Path, default=ROOT / '.local/poc01/real-cowlog-window')
    parser.add_argument('--openclaw-bin', type=Path, help='optional actual host CLI executable; not a shell command')
    args = parser.parse_args(argv)
    if os.geteuid() == 0:
        raise PrepareError('run as the ordinary Spark user, not root/sudo')
    key = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    out = ROOT / '.local/poc02' / ('prepare-' + key)
    stage = Path.home() / 'scopex-poc02-work' / ('input-' + key)
    result = prepare(args.baseline_run, args.suite, out, stage, args.openclaw_bin)
    print(result)
    print('准备报告：', out / 'summary.md')
    print('输入暂存：', stage)
    return 0  # preparation only; not a model score or isolation approval


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('准备已中断；已生成材料保留，不自动删除。', file=sys.stderr)
        sys.exit(130)
    except (PrepareError, OSError, ValueError, KeyError, TypeError) as exc:
        print('PREPARE_ERROR:', str(exc), file=sys.stderr)
        sys.exit(2)
