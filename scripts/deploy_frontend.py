#!/usr/bin/env python3
"""只更新已挂载的前端目录，保留旧资源和回滚入口，不操作容器或任务。"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from datetime import datetime, timezone


def publish(source: Path, target: Path, backups: Path, revision: str, expected_index: str | None = None) -> dict:
    source, target, backups = source.resolve(), target.resolve(), backups.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError('构建目录与目标目录必须独立')
    index = source / 'index.html'
    html = index.read_text()
    assets = re.findall(r'(?:src|href)="(\./assets/[^"?#]+)"', html)
    if not assets or re.search(r'(?:src|href)="/assets/', html):
        raise ValueError('构建必须使用相对资源路径')
    for asset in assets:
        resolved = (source / asset).resolve()
        if source not in resolved.parents or not resolved.is_file():
            raise ValueError('构建资源缺失或越界')
    if not (target / 'index.html').is_file():
        raise ValueError('目标不是已有前端目录')
    if expected_index and hashlib.sha256((target / 'index.html').read_bytes()).hexdigest() != expected_index:
        raise ValueError('现场前端版本已变化，拒绝覆盖')
    files = sorted(p for p in source.rglob('*') if p.is_file())
    if any(p.is_symlink() for p in source.rglob('*')):
        raise ValueError('构建目录不能包含符号链接')
    # 同名哈希资源必须相同；不覆盖旧页面可能仍在请求的静态文件。
    for path in files:
        relative = path.relative_to(source)
        existing = target / relative
        if str(relative).startswith('assets/') and existing.exists() and path.read_bytes() != existing.read_bytes():
            raise ValueError(f'资源文件名冲突：{relative}')
    backup = backups / (datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-') + revision[:12])
    backup.mkdir(parents=True, mode=0o700)
    shutil.copytree(target, backup / 'dist-before')
    manifest = {'revision': revision, 'target': str(target), 'backup': str(backup),
                'before_index_sha256': hashlib.sha256((target / 'index.html').read_bytes()).hexdigest(),
                'files': {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    for path in files:
        relative = path.relative_to(source)
        if relative == Path('index.html'):
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.read_bytes() == path.read_bytes():
            continue
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as tmp:
            tmp.write(path.read_bytes()); tmp.flush(); os.fsync(tmp.fileno())
        os.chmod(tmp.name, 0o644)
        os.replace(tmp.name, destination)
    with tempfile.NamedTemporaryFile(dir=target, delete=False) as tmp:
        tmp.write(index.read_bytes()); tmp.flush(); os.fsync(tmp.fileno())
    os.chmod(tmp.name, 0o644)
    os.replace(tmp.name, target / 'index.html')
    (backup / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--backups', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--expected-index-sha256', required=True)
    args = parser.parse_args()
    print(json.dumps(publish(args.dist, args.target, args.backups, args.revision, args.expected_index_sha256),ensure_ascii=False))
