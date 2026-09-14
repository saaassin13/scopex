#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import statistics
from typing import Any

RAW_RE = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}:\d{3}).*'
    r'Get EncoderVal, raw\[(?P<raw>-?\d+)\], filtered\[(?P<filtered>-?\d+)\]'
)
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


def main() -> int:
    ap = argparse.ArgumentParser(description='Analyze encoder sample health without assigning business root cause.')
    ap.add_argument('log', type=Path)
    ap.add_argument('--start', type=parse_time)
    ap.add_argument('--end', type=parse_time)
    ap.add_argument('--invalid-min', type=int, default=2 ** 63,
                    help='raw >= this value is treated as invalid/read-failure evidence')
    ap.add_argument('--gap-factor', type=float, default=3.0,
                    help='sampling gap candidate when dt exceeds factor × median dt')
    ap.add_argument('--gap-min-ms', type=float, default=100.0,
                    help='minimum absolute sampling-gap threshold')
    ap.add_argument('--large-negative-pulses', type=int, default=100000,
                    help='large negative jump candidate threshold; not automatically called reset')
    ap.add_argument('--flat-ms', type=float, default=1000.0,
                    help='unchanged raw duration candidate threshold')
    ap.add_argument('--outlier-mad', type=float, default=12.0,
                    help='absolute-delta statistical outlier threshold in MAD units')
    ap.add_argument('--max-events', type=int, default=200)
    ap.add_argument('--out', type=Path)
    args = ap.parse_args()

    if not args.log.is_file():
        ap.error(f'log does not exist: {args.log}')

    samples: list[dict[str, Any]] = []
    parsed_total = invalid_total = 0
    with args.log.open(encoding='utf-8', errors='replace') as f:
        for line_no, line in enumerate(f, 1):
            m = RAW_RE.match(line)
            if not m:
                continue
            parsed_total += 1
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
            samples.append({
                'ts': ts, 'ts_text': m.group('ts'), 'raw': raw, 'filtered': filtered,
                'line_no': line_no, 'invalid': invalid,
            })

    valid = [s for s in samples if not s['invalid']]
    pairs = []
    for sample_seq, (a, b) in enumerate(zip(samples, samples[1:])):
        # Invalid/read-failure samples are hard continuity breaks. Do not bridge
        # the normal samples on either side into one synthetic interval.
        if a['invalid'] or b['invalid']:
            continue
        dt_ms = (b['ts'] - a['ts']).total_seconds() * 1000.0
        if dt_ms <= 0:
            continue
        pairs.append({
            'sample_seq': sample_seq,
            'a': a, 'b': b, 'dt_ms': dt_ms,
            'delta_raw': b['raw'] - a['raw'],
            'delta_filtered': b['filtered'] - a['filtered'],
        })

    dt_values = [p['dt_ms'] for p in pairs]
    median_dt = median(dt_values)
    gap_threshold = max(args.gap_min_ms, (median_dt or args.gap_min_ms) * args.gap_factor)

    abs_deltas = [abs(float(p['delta_raw'])) for p in pairs if p['delta_raw'] != 0]
    delta_center = median(abs_deltas)
    delta_mad = mad(abs_deltas, delta_center)
    if delta_center is None:
        outlier_threshold = None
    else:
        scale = max(delta_mad or 0.0, 1.0)
        outlier_threshold = delta_center + args.outlier_mad * scale

    events: list[dict[str, Any]] = []
    for p in pairs:
        a, b = p['a'], p['b']
        base = {
            'ts': b['ts_text'], 'line_from': a['line_no'], 'line_to': b['line_no'],
            'raw_from': a['raw'], 'raw_to': b['raw'],
            'delta_raw': p['delta_raw'], 'dt_ms': round(p['dt_ms'], 3),
        }
        if p['dt_ms'] > gap_threshold:
            events.append({'type': 'sampling_gap', **base})
        if p['delta_raw'] < 0:
            kind = 'large_negative_jump_candidate' if abs(p['delta_raw']) >= args.large_negative_pulses else 'negative_jump'
            events.append({'type': kind, **base})
        elif outlier_threshold is not None and p['delta_raw'] > outlier_threshold:
            events.append({'type': 'positive_delta_outlier_candidate', **base,
                           'statistical_threshold_pulses': round(outlier_threshold, 3)})

    flat_events = []
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
                'start': p0['a']['ts_text'], 'end': pend['b']['ts_text'],
                'duration_ms': round(duration, 3), 'raw': p0['a']['raw'],
                'line_start': p0['a']['line_no'], 'line_end': pend['b']['line_no'],
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

    events.extend(flat_events)
    events.sort(key=lambda e: (e.get('ts') or e.get('start') or '', e['type']))

    filtered_error = [abs(s['raw'] - s['filtered']) for s in valid]
    result = {
        'schema': 1,
        'log': str(args.log),
        'window': {
            'start': args.start.strftime(TS_FMT) if args.start else None,
            'end': args.end.strftime(TS_FMT) if args.end else None,
        },
        'config': {
            'invalid_min': args.invalid_min,
            'gap_factor': args.gap_factor,
            'gap_min_ms': args.gap_min_ms,
            'large_negative_pulses': args.large_negative_pulses,
            'flat_ms': args.flat_ms,
            'outlier_mad': args.outlier_mad,
        },
        'summary': {
            'matching_lines_in_full_log': parsed_total,
            'samples_in_window': len(samples),
            'valid_samples': len(valid),
            'invalid_samples': invalid_total,
            'first_ts': valid[0]['ts_text'] if valid else None,
            'last_ts': valid[-1]['ts_text'] if valid else None,
            'median_sample_dt_ms': round(median_dt, 3) if median_dt is not None else None,
            'sample_gap_threshold_ms': round(gap_threshold, 3),
            'absolute_delta_median_pulses': round(delta_center, 3) if delta_center is not None else None,
            'absolute_delta_mad_pulses': round(delta_mad, 3) if delta_mad is not None else None,
            'positive_delta_outlier_threshold_pulses': round(outlier_threshold, 3) if outlier_threshold is not None else None,
            'raw_filtered_abs_diff_median': round(median(filtered_error), 3) if filtered_error else None,
            'raw_filtered_abs_diff_max': max(filtered_error) if filtered_error else None,
        },
        'event_counts': {},
        'events_truncated': len(events) > args.max_events,
        'events': events[:args.max_events],
        'semantics': {
            'negative_jump': 'observed raw decrease; cause not assigned',
            'large_negative_jump_candidate': 'large raw decrease; may be reset, real reverse, or fault until log context confirms',
            'positive_delta_outlier_candidate': 'statistical raw-delta outlier, not automatically a physical spike',
            'flat_raw_candidate': 'raw remained unchanged for configured duration; may be normal stop or abnormal stall',
            'sampling_gap': 'timestamp gap above configured/data-driven threshold',
        },
    }
    for event in events:
        kind = event['type']
        result['event_counts'][kind] = result['event_counts'].get(kind, 0) + 1

    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + '\n', encoding='utf-8')
    return 0 if valid else 1


if __name__ == '__main__':
    raise SystemExit(main())
