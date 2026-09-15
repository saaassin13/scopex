from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from contextlib import contextmanager
import tempfile
from typing import Any
import zipfile


_IMAGE_RE = re.compile(r'^(?P<stamp>\d{8}-\d{9})\.(?:jpg|jpeg)$', re.IGNORECASE)
_LINE_TS_RE = re.compile(r'^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[:.,]\d{3})')


class DataPackageError(ValueError):
    pass


class DataPackageService:
    """Build task-owned downloads from recorded business-data access."""

    MODES = {'image_evidence', 'image_window', 'encoder_window', 'encoder_source_files'}

    def __init__(self, *, work_root: Path, data_binds: tuple[str, ...], max_bytes: int, max_files: int) -> None:
        self.work_root = Path(work_root)
        self.max_bytes = max_bytes
        self.max_files = max_files
        self._roots: list[tuple[Path, str]] = []
        for bind in data_binds:
            parts = bind.rsplit(':', 2)
            if len(parts) == 3 and parts[2] == 'ro':
                self._roots.append((Path(parts[0]).resolve(), parts[1].rstrip('/')))
        self._lock = threading.Lock()
        self._busy: set[str] = set()

    @contextmanager
    def operation(self, task_id: str):
        with self._lock:
            if task_id in self._busy:
                raise DataPackageError('该任务正在收集或删除数据，请稍后重试')
            self._busy.add(task_id)
        try:
            yield
        finally:
            with self._lock:
                self._busy.discard(task_id)

    def _task_root(self, task_id: str) -> Path:
        if not task_id or '/' in task_id or '\\' in task_id or task_id in {'.', '..'}:
            raise DataPackageError('invalid task id')
        return self.work_root / task_id

    def _resolve(self, agent_path: str) -> Path:
        for host_root, agent_root in sorted(self._roots, key=lambda row: len(row[1]), reverse=True):
            if agent_path == agent_root or agent_path.startswith(agent_root + '/'):
                relative = agent_path[len(agent_root):].lstrip('/')
                resolved = (host_root / relative).resolve()
                try:
                    resolved.relative_to(host_root)
                except ValueError as exc:
                    raise DataPackageError('recorded data path escaped its configured source') from exc
                return resolved
        raise DataPackageError(f'recorded data path is not in a configured source: {agent_path}')

    def _access_rows(self, task_id: str) -> list[dict[str, Any]]:
        path = self._task_root(task_id) / 'scratch' / 'data-access.jsonl'
        if not path.is_file():
            return []
        rows = []
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and value.get('schema') == 1:
                rows.append(value)
        return rows

    @staticmethod
    def _image_evidence(evidence: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for item in evidence.get('items', []):
            metadata = item.get('metadata') or {}
            source = item.get('source')
            if (metadata.get('evidence_type') == 'image' and isinstance(source, str)
                    and isinstance(metadata.get('sha256'), str) and metadata.get('sha256')):
                out.append({'path': source, 'sha256': metadata.get('sha256')})
        return out

    def options(self, task_id: str, evidence: dict[str, Any]) -> dict[str, Any]:
        rows = self._access_rows(task_id)
        images = self._image_evidence(evidence)
        options = []
        if images:
            options.append({'mode': 'image_evidence', 'label': '已分析照片', 'count': len(images), 'recommended': True})
        image_scopes = [row for row in rows if row.get('source') == 'left_camera_multimodal' and row.get('operation') == 'locate' and row.get('data_kind') == 'jpg']
        unique_image_scopes = {(row.get('start'), row.get('end')): row for row in image_scopes}
        if image_scopes:
            options.append({'mode': 'image_window', 'label': '分析时间范围内的照片',
                            'count': sum(int(row.get('matched_count') or 0) for row in unique_image_scopes.values()),
                            'recommended': not images})
        encoder = [row for row in rows if row.get('purpose') == 'encoder_health' and row.get('operation') == 'analyze']
        if encoder:
            options.append({'mode': 'encoder_window', 'label': '分析时间范围的原始日志', 'count': None, 'recommended': True})
            source_count = len({path for row in encoder for path in row.get('source_files', []) if isinstance(path, str)})
            options.append({'mode': 'encoder_source_files', 'label': '完整轮转日志', 'count': source_count, 'recommended': False})
        package_dir = self._task_root(task_id) / 'collected'
        package = package_dir / 'scopex-data.zip'
        result = {'task_id': task_id, 'available': bool(options), 'options': options,
                  'package_ready': package.is_file(), 'download_url': f'/tasks/{task_id}/data-package/download' if package.is_file() else None}
        if package.is_file():
            try:
                with zipfile.ZipFile(package) as archive:
                    manifest = json.loads(archive.read('manifest.json'))
                result['collected_files'] = len(manifest.get('files', []))
                result['missing_files'] = len(manifest.get('missing', []))
                result['package_modes'] = manifest.get('modes', [])
                result['partial'] = bool(manifest.get('missing'))
            except (OSError, json.JSONDecodeError, zipfile.BadZipFile, KeyError):
                pass
        return result

    @staticmethod
    def _parse_time(value: str) -> datetime:
        normalized = value.replace(',', ':').replace('.', ':')
        for fmt in ('%Y-%m-%d %H:%M:%S:%f', '%Y-%m-%d %H:%M:%S'):
            try:
                return datetime.strptime(normalized, fmt)
            except ValueError:
                pass
        raise DataPackageError(f'invalid recorded time: {value}')

    def _window_images(self, rows: list[dict[str, Any]]) -> list[tuple[str, Path]]:
        found: dict[str, Path] = {}
        for row in rows:
            if row.get('source') != 'left_camera_multimodal' or row.get('operation') != 'locate' or row.get('data_kind') != 'jpg':
                continue
            start, end = self._parse_time(str(row['start'])), self._parse_time(str(row['end']))
            root_row = next(((host, agent) for host, agent in self._roots if agent == '/agent-data/left-camera'), None)
            if root_row is None:
                raise DataPackageError('left camera source is not configured')
            host_root, agent_root = root_row
            cursor = start.replace(minute=0, second=0, microsecond=0)
            while cursor < end:
                hour = host_root / cursor.strftime('%Y%m%d') / cursor.strftime('%H')
                if hour.is_dir() and not hour.is_symlink() and not hour.parent.is_symlink():
                    with os.scandir(hour) as entries:
                        for entry in entries:
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            match = _IMAGE_RE.fullmatch(entry.name)
                            if not match:
                                continue
                            try:
                                stamp = datetime.strptime(match.group('stamp'), '%Y%m%d-%H%M%S%f')
                            except ValueError:
                                continue
                            if start <= stamp < end:
                                agent_path = f"{agent_root}/{cursor:%Y%m%d}/{cursor:%H}/{entry.name}"
                                found[agent_path] = Path(entry.path)
                cursor = cursor.replace(minute=0, second=0, microsecond=0)
                cursor += timedelta(hours=1)
        return sorted(found.items())

    def _encoder_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [row for row in rows if row.get('purpose') == 'encoder_health' and row.get('operation') == 'analyze']

    def build(self, task_id: str, modes: list[str], evidence: dict[str, Any]) -> Path:
        clean_modes = list(dict.fromkeys(modes))
        if not clean_modes or any(mode not in self.MODES for mode in clean_modes):
            raise DataPackageError('select at least one supported data package mode')
        rows = self._access_rows(task_id)
        package_dir = self._task_root(task_id) / 'collected'
        package_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        target = package_dir / 'scopex-data.zip'
        temporary = package_dir / '.scopex-data.zip.tmp'
        manifest = {'schema': 1, 'task_id': task_id,
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'modes': clean_modes, 'access_scopes': rows, 'files': [], 'missing': []}
        written_paths = set()
        total_bytes = 0

        def issue(path, reason):
            manifest['missing'].append({'path': path, 'reason': reason})

        def add_file(archive, agent_path, arcname, expected_sha=None, windows=None):
            nonlocal total_bytes
            if arcname in written_paths:
                return
            if len(written_paths) >= self.max_files:
                issue(agent_path, 'file_limit')
                return
            try:
                source = self._resolve(agent_path)
                handle = source.open('rb')
            except (OSError, DataPackageError):
                issue(agent_path, 'unavailable')
                return
            # Stage one file on disk: a source read failure must not leave a
            # corrupt ZIP entry. Hash and archive the same staged bytes.
            with handle, tempfile.TemporaryFile(dir=package_dir) as staged:
                digest = hashlib.sha256()
                size = 0
                try:
                    remaining = os.fstat(handle.fileno()).st_size
                    if windows is None and remaining > self.max_bytes - total_bytes:
                        issue(agent_path, 'byte_limit')
                        return
                    while remaining:
                        block = handle.readline(min(remaining, 1024 * 1024)) if windows is not None else handle.read(min(remaining, 1024 * 1024))
                        if not block:
                            issue(agent_path, 'source_shortened')
                            return
                        remaining -= len(block)
                        if windows is not None:
                            match = _LINE_TS_RE.match(block[:32].decode('ascii', errors='ignore'))
                            if not match:
                                continue
                            try:
                                stamp = self._parse_time(match.group('ts'))
                            except DataPackageError:
                                continue
                            if not any(start <= stamp < end for start, end in windows):
                                continue
                        if total_bytes + size + len(block) > self.max_bytes:
                            issue(agent_path, 'byte_limit_partial_log')
                            break
                        # Disk/ZIP write errors propagate, preserving the old package.
                        staged.write(block)
                        digest.update(block)
                        size += len(block)
                except OSError as exc:
                    # A full destination disk cannot be fixed by skipping sources.
                    if exc.errno in (28, 122):
                        raise
                    issue(agent_path, 'read_error')
                    return
                if not size and windows is not None:
                    return
                sha = digest.hexdigest()
                if expected_sha and sha != expected_sha:
                    issue(agent_path, 'content_changed_current_copy_included')
                staged.seek(0)
                with archive.open(arcname, 'w', force_zip64=True) as output:
                    for block in iter(lambda: staged.read(1024 * 1024), b''):
                        output.write(block)
                written_paths.add(arcname)
                total_bytes += size
                manifest['files'].append({'source': agent_path, 'archive_path': arcname,
                                          'bytes': size, 'sha256': sha,
                                          'analysis_sha256': expected_sha})

        try:
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                if 'image_evidence' in clean_modes:
                    for item in self._image_evidence(evidence):
                        add_file(archive, item['path'], 'images/analyzed/' + Path(item['path']).name, item['sha256'])
                if 'image_window' in clean_modes:
                    try:
                        images = dict(self._window_images(rows))
                    except (OSError, DataPackageError):
                        images = {}
                        issue('image_window', 'scan_failed')
                    for row in rows:
                        if row.get('source') == 'left_camera_multimodal' and row.get('data_kind') == 'jpg':
                            for path in row.get('selected_files', []):
                                if isinstance(path, str):
                                    images.setdefault(path, None)
                    for path in sorted(images):
                        add_file(archive, path, 'images/window/' + Path(path).name)
                windows_by_file = {}
                for row in self._encoder_rows(rows):
                    window = (self._parse_time(str(row['start'])), self._parse_time(str(row['end'])))
                    for path in row.get('source_files', []):
                        if isinstance(path, str):
                            windows_by_file.setdefault(path, []).append(window)
                for path, windows in sorted(windows_by_file.items()):
                    if 'encoder_source_files' in clean_modes:
                        add_file(archive, path, 'logs/source/' + Path(path).name)
                    if 'encoder_window' in clean_modes:
                        # Each file is scanned once, so overlapping windows never
                        # require remembering individual log lines.
                        merged = []
                        for start, end in sorted(set(windows)):
                            if merged and start <= merged[-1][1]:
                                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
                            else:
                                merged.append((start, end))
                        add_file(archive, path, 'logs/window/' + Path(path).name, windows=merged)
                if not manifest['files']:
                    raise DataPackageError('没有可收集的数据：源文件不可用或超过限制；已有数据包保持不变')
                manifest['total_bytes'] = total_bytes
                archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target
