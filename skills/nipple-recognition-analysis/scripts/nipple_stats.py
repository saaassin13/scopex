#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
from typing import Any

TS_RE = re.compile(r'^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}:\d{3})')
TS_FMT = '%Y-%m-%d %H:%M:%S:%f'
LOG_RE = re.compile(r'^CowDisinfect-(?P<date>\d{8})-(?P<time>\d{6})\.log(?:\.(?P<rotation>\d+))?$')
START_RE = re.compile(
    r'Start left camera AI detect, ImgTimeStamp\[(?P<img>[\d-]+)\], '
    r'CowOccuredCount\[(?P<cow>\d+)\], DetectingNumCurRound\[(?P<round>\d+)\]'
)
FRAME_RE = re.compile(
    r'Left camera cow \[(?P<cow>\d+)\] detecting \[(?P<round>\d+)\] finished,'
    r'.*NippleNum\[(?P<nipple>\d+)\]'
)
FINISH_RE = re.compile(
    r'New cow detecte finished, cow count \[(?P<cow>\d+)\], '
    r'LastImgTimeStamp\[(?P<img>[\d-]+)\]'
)
SAVE_RE = re.compile(r'SaveImg2Disk begin,.*filePath\[(?P<path>[^\]]+\.jpg)\]')


def parse_time(text: str) -> datetime:
    try:
        return datetime.strptime(text, TS_FMT)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f'invalid timestamp: {text}') from exc


def line_time(line: str) -> datetime | None:
    m = TS_RE.match(line)
    if not m:
        return None
    try:
        return datetime.strptime(m.group('ts'), TS_FMT)
    except ValueError:
        return None


def first_log_time(path: Path) -> datetime | None:
    with path.open(encoding='utf-8', errors='replace') as handle:
        for line in handle:
            ts = line_time(line)
            if ts is not None:
                return ts
    return None


def ordered_logs(paths: list[Path]) -> list[Path]:
    rows = []
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
        first = first_log_time(path)
        rows.append((first or datetime.max, str(path), path))
    rows.sort(key=lambda row: (row[0], row[1]))
    return [row[2] for row in rows]


def hour_keys(start: datetime, end: datetime, maximum: int = 48) -> set[str]:
    if end <= start:
        raise ValueError('--end must be after --start')
    current = start.replace(minute=0, second=0, microsecond=0)
    out: set[str] = set()
    while current < end:
        out.add(current.strftime('%Y%m%d%H'))
        if len(out) > maximum:
            raise ValueError(f'time window exceeds {maximum} hour buckets')
        current += timedelta(hours=1)
    return out


def discover_logs(root: Path, start: datetime, end: datetime, max_files: int = 32) -> list[Path]:
    if not root.is_dir():
        raise FileNotFoundError(root)
    keys = hour_keys(start, end)
    rows: list[tuple[datetime, int, Path]] = []
    with os.scandir(root) as entries:
        for entry in entries:
            if not entry.is_file(follow_symlinks=False):
                continue
            m = LOG_RE.fullmatch(entry.name)
            if not m:
                continue
            ts = datetime.strptime(m.group('date') + m.group('time'), '%Y%m%d%H%M%S')
            if ts.strftime('%Y%m%d%H') not in keys:
                continue
            rows.append((ts, int(m.group('rotation') or 0), root / entry.name))
    rows.sort(key=lambda row: (row[0], row[1], str(row[2])))
    if len(rows) > max_files:
        raise ValueError(f'relevant rotated logs exceed max_files={max_files}; narrow the window')
    return ordered_logs([row[2] for row in rows])


def artifact_hour_dirs(root: Path, start: datetime | None, end: datetime | None) -> list[Path]:
    if not root.is_dir():
        raise FileNotFoundError(root)
    if start is None or end is None:
        return [root]
    dirs = []
    current = start.replace(minute=0, second=0, microsecond=0)
    while current < end:
        if len(dirs) >= 24:
            raise ValueError('artifact time window exceeds 24 hour directories; narrow the request')
        candidate = root / current.strftime('%Y%m%d') / current.strftime('%H')
        if candidate.is_dir():
            dirs.append(candidate)
        current += timedelta(hours=1)
    return dirs


