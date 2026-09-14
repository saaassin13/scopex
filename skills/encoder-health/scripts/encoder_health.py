#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import statistics
from typing import Any

RAW_RE = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}:\d{3}).*'
    r'Get EncoderVal, raw\[(?P<raw>-?\d+)\], filtered\[(?P<filtered>-?\d+)\]'
)
LOG_RE = re.compile(r'^CowDisinfect-(?P<date>\d{8})-(?P<time>\d{6})\.log(?:\.(?P<rotation>\d+))?$')
TS_FMT = '%Y-%m-%d %H:%M:%S:%f'


def parse_time(text: str) -> datetime:
    try:
        return datetime.strptime(text, TS_FMT)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f'invalid timestamp: {text}') from exc


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def mad(values: list[float], center: float | None = None) -> float | None:
    if not values:
        return None
    c = statistics.median(values) if center is None else center
    return statistics.median(abs(v - c) for v in values)


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    rows = sorted(values)
    if len(rows) == 1:
        return rows[0]
    position = (len(rows) - 1) * q
    low = int(position)
    high = min(low + 1, len(rows) - 1)
    weight = position - low
    return rows[low] * (1.0 - weight) + rows[high] * weight


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


def discover_logs(root: Path, start: datetime, end: datetime, *, max_files: int = 32) -> list[Path]:
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
    return [row[2] for row in rows]


