#!/usr/bin/env python3
"""Read-only Linux OpenClaw entry-point lookup. Python >=3.10, stdlib only.
No model/HTTP calls; no OpenClaw, npm, npx, shell, or package execution.
Does not load OpenClaw configs, .env, history, or /proc/*/environ.
Prints metadata only. An entry candidate is NOT proof of a running Gateway.
"""
from __future__ import annotations
import argparse
import glob
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys


def text(path, limit=65536):
    try:
        with Path(path).open('rb') as f:
            return f.read(limit).decode('utf-8', errors='replace')
    except OSError:
        return ''


def link(path):
    try:
        return os.readlink(path)
    except OSError:
        return None


def command(argv):
    # Execute only caller-owned fixed metadata commands, never discovered entries.
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'XDG_RUNTIME_DIR',
            'DBUS_SESSION_BUS_ADDRESS') if k in os.environ}
    env.update(LC_ALL='C', SYSTEMD_PAGER='cat', SYSTEMD_COLORS='0')
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=5,
                           env=env, stdin=subprocess.DEVNULL, check=False)
        if p.returncode:
            return 'unavailable', ''  # Never print raw stderr/config-related diagnostics.
        if len(p.stdout) > 262144:
            return 'truncated', ''
        return 'ok', p.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 'unavailable', ''


def program_paths(args, cwd=None):
    """Only the initial executable and a consecutive Node script, not flag values."""
    if not args:
        return []
    names = {0}
    if Path(args[0]).name in ('node', 'nodejs', 'bun') and len(args) > 1:
        if not args[1].startswith('-') and args[1].endswith(('.js', '.mjs', '.cjs')):
            names.add(1)
    paths = []
    for index in sorted(names):
        value = args[index]
        if len(value) > 4096 or any(c in value for c in ('\n', '\r', '\0', '=', '://', '$', '`', '%')):
            continue
        p = Path(value)
        if not p.is_absolute():
            if cwd and Path(cwd).is_absolute() and ('/' in value or index == 1):
                p = Path(cwd) / p
            else:
                continue
        paths.append(str(p))
    return paths


def wrapped_program_paths(args, cwd=None, depth=0):
    """Best-effort metadata parsing of simple sg/sh/env launchers; NEVER execute.

    systemctl show is display text and can flatten quoting. Its output is only
    a path candidate, not a reconstructed launch command. Refuse complex shell
    programs, variable expansion, and unknown option syntax.
    """
    paths = program_paths(args, cwd)
    if not args or depth >= 6:
        return paths
    head = Path(args[0]).name
    tail = args[1:]
    body = None
    if head == 'sg':
        if tail and tail[0] == '-':
            tail = tail[1:]
        if not tail or not re.fullmatch(r'[A-Za-z0-9_.-]+', tail[0]):
            return paths
        tail = tail[1:]  # Group name, not a program or an arbitrary flag value.
        if tail and tail[0] in ('-c', '--command'):
            tail = tail[1:]
        if tail:
            body = tail[0] if len(tail) == 1 else ' '.join(tail)
    elif head in ('sh', 'bash', 'dash', 'zsh'):
        if len(tail) >= 2 and re.fullmatch(r'-[lc]+', tail[0]) and 'c' in tail[0]:
            body = tail[1] if len(tail) == 2 else ' '.join(tail[1:])
    elif head in ('env', 'exec'):
        if head == 'env':
            if tail and tail[0] in ('-i', '--ignore-environment'):
                tail = tail[1:]
            while tail and re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', tail[0]):
                tail = tail[1:]  # Never output assignment values.
        if tail and tail[0] == '--':
            tail = tail[1:]
        if tail and not tail[0].startswith('-'):
            paths += wrapped_program_paths(tail, cwd, depth + 1)
    elif head in ('node', 'nodejs', 'bun'):
        # Known no-argument Node switches only; no --eval or --require parsing.
        while tail and (tail[0] in ('--no-warnings', '--no-deprecation') or
                        tail[0].startswith('--disable-warning=')):
            tail = tail[1:]
        if tail and tail[0] == '--':
            tail = tail[1:]
        if tail and tail[0].endswith(('.js', '.mjs', '.cjs')) and not tail[0].startswith('-'):
            paths += program_paths([args[0], tail[0]], cwd)
    if body and len(body) <= 65536:
        try:
            lex = shlex.shlex(body, posix=True, punctuation_chars=';&|<>()')
            lex.whitespace_split = True
            lex.commenters = ''
            inner = list(lex)
        except ValueError:
            inner = []
        # Optional literal `cd /path && command`; do not parse general scripts.
        if (len(inner) >= 4 and inner[0] == 'cd' and inner[2] == '&&' and
                Path(inner[1]).is_absolute() and not re.search(r'[$`%\n\r]', inner[1])):
            cwd, inner = inner[1], inner[3:]
        # Leading literal environment assignments, without reading environment.
        while inner and re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', inner[0]):
            inner = inner[1:]
        if inner and not any(t and all(c in ';&|<>()' for c in t) for t in inner):
            paths += wrapped_program_paths(inner, cwd, depth + 1)
    return list(dict.fromkeys(paths))