def artifact_index(root: Path | None, start: datetime | None, end: datetime | None) -> dict[str, dict[str, str | None]]:
    out: dict[str, dict[str, str | None]] = {}
    if root is None:
        return out
    for directory in artifact_hour_dirs(root, start, end):
        with os.scandir(directory) as entries:
            for entry in entries:
                if not entry.is_file(follow_symlinks=False):
                    continue
                path = directory / entry.name
                suffix = path.suffix.lower()
                if suffix not in {'.json', '.jpg', '.jpeg'}:
                    continue
                slot = out.setdefault(path.stem, {'json': None, 'image': None})
                if suffix == '.json':
                    slot['json'] = str(path)
                else:
                    slot['image'] = str(path)
    return out


def json_2d_marker_count(path: str | None) -> int | None:
    if not path:
        return None
    try:
        doc = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    labels = set()
    for rect in (((doc.get('Markers') or {}).get('Rect')) or []):
        if not isinstance(rect, dict):
            continue
        text = str(rect.get('Text') or '').strip()
        if text in {'1', '2', '3', '4'}:
            labels.add(text)
    return min(len(labels), 4)


def in_window(ts: datetime | None, start: datetime | None, end: datetime | None) -> bool:
    if ts is None:
        return False
    if start is not None and ts < start:
        return False
    if end is not None and ts >= end:
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description='Compute cow-level 2D nipple-detection KPIs from a bounded CowDisinfect time window.')
    ap.add_argument('logs', nargs='*', type=Path, help='explicit rotated CowDisinfect log files')
    ap.add_argument('--log-dir', type=Path, help='CowDisinfect log directory; hourly files selected by --start/--end')
    ap.add_argument('--artifact-dir', type=Path, help='optional LeftCamera root containing YYYYMMDD/HH result directories')
    ap.add_argument('--start', type=parse_time, help='include cows whose first named detection starts at/after this time')
    ap.add_argument('--end', type=parse_time, help='exclude cows whose first named detection starts at/after this time')
    ap.add_argument('--details-out', type=Path, help='optional full per-cow details path; keep large details out of model stdout')
    ap.add_argument('--out', type=Path, help='optional compact summary output path')
    args = ap.parse_args()

    if args.start and args.end and args.end <= args.start:
        ap.error('--end must be after --start')
    if args.log_dir and args.logs:
        ap.error('use either explicit logs or --log-dir, not both')
    try:
        if args.log_dir:
            if args.start is None or args.end is None:
                ap.error('--log-dir requires --start and --end')
            logs = discover_logs(args.log_dir, args.start, args.end)
        else:
            if not args.logs:
                ap.error('provide rotated logs or --log-dir')
            logs = ordered_logs(list(args.logs))
        artifacts = artifact_index(args.artifact_dir, args.start, args.end)
    except (FileNotFoundError, ValueError) as exc:
        ap.error(f'path/data selection error: {exc}')

    epoch = 0
    last_occured: int | None = None
    active_img_by_pair: dict[tuple[int, int], str] = {}
    frame_by_img: dict[str, dict[str, Any]] = {}
    cycles: dict[str, dict[str, Any]] = {}
    saved_images_from_log: set[str] = set()
    finish_without_frame = 0
    log_meta: list[dict[str, Any]] = []

    def cycle(key: str, cow: int) -> dict[str, Any]:
        return cycles.setdefault(key, {
            'cow_id': key,
            'epoch': int(key.split(':', 1)[0][1:]),
            'cow_occured_count': cow,
            'first_detect_ts': None,
            'first_detect_img_timestamp': None,
            'detect_rounds': 0,
            'finish_ts': None,
            'finish_cow_count': None,
            'last_img_timestamp': None,
            'selected_2d_nipple_raw': None,
            'selected_2d_nipple_count': None,
            'selected_frame_round': None,
            'selected_frame_line': None,
            'finish_line': None,
        })

    global_line = 0
    for log in logs:
        first_seen = last_seen = None
        matching_lines = 0
        with log.open(encoding='utf-8', errors='replace') as f:
            for line in f:
                global_line += 1
                ts = line_time(line)
                if ts is not None:
                    first_seen = first_seen or ts
                    last_seen = ts
                m = START_RE.search(line)
                if m:
                    matching_lines += 1
                    cow = int(m.group('cow'))
                    rnd = int(m.group('round'))
                    img = m.group('img')
                    if last_occured is not None and cow < last_occured:
                        epoch += 1
                        active_img_by_pair.clear()
                    last_occured = cow
                    key = f'e{epoch}:{cow}'
                    row = cycle(key, cow)
                    row['detect_rounds'] += 1
                    if row['first_detect_ts'] is None:
                        row['first_detect_ts'] = ts.strftime(TS_FMT) if ts else None
                        row['first_detect_img_timestamp'] = img
                    active_img_by_pair[(cow, rnd)] = img
                    frame_by_img[img] = {
                        'cow_id': key,
                        'cow_occured_count': cow,
                        'round': rnd,
                        'start_ts': ts.strftime(TS_FMT) if ts else None,
                        'start_line': global_line,
                        'nipple_raw': None,
                        'result_line': None,
                    }
                    continue
                m = FRAME_RE.search(line)
                if m:
                    matching_lines += 1
                    cow = int(m.group('cow'))
                    rnd = int(m.group('round'))
                    img = active_img_by_pair.get((cow, rnd))
                    if img and img in frame_by_img:
                        frame_by_img[img]['nipple_raw'] = int(m.group('nipple'))
                        frame_by_img[img]['result_line'] = global_line
                    continue
                m = FINISH_RE.search(line)
                if m:
                    matching_lines += 1
                    img = m.group('img')
                    frame = frame_by_img.get(img)
                    if frame is None:
                        finish_without_frame += 1
                        continue
                    key = frame['cow_id']
                    row = cycle(key, frame['cow_occured_count'])
                    row['finish_ts'] = ts.strftime(TS_FMT) if ts else None
                    row['finish_cow_count'] = int(m.group('cow'))
                    row['last_img_timestamp'] = img
                    row['finish_line'] = global_line
                    raw_count = frame.get('nipple_raw')
                    row['selected_2d_nipple_raw'] = raw_count
                    row['selected_2d_nipple_count'] = min(raw_count, 4) if isinstance(raw_count, int) else None
                    row['selected_frame_round'] = frame.get('round')
                    row['selected_frame_line'] = frame.get('result_line')
                    continue
                m = SAVE_RE.search(line)
                if m:
                    saved_images_from_log.add(Path(m.group('path')).stem)
        log_meta.append({
            'path': str(log),
            'first_ts': first_seen.strftime(TS_FMT) if first_seen else None,
            'last_ts': last_seen.strftime(TS_FMT) if last_seen else None,
            'business_anchor_lines': matching_lines,
        })

    selected_cycles = []
    for row in cycles.values():
        first_ts = parse_time(row['first_detect_ts']) if row['first_detect_ts'] else None
        if not in_window(first_ts, args.start, args.end):
            continue
        item = dict(row)
        img = item.get('last_img_timestamp')
        artifact = artifacts.get(str(img), {'json': None, 'image': None}) if img else {'json': None, 'image': None}
        item['artifact'] = {
            'json': artifact.get('json'),
            'image': artifact.get('image'),
            'image_save_seen_in_log': bool(img and img in saved_images_from_log),
            'json_2d_marker_count': json_2d_marker_count(artifact.get('json')),
        }
        marker_count = item['artifact']['json_2d_marker_count']
        selected_count = item.get('selected_2d_nipple_count')
        item['artifact_2d_count_matches_log'] = marker_count == selected_count if marker_count is not None and selected_count is not None else None
        if item.get('finish_ts') is None:
            item['status'] = 'unfinished_cycle'
        elif selected_count is None:
            item['status'] = 'final_2d_result_missing'
        else:
            item['status'] = 'finished'
        selected_cycles.append(item)

    selected_cycles.sort(key=lambda row: row.get('first_detect_ts') or '')
    total_cows = len(selected_cycles)
    finished = [row for row in selected_cycles if row.get('selected_2d_nipple_count') is not None]
    complete = sum(1 for row in selected_cycles if row.get('selected_2d_nipple_count') == 4)
    raw_sum = sum(int(row.get('selected_2d_nipple_raw') or 0) for row in selected_cycles)
    detected_sum = sum(int(row.get('selected_2d_nipple_count') or 0) for row in selected_cycles)
    over_detected = sum(1 for row in selected_cycles if (row.get('selected_2d_nipple_raw') or 0) > 4)
    distribution = Counter(
        str(row['selected_2d_nipple_count']) if row.get('selected_2d_nipple_count') is not None else 'missing'
        for row in selected_cycles
    )
    artifact_json = sum(1 for row in selected_cycles if row['artifact']['json'])
    artifact_image = sum(1 for row in selected_cycles if row['artifact']['image'])
    artifact_log_save = sum(1 for row in selected_cycles if row['artifact']['image_save_seen_in_log'])
    artifact_mismatch = [row['cow_id'] for row in selected_cycles if row['artifact_2d_count_matches_log'] is False]
    expected = total_cows * 4

    summary = {
        'total_cows': total_cows,
        'cows_with_final_2d_result': len(finished),
        'complete_four_nipple_cows': complete,
        'complete_four_nipple_rate': round(complete / total_cows, 6) if total_cows else None,
        'raw_2d_detections': raw_sum,
        'capped_2d_detections': detected_sum,
        'expected_nipples': expected,
        'nipple_recognition_rate': round(detected_sum / expected, 6) if expected else None,
        'distribution_by_final_2d_count': dict(sorted(distribution.items())),
    }
    quality = {
        'finish_without_matching_final_frame': finish_without_frame,
        'unfinished_cycles_in_window': sum(1 for row in selected_cycles if row['status'] == 'unfinished_cycle'),
        'final_2d_result_missing': sum(1 for row in selected_cycles if row['status'] == 'final_2d_result_missing'),
        'over_four_2d_detections': over_detected,
        'artifact_json_present': artifact_json if args.artifact_dir else None,
        'artifact_image_present': artifact_image if args.artifact_dir else None,
        'image_save_seen_in_log': artifact_log_save,
        'artifact_2d_count_mismatch_count': len(artifact_mismatch),
        'artifact_2d_count_mismatch_cows_sample': artifact_mismatch[:10],
    }
    result = {
        'scopex_role': 'business_facts',
        'schema': 3,
        'source': 'nipple-recognition-analysis',
        'semantics': {
            'cow_denominator': 'unique named cow detection cycles whose first DetectingNumCurRound frame starts in the requested window',
            'nipple_source': '2D NippleNum on the frame referenced by New cow detecte finished -> LastImgTimeStamp',
            'max_nipples_per_cow': 4,
            'three_d_nipple_validity_used': False,
            'artifacts': 'JPG/JSON are optional supporting success artifacts and are not the KPI denominator',
        },
        'window': {
            'start': args.start.strftime(TS_FMT) if args.start else None,
            'end': args.end.strftime(TS_FMT) if args.end else None,
            'basis': 'first_detect_ts',
        },
        'logs': log_meta,
        'quality': quality,
        'summary': summary,
    }
    if args.details_out:
        args.details_out.parent.mkdir(parents=True, exist_ok=True)
        args.details_out.write_text(
            json.dumps({'schema': 1, 'cows': selected_cycles}, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8',
        )
        result['details_out'] = str(args.details_out)

    text = json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if total_cows else 1


if __name__ == '__main__':
    raise SystemExit(main())