def main() -> int:
    ap = argparse.ArgumentParser(description='Analyze encoder sample health across a bounded CowDisinfect time window without assigning business root cause.')
    ap.add_argument('logs', nargs='*', type=Path, help='explicit rotated log files')
    ap.add_argument('--log-dir', type=Path, help='CowDisinfect log directory; relevant hourly files are selected by --start/--end')
    ap.add_argument('--start', type=parse_time)
    ap.add_argument('--end', type=parse_time)
    ap.add_argument('--invalid-min', type=int, default=2 ** 63,
                    help='raw >= this value is treated as invalid/read-failure evidence')
    ap.add_argument('--gap-factor', type=float, default=3.0,
                    help='sampling gap candidate when dt exceeds factor × median dt')
    ap.add_argument('--gap-min-ms', type=float, default=100.0,
                    help='minimum absolute sampling-gap threshold')
    ap.add_argument('--large-negative-pulses', type=int, default=100000,
                    help='configured large negative candidate threshold; not automatically called reset')
    ap.add_argument('--flat-ms', type=float, default=1000.0,
                    help='unchanged raw duration candidate threshold')
    ap.add_argument('--outlier-mad', type=float, default=12.0,
                    help='delta statistical outlier threshold in MAD units')
    ap.add_argument('--top-events', type=int, default=10)
    ap.add_argument('--out', type=Path, help='optional compact summary output path')
    ap.add_argument('--events-out', type=Path, help='optional detailed candidate-event output path')
    args = ap.parse_args()

    if args.start and args.end and args.end <= args.start:
        ap.error('--end must be after --start')
    if args.log_dir and args.logs:
        ap.error('use either explicit logs or --log-dir, not both')
    if args.log_dir:
        if args.start is None or args.end is None:
            ap.error('--log-dir requires --start and --end')
        try:
            logs = discover_logs(args.log_dir, args.start, args.end)
        except (FileNotFoundError, ValueError) as exc:
            ap.error(str(exc))
    else:
        logs = list(args.logs)
        if not logs:
            ap.error('provide rotated log files or --log-dir')
        for path in logs:
            if not path.is_file():
                ap.error(f'log does not exist: {path}')

    samples: list[dict[str, Any]] = []
    matching_lines_full = 0
    invalid_total = 0
    per_file_matches: dict[str, int] = {}
    for file_index, log in enumerate(logs):
        matched_here = 0
        with log.open(encoding='utf-8', errors='replace') as f:
            for line_no, line in enumerate(f, 1):
                m = RAW_RE.match(line)
                if not m:
                    continue
                matching_lines_full += 1
                ts = datetime.strptime(m.group('ts'), TS_FMT)
                if args.start and ts < args.start:
                    continue
                if args.end and ts >= args.end:
                    continue
                raw = int(m.group('raw'))
                filtered = int(m.group('filtered'))
                invalid = raw < 0 or raw >= args.invalid_min
                if invalid:
                    invalid_total += 1
                matched_here += 1
                samples.append({
                    'ts': ts,
                    'ts_text': m.group('ts'),
                    'raw': raw,
                    'filtered': filtered,
                    'line_no': line_no,
                    'source': str(log),
                    'file_index': file_index,
                    'invalid': invalid,
                })
        per_file_matches[str(log)] = matched_here

    samples.sort(key=lambda row: (row['ts'], row['file_index'], row['line_no']))
    valid = [s for s in samples if not s['invalid']]
    pairs: list[dict[str, Any]] = []
    for sample_seq, (a, b) in enumerate(zip(samples, samples[1:])):
        if a['invalid'] or b['invalid']:
            continue
        dt_ms = (b['ts'] - a['ts']).total_seconds() * 1000.0
        if dt_ms <= 0:
            continue
        pairs.append({
            'sample_seq': sample_seq,
            'a': a,
            'b': b,
            'dt_ms': dt_ms,
            'delta_raw': b['raw'] - a['raw'],
            'delta_filtered': b['filtered'] - a['filtered'],
        })

    dt_values = [p['dt_ms'] for p in pairs]
    median_dt = median(dt_values)
    gap_threshold = max(args.gap_min_ms, (median_dt or args.gap_min_ms) * args.gap_factor)

    positive_deltas = [float(p['delta_raw']) for p in pairs if p['delta_raw'] > 0]
    pos_center = median(positive_deltas)
    pos_mad = mad(positive_deltas, pos_center)
    positive_outlier_threshold = None if pos_center is None else pos_center + args.outlier_mad * max(pos_mad or 0.0, 1.0)

    negative_magnitudes = [float(abs(p['delta_raw'])) for p in pairs if p['delta_raw'] < 0]
    neg_center = median(negative_magnitudes)
    neg_mad = mad(negative_magnitudes, neg_center)
    negative_outlier_threshold = None if neg_center is None else neg_center + args.outlier_mad * max(neg_mad or 0.0, 1.0)

    candidates: list[dict[str, Any]] = []
    negative_count = 0
    large_negative_count = 0
    negative_outlier_count = 0
    positive_outlier_count = 0
    sampling_gap_count = 0

    for p in pairs:
        a, b = p['a'], p['b']
        base = {
            'ts': b['ts_text'],
            'source_from': a['source'],
            'source_to': b['source'],
            'line_from': a['line_no'],
            'line_to': b['line_no'],
            'raw_from': a['raw'],
            'raw_to': b['raw'],
            'delta_raw': p['delta_raw'],
            'dt_ms': round(p['dt_ms'], 3),
        }
        if p['dt_ms'] > gap_threshold:
            sampling_gap_count += 1
            candidates.append({'type': 'sampling_gap', **base})
        if p['delta_raw'] < 0:
            negative_count += 1
            magnitude = abs(p['delta_raw'])
            if magnitude >= args.large_negative_pulses:
                large_negative_count += 1
                candidates.append({'type': 'large_negative_jump_candidate', **base})
            elif negative_outlier_threshold is not None and magnitude > negative_outlier_threshold:
                negative_outlier_count += 1
                candidates.append({
                    'type': 'negative_jump_outlier_candidate',
                    **base,
                    'statistical_threshold_pulses': round(negative_outlier_threshold, 3),
                })
        elif positive_outlier_threshold is not None and p['delta_raw'] > positive_outlier_threshold:
            positive_outlier_count += 1
            candidates.append({
                'type': 'positive_delta_outlier_candidate',
                **base,
                'statistical_threshold_pulses': round(positive_outlier_threshold, 3),
            })

    flat_events: list[dict[str, Any]] = []
    start_idx: int | None = None

    def flush_flat(end_idx: int) -> None:
        nonlocal start_idx
        if start_idx is None or end_idx < start_idx:
            start_idx = None
            return
        p0 = pairs[start_idx]
        pend = pairs[end_idx]
        duration = (pend['b']['ts'] - p0['a']['ts']).total_seconds() * 1000.0
        if duration >= args.flat_ms:
            flat_events.append({
                'type': 'flat_raw_candidate',
                'start': p0['a']['ts_text'],
                'end': pend['b']['ts_text'],
                'duration_ms': round(duration, 3),
                'raw': p0['a']['raw'],
                'source_start': p0['a']['source'],
                'source_end': pend['b']['source'],
                'line_start': p0['a']['line_no'],
                'line_end': pend['b']['line_no'],
            })
        start_idx = None

    previous_seq: int | None = None
    for i, p in enumerate(pairs):
        if previous_seq is not None and p['sample_seq'] != previous_seq + 1:
            flush_flat(i - 1)
        if p['delta_raw'] == 0:
            if start_idx is None:
                start_idx = i
        else:
            flush_flat(i - 1)
        previous_seq = p['sample_seq']
    if pairs:
        flush_flat(len(pairs) - 1)
    candidates.extend(flat_events)
    candidates.sort(key=lambda event: (event.get('ts') or event.get('start') or '', event['type']))

    def severity(event: dict[str, Any]) -> float:
        if event['type'] == 'sampling_gap':
            return float(event.get('dt_ms') or 0.0)
        if event['type'] in {'large_negative_jump_candidate', 'negative_jump_outlier_candidate', 'positive_delta_outlier_candidate'}:
            return abs(float(event.get('delta_raw') or 0.0))
        if event['type'] == 'flat_raw_candidate':
            return float(event.get('duration_ms') or 0.0)
        return 0.0

    top_candidates = sorted(candidates, key=severity, reverse=True)[:max(1, min(args.top_events, 50))]
    filtered_error = [abs(s['raw'] - s['filtered']) for s in valid]
    negative_p95 = percentile(negative_magnitudes, 0.95)
    negative_max = max(negative_magnitudes) if negative_magnitudes else None
    longest_flat = max((event['duration_ms'] for event in flat_events), default=None)

    facts = {
        'matching_encoder_lines_in_selected_logs': matching_lines_full,
        'samples_in_window': len(samples),
        'valid_samples': len(valid),
        'invalid_samples': invalid_total,
        'first_ts': valid[0]['ts_text'] if valid else None,
        'last_ts': valid[-1]['ts_text'] if valid else None,
        'median_sample_dt_ms': round(median_dt, 3) if median_dt is not None else None,
        'sample_gap_threshold_ms': round(gap_threshold, 3),
        'sampling_gap_count': sampling_gap_count,
        'negative_jump_count': negative_count,
        'negative_jump_abs_median_pulses': round(neg_center, 3) if neg_center is not None else None,
        'negative_jump_abs_p95_pulses': round(negative_p95, 3) if negative_p95 is not None else None,
        'negative_jump_abs_max_pulses': round(negative_max, 3) if negative_max is not None else None,
        'negative_jump_outlier_threshold_pulses': round(negative_outlier_threshold, 3) if negative_outlier_threshold is not None else None,
        'negative_jump_outlier_candidate_count': negative_outlier_count,
        'large_negative_jump_candidate_count': large_negative_count,
        'positive_delta_outlier_threshold_pulses': round(positive_outlier_threshold, 3) if positive_outlier_threshold is not None else None,
        'positive_delta_outlier_candidate_count': positive_outlier_count,
        'flat_raw_candidate_count': len(flat_events),
        'longest_flat_raw_ms': longest_flat,
        'raw_filtered_abs_diff_median': round(median(filtered_error), 3) if filtered_error else None,
        'raw_filtered_abs_diff_max': max(filtered_error) if filtered_error else None,
    }
    result = {
        'scopex_role': 'business_facts',
        'schema': 2,
        'source': 'encoder-health',
        'window': {
            'start': args.start.strftime(TS_FMT) if args.start else None,
            'end': args.end.strftime(TS_FMT) if args.end else None,
        },
        'logs': [str(path) for path in logs],
        'per_file_matching_samples': per_file_matches,
        'config': {
            'invalid_min': args.invalid_min,
            'gap_factor': args.gap_factor,
            'gap_min_ms': args.gap_min_ms,
            'large_negative_pulses': args.large_negative_pulses,
            'flat_ms': args.flat_ms,
            'outlier_mad': args.outlier_mad,
        },
        'facts': facts,
        'top_candidates': top_candidates,
        'candidate_events_total': len(candidates),
        'semantics': {
            'negative_jump_count': 'all observed raw decreases; small decreases may be normal jitter and are summarized, not individually promoted',
            'negative_jump_outlier_candidate': 'statistically unusual raw decrease; root cause is not assigned',
            'large_negative_jump_candidate': 'raw decrease above configured profile threshold; may be reset, real reverse, or fault until context confirms',
            'positive_delta_outlier_candidate': 'statistically unusual positive raw delta, not automatically a physical spike',
            'flat_raw_candidate': 'raw remained unchanged for configured duration; may be normal stop or abnormal stall',
            'sampling_gap': 'timestamp gap above configured/data-driven threshold',
        },
    }
    if args.events_out:
        args.events_out.parent.mkdir(parents=True, exist_ok=True)
        args.events_out.write_text(json.dumps({'schema': 1, 'events': candidates}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        result['events_out'] = str(args.events_out)
    text = json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if valid else 1


if __name__ == '__main__':
    raise SystemExit(main())