def summarize_unit(raw):
    fields = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
    result = {k: fields.get(k) for k in ('Id', 'LoadState', 'ActiveState', 'MainPID',
                                       'User', 'FragmentPath', 'WorkingDirectory')}
    start = fields.get('ExecStart', '')
    # A display parser, NOT a systemd launcher. Do not execute reconstructed argv.
    m = re.search(r'argv\[\]=(.*?)(?: ; (?:ignore_errors|flags|start_time)=|\s*\})', start)
    try:
        args = shlex.split(m.group(1)) if m else []
    except ValueError:
        args = []
    result['program_paths'] = wrapped_program_paths(args, fields.get('WorkingDirectory'))
    wrapper = Path(args[0]).name if args else None
    result['launcher'] = wrapper if wrapper in ('sg', 'sh', 'bash', 'dash', 'zsh', 'env', 'exec') else None
    if result['launcher']:
        outer = set(program_paths(args, fields.get('WorkingDirectory')))
        nested = [v for v in result['program_paths'] if v not in outer]
        result['entry_parse'] = 'wrapped_paths_candidate' if nested else 'wrapper_only'
    else:
        result['entry_parse'] = 'paths_found' if result['program_paths'] else 'unknown'
    result['entry_source'] = 'loaded_systemctl_show_metadata_not_executed'
    return result


def services():
    binary = shutil.which('systemctl')
    if not binary:
        return [{'scope': 'both', 'status': 'systemctl_not_found', 'units': []}]
    out = []
    props = 'Id,LoadState,ActiveState,MainPID,User,FragmentPath,WorkingDirectory,ExecStart'
    for scope, flags in [('user', ['--user']), ('system', [])]:
        status, raw = command([binary, *flags, 'list-units', '--all', '--type=service',
                               '--no-legend', '--plain', '--no-pager'])
        names = {line.split()[0] for line in raw.splitlines()
                 if line.split() and 'openclaw' in line.split()[0].lower()}
        # Also inspect the default name if the installed unit is not currently loaded.
        names.add('openclaw-gateway.service')
        units = []
        for name in sorted(names)[:8]:
            if not re.fullmatch(r'[A-Za-z0-9_.@:\\-]+\.service', name):
                continue
            ok, detail = command([binary, *flags, 'show', name, '--no-pager', '-p', props])
            if ok == 'ok':
                row = summarize_unit(detail)
                if row.get('LoadState') != 'not-found':
                    units.append(row)
        out.append({'scope': scope, 'status': status, 'units': units})
    return out


def processes(proc=Path('/proc')):
    out = []
    for folder in sorted(proc.iterdir()):
        if not folder.name.isdigit() or int(folder.name) == os.getpid():
            continue
        comm = text(folder / 'comm', 256).strip()
        args = text(folder / 'cmdline').split('\0')
        # Match executable/script identities, not arbitrary --message or --token values.
        named = 'openclaw' in comm.lower() or any('openclaw' in a.lower()
                   for a in args[:2] if a and not a.startswith('-') and ' ' not in a)
        if not named:
            continue
        stat = text(folder / 'status', 4096)
        uid = re.search(r'^Uid:\s+(\d+)', stat, re.M)
        cwd, exe = link(folder / 'cwd'), link(folder / 'exe')
        cgroup = text(folder / 'cgroup', 8192)
        unit = re.findall(r'([^/\n]+\.service)(?:/|$)', cgroup, re.M)
        out.append({'pid': int(folder.name), 'uid': int(uid[1]) if uid else None,
                    'comm': comm, 'exe': exe, 'cwd': cwd,
                    'program_paths': program_paths(args, cwd), 'service_units': unit,
                    'details': 'metadata_only' if cwd and exe else 'partial_or_permission_denied'})
        if len(out) >= 30:
            break
    return out


