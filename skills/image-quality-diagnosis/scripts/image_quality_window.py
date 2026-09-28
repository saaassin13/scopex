#!/usr/bin/env python3
"""Check three consecutive saved originals against supplied visual observations."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import re

IMAGE_NAME = re.compile(r'(\d{8}-\d{9})\.(?:jpg|jpeg)', re.I)


def inventory(root: Path, start_text: str, end_text: str) -> tuple[datetime, datetime, list[tuple[datetime, Path]]]:
    start, end = datetime.fromisoformat(start_text), datetime.fromisoformat(end_text)
    if start.tzinfo or end.tzinfo:
        raise ValueError('use local filename time without timezone offsets')
    if (end - start).total_seconds() not in (1800, 3600):
        raise ValueError('window must be exactly 30 or 60 minutes')
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError('camera source must be a directory')
    files = []
    cursor = start.replace(minute=0, second=0, microsecond=0)
    while cursor < end:
        directory = root / cursor.strftime('%Y%m%d') / cursor.strftime('%H')
        if directory.is_symlink() or directory.parent.is_symlink():
            raise ValueError('camera hour directories must not be symlinks')
        if directory.is_dir():
            for path in directory.iterdir():
                match = IMAGE_NAME.fullmatch(path.name)
                if not match:
                    continue
                if path.is_symlink():
                    raise ValueError('original image must not be a symlink')
                stamp = datetime.strptime(match[1], '%Y%m%d-%H%M%S%f')
                if stamp.strftime('%Y%m%d/%H') != cursor.strftime('%Y%m%d/%H'):
                    raise ValueError('filename time disagrees with camera directory')
                if start <= stamp < end and path.is_file():
                    files.append((stamp, path))
                    if len(files) > 100000:
                        raise ValueError('window contains more than 100000 originals; no partial verdict returned')
        cursor += timedelta(hours=1)
    files.sort()
    if len({stamp for stamp, _ in files}) != len(files):
        raise ValueError('multiple originals share a timestamp; camera ordering is ambiguous')
    return start, end, files


def neighbors(root: Path, start: str, end: str, image: Path) -> dict:
    _, _, files = inventory(root, start, end)
    paths = [path for _, path in files]
    try:
        index = paths.index(image.resolve(strict=True))
    except ValueError as exc:
        raise ValueError('anchor image is not in this camera window') from exc
    # Return a small neighborhood. The operator/Agent can extend at either edge
    # if a clear frame interrupts the first suspected triple.
    selected = files[max(0, index - 2):index + 3]
    return {'camera_root': str(root.resolve()), 'start': start, 'end': end,
            'anchor': str(image), 'consecutive_originals': [str(path) for _, path in selected],
            'note': 'These paths establish order, not visual inspection or a blur verdict.'}


def summarize(data: dict, root: Path) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get('camera'), str) or not data['camera'].strip():
        raise ValueError('camera is required; analyze one source at a time')
    start, end, files = inventory(root, data['start'], data['end'])
    positions = {str(path): i for i, (_, path) in enumerate(files)}
    rows = data.get('observations')
    if not isinstance(rows, list) or len(rows) > 256:
        raise ValueError('observations must be a list with at most 256 entries')
    observed = {}
    for row in rows:
        if not isinstance(row, dict) or row.get('visually_reviewed') is not True:
            raise ValueError('only actual original-image observations qualify, not metrics')
        if row.get('camera', data['camera']) != data['camera']:
            raise ValueError('mixed cameras cannot form a consecutive sequence')
        path = row.get('path')
        if not isinstance(path, str) or '..' in Path(path).parts or Path(path).is_symlink():
            raise ValueError('an original image path is required')
        path = str(Path(path).resolve(strict=True))
        if path not in positions:
            raise ValueError('observation outside this camera or requested window')
        verdict = row.get('verdict')
        if verdict not in ('degraded', 'clear', 'uncertain'):
            raise ValueError('verdict must be degraded, clear or uncertain')
        if not isinstance(row.get('reason'), str) or not row['reason'].strip():
            raise ValueError('every observation needs visible evidence of dirt/blur or clear/uncertain appearance')
        i = positions[path]
        if i in observed and observed[i]['verdict'] != verdict:
            raise ValueError('conflicting verdicts for one original')
        observed[i] = {'path': path, 'time': files[i][0].isoformat(sep=' ', timespec='milliseconds'),
                       'verdict': verdict, 'reason': row['reason'].strip()}
    groups = []
    current = []
    for i in sorted(observed):
        if observed[i]['verdict'] != 'degraded':
            if len(current) >= 3:
                groups.append(current)
            current = []
        elif current and i != current[-1] + 1:
            if len(current) >= 3:
                groups.append(current)
            current = [i]
        else:
            current.append(i)
    if len(current) >= 3:
        groups.append(current)
    events = [{'start': observed[group[0]]['time'], 'end': observed[group[-1]]['time'],
               'consecutive_count': len(group), 'frames': [observed[i] for i in group],
               'max_saved_frame_gap_seconds': max((files[b][0] - files[a][0]).total_seconds()
                                                  for a, b in zip(group, group[1:]))}
              for group in groups]
    unknown = len(files) - sum(row['verdict'] != 'uncertain' for row in observed.values())
    status = 'abnormal' if events else 'no_images' if not files else 'insufficient_evidence' if unknown else 'criterion_not_met'
    bucket_count = int((end - start).total_seconds() // 300)
    timeline = []
    for bucket in range(bucket_count):
        lo, hi = start + timedelta(minutes=5 * bucket), start + timedelta(minutes=5 * (bucket + 1))
        indices = [i for i, (stamp, _) in enumerate(files) if lo <= stamp < hi]
        timeline.append({'start': lo.isoformat(sep=' '), 'end': hi.isoformat(sep=' '),
                         'saved_frames': len(indices),
                         'reviewed_frames': sum(i in observed for i in indices),
                         'degraded_frames': sum(observed.get(i, {}).get('verdict') == 'degraded' for i in indices)})
    return {'scopex_role': 'business_facts', 'source': 'image-quality-diagnosis', 'schema': 1,
            'camera': data['camera'], 'start': data['start'], 'end': data['end'],
            'status': status, 'rule': 'three_consecutive_saved_originals_with_visible_dirt_or_blur',
            'basis': 'provided_visual_observations_not_independent_verification',
            'saved_frames': len(files), 'reviewed_frames': len(observed), 'unknown_frames': unknown,
            'events': events, 'timeline': timeline,
            'limitations': ['Consecutive means adjacent saved originals in this camera window, not every camera exposure.',
                            'A viewed image is a supplied observation; the tool verifies ordering, not image content.',
                            'Unviewed/uncertain originals break the proof; missing saves never establish clear images.',
                            'Five-minute bins show coverage only; no blurry-duration or full-window percentage is inferred.']}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    nearby = commands.add_parser('neighbors')
    nearby.add_argument('image', type=Path)
    nearby.add_argument('--start', required=True)
    nearby.add_argument('--end', required=True)
    aggregate = commands.add_parser('summarize')
    aggregate.add_argument('observations', type=Path)
    for command in (nearby, aggregate):
        command.add_argument('--source-root', type=Path, default=Path('/agent-data/left-camera'))
    args = parser.parse_args()
    try:
        if args.command == 'neighbors':
            result = neighbors(args.source_root, args.start, args.end, args.image)
        else:
            if args.observations.stat().st_size > 1024 * 1024:
                raise ValueError('observation input exceeds 1 MiB')
            result = summarize(json.loads(args.observations.read_text(encoding='utf-8')), args.source_root)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