def package_at(root):
    root = Path(root)
    try:
        package = root / 'package.json'
        if not package.is_file() or package.stat().st_size > 262144:
            return None
        data = json.loads(text(package, 262145))
        if not isinstance(data, dict) or data.get('name') != 'openclaw':
            return None
        version = data.get('version')
        if not isinstance(version, str) or not re.fullmatch(r'[0-9A-Za-z_.+\-]{1,80}', version):
            version = 'UNKNOWN'
        bins = data.get('bin')
        entry = bins.get('openclaw') if isinstance(bins, dict) else bins
        entry = entry if isinstance(entry, str) else 'openclaw.mjs'
        target = (root / entry).resolve()
        if not target.is_relative_to(root.resolve()) or not target.is_file():
            target = None
        return {'package_root': str(root.resolve()), 'package_version': version,
                'entry_file': str(target) if target else None,
                'version_source': 'package.json_only_not_executed'}
    except (OSError, ValueError, RuntimeError):
        return None


def installed(proc_rows, service_rows, extra_roots=(), home=None):
    home = Path.home() if home is None else Path(home)
    patterns = [str(home / p) for p in (
        '.nvm/versions/node/*/bin/openclaw', '.local/bin/openclaw',
        '.npm-global/bin/openclaw', '.local/share/pnpm/openclaw',
        '.local/share/pnpm/global/*/node_modules/openclaw/package.json',
        '.local/share/mise/installs/node/*/bin/openclaw', '.volta/tools/image/packages/openclaw/*/lib/node_modules/openclaw/package.json',
        '.local/share/fnm/node-versions/*/installation/bin/openclaw',
        'openclaw/openclaw.mjs', 'workspaces/code/openclaw/openclaw.mjs')]
    patterns += ['/usr/local/bin/openclaw', '/usr/bin/openclaw',
                 '/usr/local/lib/node_modules/openclaw/package.json',
                 '/usr/lib/node_modules/openclaw/package.json', '/opt/openclaw/openclaw.mjs']
    entries = set()
    for pattern in patterns:
        for path in glob.iglob(pattern):
            entries.add(path)
            if len(entries) >= 200:
                break
    for name in ('openclaw',):
        path = shutil.which(name)
        if path:
            entries.add(path)
    dirs = {Path(p) for p in extra_roots}
    for row in proc_rows + [u for s in service_rows for u in s['units']]:
        cwd = row.get('cwd') or row.get('WorkingDirectory')
        if cwd and Path(cwd).is_absolute():
            dirs.add(Path(cwd))
        for path in row.get('program_paths', []) + [row.get('exe')]:
            if path:
                entries.add(path)
    existing = []
    for value in sorted(entries):
        p = Path(value)
        try:
            if not p.is_file():
                continue
            resolved = p.resolve()
            if 'openclaw' in str(p).lower():
                existing.append({'path': str(p), 'resolved': str(resolved)})
            dirs.update(list(resolved.parents)[:4])
            if resolved.name in ('node', 'nodejs'):
                dirs.add(resolved.parent.parent / 'lib/node_modules/openclaw')
        except (OSError, RuntimeError):
            continue
    packages = {}
    for d in sorted(dirs):
        for candidate in (d, d / 'node_modules/openclaw'):
            result = package_at(candidate)
            if result:
                packages[result['package_root']] = result
    return {'entry_candidates': existing, 'packages': list(packages.values())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, action='append', default=[],
                        help='optional known source/package directory; not searched recursively')
    args = parser.parse_args(argv)
    if not sys.platform.startswith('linux'):
        parser.error('run on Spark Linux')
    if os.geteuid() == 0:
        parser.error('run as the ordinary user; do not use sudo')
    rows, units = processes(), services()
    report = {'status': 'LOCATED_METADATA_ONLY_NOT_RUN', 'processes': rows,
              'services': units, **installed(rows, units, args.source_dir),
              'limits': 'No full-disk scan. Missing results may be other user/container/custom unit. '
                        'No install/configuration/inference was performed.'}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError) as exc:
        print('LOCATE_ERROR:', type(exc).__name__, file=sys.stderr)
        sys.exit(2)
