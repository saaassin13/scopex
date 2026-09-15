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

STAMP = r'(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}:\d{3})'
MAIN_RE = re.compile(
    r'^' + STAMP + r'.*EncoderVal \[(?P<count>-?\d+)\], TurnTableSpeed '
    r'\[(?P<speed>-?[\d.]+) mm/s\]'
)
RAW_RE = re.compile(
    r'^' + STAMP + r'.*Get EncoderVal, raw\[(?P<raw>-?\d+)\], filtered\[(?P<filtered>-?\d+)\]'
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


def basic_stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {'n': 0, 'min': None, 'p05': None, 'median': None, 'p95': None, 'max': None}
    return {
        'n': len(values),
        'min': min(values),
        'p05': percentile(values, 0.05),
        'median': median(values),
        'p95': percentile(values, 0.95),
        'max': max(values),
    }


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


def _window_ok(ts: datetime, start: datetime | None, end: datetime | None) -> bool:
    return (start is None or ts >= start) and (end is None or ts < end)


def load_samples(logs: list[Path], start: datetime | None, end: datetime | None, invalid_min: int):
    main: list[dict[str, Any]] = []
    raw: list[dict[str, Any]] = []
    invalid_raw = 0
    per_file: dict[str, dict[str, int]] = {}
    for file_index, log in enumerate(logs):
        main_here = 0
        raw_here = 0
        with log.open(encoding='utf-8', errors='replace') as handle:
            for line_no, line in enumerate(handle, 1):
                m = MAIN_RE.match(line)
                if m:
                    ts = datetime.strptime(m.group('ts'), TS_FMT)
                    if _window_ok(ts, start, end):
                        main.append({
                            'ts': ts, 'ts_text': m.group('ts'), 'count': int(m.group('count')),
                            'speed': float(m.group('speed')), 'line_no': line_no,
                            'source': str(log), 'file_index': file_index,
                        })
                        main_here += 1
                    continue
                m = RAW_RE.match(line)
                if not m:
                    continue
                ts = datetime.strptime(m.group('ts'), TS_FMT)
                if not _window_ok(ts, start, end):
                    continue
                value = int(m.group('raw'))
                invalid = value < 0 or value >= invalid_min
                if invalid:
                    invalid_raw += 1
                raw.append({
                    'ts': ts, 'ts_text': m.group('ts'), 'count': value,
                    'filtered': int(m.group('filtered')), 'invalid': invalid,
                    'line_no': line_no, 'source': str(log), 'file_index': file_index,
                })
                raw_here += 1
        per_file[str(log)] = {'application': main_here, 'raw_filtered': raw_here}
    main.sort(key=lambda row: (row['ts'], row['file_index'], row['line_no']))
    raw.sort(key=lambda row: (row['ts'], row['file_index'], row['line_no']))
    return main, raw, invalid_raw, per_file


def build_pairs(samples: list[dict[str, Any]], *, count_key: str = 'count') -> list[dict[str, Any]]:
    pairs = []
    for seq, (a, b) in enumerate(zip(samples, samples[1:])):
        if a.get('invalid') or b.get('invalid'):
            continue
        dt_ms = (b['ts'] - a['ts']).total_seconds() * 1000.0
        if dt_ms <= 0:
            continue
        pairs.append({
            'seq': seq, 'a': a, 'b': b, 'dt_ms': dt_ms,
            'delta': b[count_key] - a[count_key],
        })
    return pairs


def local_positive_profile(pairs: list[dict[str, Any]], index: int, gap_threshold: float, radius: int = 20):
    lo = max(0, index - radius)
    hi = min(len(pairs), index + radius + 1)
    values = [
        # Compare increments over the SAME elapsed time. A longer polling
        # interval at constant velocity must not look like a pulse spike.
        float(pairs[i]['delta']) * pairs[index]['dt_ms'] / pairs[i]['dt_ms'] for i in range(lo, hi)
        if i != index and pairs[i]['dt_ms'] <= gap_threshold and pairs[i]['delta'] > 0
    ]
    center = median(values)
    spread = mad(values, center)
    return center, spread


def anomaly_thresholds(center: float | None, spread: float | None) -> tuple[float, float]:
    # Relative/data-driven thresholds. These are candidate separators, not a
    # hardware specification. They intentionally avoid flagging every -1/-2
    # count fluctuation as a business anomaly.
    if center is None or center <= 0:
        return 5.0, 20.0
    robust = max(spread or 0.0, 1.0)
    reverse = max(5.0, center * 0.25, robust * 6.0)
    positive_spike = max(20.0, center * 3.0, center + robust * 8.0)
    return reverse, positive_spike


def motion_context(pairs, index, direction, gap_threshold, window_ms=2000.0):
    """Bounded neighboring motion, without inferring a physical cause."""
    chosen = []
    elapsed = 0.0
    previous = None
    while 0 <= index < len(pairs):
        p = pairs[index]
        if p['dt_ms'] > gap_threshold or (previous is not None and p['seq'] != previous + direction):
            break
        if elapsed + p['dt_ms'] > window_ms:
            break
        chosen.append(p)
        elapsed += p['dt_ms']
        previous = p['seq']
        index += direction
    if not chosen:
        return {'samples': 0, 'observed_ms': 0.0}
    rates = [p['delta'] * 1000.0 / p['dt_ms'] for p in chosen]
    return {
        'samples': len(chosen), 'observed_ms': round(elapsed, 3),
        'rate_counts_s_min': round(min(rates), 3),
        'rate_counts_s_median': round(statistics.median(rates), 3),
        'rate_counts_s_max': round(max(rates), 3),
        'stationary_fraction': round(sum(p['delta'] == 0 for p in chosen) / len(chosen), 3),
    }


def detect_count_events(samples: list[dict[str, Any]], *, flat_ms: float, gap_factor: float, gap_min_ms: float,
                        recovery_ms: float = 1000.0):
    pairs = build_pairs(samples)
    intervals = [p['dt_ms'] for p in pairs]
    median_dt = median(intervals)
    gap_threshold = max(gap_min_ms, (median_dt or gap_min_ms) * gap_factor)

    events: list[dict[str, Any]] = []
    gap_count = 0
    for p in pairs:
        if p['dt_ms'] > gap_threshold:
            gap_count += 1
            events.append({
                'type': 'sampling_gap', 'at': p['b']['ts_text'], 'dt_ms': round(p['dt_ms'], 3),
                'threshold_ms': round(gap_threshold, 3),
                'source_from': p['a']['source'], 'source_to': p['b']['source'],
                'line_from': p['a']['line_no'], 'line_to': p['b']['line_no'],
            })

    # Consecutive negative deltas are grouped. Tiny groups are retained only as
    # telemetry; significant groups become reverse/glitch candidates.
    negative_pair_indices = [i for i, p in enumerate(pairs) if p['dt_ms'] <= gap_threshold and p['delta'] < 0]
    groups: list[list[int]] = []
    for index in negative_pair_indices:
        if groups and index == groups[-1][-1] + 1 and pairs[index]['a'] is pairs[groups[-1][-1]]['b']:
            groups[-1].append(index)
        else:
            groups.append([index])

    significant_reverse = 0
    reverse_glitch = 0
    reverse_interval = 0
    reverse_step = 0
    small_negative_groups = 0
    recovery_indices: set[int] = set()
    max_reverse = 0.0

    for group in groups:
        first_i, last_i = group[0], group[-1]
        first, last = pairs[first_i], pairs[last_i]
        drop = sum(pairs[i]['delta'] for i in group)
        magnitude = abs(float(drop))
        center, spread = local_positive_profile(pairs, first_i, gap_threshold)
        reverse_threshold, _ = anomaly_thresholds(center, spread)
        if magnitude < reverse_threshold:
            small_negative_groups += 1
            continue
        max_reverse = max(max_reverse, magnitude)

        recovery = 0.0
        recovery_elapsed = 0.0
        used_recovery: list[int] = []
        for j in range(last_i + 1, len(pairs)):
            p = pairs[j]
            # build_pairs omits invalid/nonpositive-time pairs but preserves
            # their original sequence numbers. Recovery must not cross a hole.
            if (p['seq'] != pairs[j - 1]['seq'] + 1
                    or p['dt_ms'] > gap_threshold or p['delta'] < 0
                    or recovery_elapsed + p['dt_ms'] > recovery_ms):
                break
            recovery_elapsed += p['dt_ms']
            if p['delta'] > 0:
                recovery += float(p['delta'])
                used_recovery.append(j)
                if recovery >= magnitude * 0.8:
                    break
        recovered = recovery >= magnitude * 0.8
        if recovered:
            recovery_indices.update(used_recovery)
        significant_reverse += 1
        if len(group) == 1 and recovered:
            event_type = 'reverse_glitch_candidate'
            reverse_glitch += 1
        elif len(group) >= 2:
            event_type = 'reverse_interval_candidate'
            reverse_interval += 1
        else:
            event_type = 'reverse_step_candidate'
            reverse_step += 1
        events.append({
            'type': event_type,
            'start': first['b']['ts_text'],
            'end': last['b']['ts_text'],
            'steps': len(group),
            'count_before': first['a']['count'],
            'count_end': last['b']['count'],
            'pulse_delta': int(drop),
            'abs_pulse_drop': magnitude,
            'duration_ms': round(sum(pairs[i]['dt_ms'] for i in group), 3),
            'mean_rate_counts_s': round(drop * 1000.0 / sum(pairs[i]['dt_ms'] for i in group), 3),
            'recovery_pulses': round(recovery, 3),
            'recovery_window_ms': recovery_ms,
            'recovery_observed_ms': round(recovery_elapsed, 3),
            'recovery_target_fraction': 0.8,
            'recovered': recovered,
            'local_positive_median': round(center, 3) if center is not None else None,
            'candidate_threshold_pulses': round(reverse_threshold, 3),
            'source_start': first['a']['source'],
            'source_end': last['b']['source'],
            'line_start': first['a']['line_no'],
            'line_end': last['b']['line_no'],
            'motion_before': motion_context(pairs, first_i - 1, -1, gap_threshold)
                if first_i > 0 and pairs[first_i - 1]['seq'] == first['seq'] - 1 else {'samples': 0},
            'motion_after': motion_context(pairs, last_i + 1, 1, gap_threshold)
                if last_i + 1 < len(pairs) and pairs[last_i + 1]['seq'] == last['seq'] + 1 else {'samples': 0},
        })

    positive_spikes = 0
    max_positive_spike = 0.0
    for i, p in enumerate(pairs):
        if p['dt_ms'] > gap_threshold or p['delta'] <= 0 or i in recovery_indices:
            continue
        center, spread = local_positive_profile(pairs, i, gap_threshold)
        _, threshold = anomaly_thresholds(center, spread)
        if p['delta'] <= threshold:
            continue
        positive_spikes += 1
        max_positive_spike = max(max_positive_spike, float(p['delta']))
        events.append({
            'type': 'positive_spike_candidate',
            'at': p['b']['ts_text'],
            'count_before': p['a']['count'],
            'count_after': p['b']['count'],
            'pulse_delta': p['delta'],
            'dt_ms': round(p['dt_ms'], 3),
            'rate_counts_s': round(p['delta'] * 1000.0 / p['dt_ms'], 3),
            'local_positive_median': round(center, 3) if center is not None else None,
            'candidate_threshold_pulses': round(threshold, 3),
            'motion_before': motion_context(pairs, i - 1, -1, gap_threshold)
                if i > 0 and pairs[i - 1]['seq'] == p['seq'] - 1 else {'samples': 0},
            'motion_after': motion_context(pairs, i + 1, 1, gap_threshold)
                if i + 1 < len(pairs) and pairs[i + 1]['seq'] == p['seq'] + 1 else {'samples': 0},
            'source_from': p['a']['source'], 'source_to': p['b']['source'],
            'line_from': p['a']['line_no'], 'line_to': p['b']['line_no'],
        })

    flat_events = []
    start_i: int | None = None
    for i, p in enumerate(pairs):
        contiguous = p['dt_ms'] <= gap_threshold
        follows_previous = i == 0 or p['seq'] == pairs[i - 1]['seq'] + 1
        # Equal values on either side of an invalid/time boundary do not
        # establish one uninterrupted flat interval. Flush, then start anew.
        if start_i is not None and (not follows_previous or not contiguous or p['delta'] != 0):
            first, last = pairs[start_i], pairs[i - 1]
            duration = (last['b']['ts'] - first['a']['ts']).total_seconds() * 1000.0
            if duration >= flat_ms:
                flat_events.append({
                    'type': 'flat_count_candidate', 'start': first['a']['ts_text'], 'end': last['b']['ts_text'],
                    'duration_ms': round(duration, 3), 'count': first['a']['count'],
                    'source_start': first['a']['source'], 'source_end': last['b']['source'],
                    'line_start': first['a']['line_no'], 'line_end': last['b']['line_no'],
                })
            start_i = None
        if contiguous and p['delta'] == 0 and start_i is None:
            start_i = i
    if start_i is not None and pairs:
        first, last = pairs[start_i], pairs[-1]
        duration = (last['b']['ts'] - first['a']['ts']).total_seconds() * 1000.0
        if duration >= flat_ms:
            flat_events.append({
                'type': 'flat_count_candidate', 'start': first['a']['ts_text'], 'end': last['b']['ts_text'],
                'duration_ms': round(duration, 3), 'count': first['a']['count'],
                'source_start': first['a']['source'], 'source_end': last['b']['source'],
                'line_start': first['a']['line_no'], 'line_end': last['b']['line_no'],
            })
    events.extend(flat_events)

    raw_negative_steps = sum(1 for p in pairs if p['dt_ms'] <= gap_threshold and p['delta'] < 0)
    delta_values = [float(p['delta']) for p in pairs if p['dt_ms'] <= gap_threshold]
    facts = {
        'samples': sum(not row.get('invalid') for row in samples),
        'median_sample_dt_ms': round(median_dt, 3) if median_dt is not None else None,
        'sample_gap_threshold_ms': round(gap_threshold, 3),
        'sampling_gap_count': gap_count,
        'signed_delta_min': min(delta_values) if delta_values else None,
        'signed_delta_median': round(median(delta_values), 3) if delta_values else None,
        'signed_delta_max': max(delta_values) if delta_values else None,
        'negative_steps_observed': raw_negative_steps,
        'small_negative_groups_ignored': small_negative_groups,
        'significant_reverse_event_count': significant_reverse,
        'reverse_glitch_candidate_count': reverse_glitch,
        'reverse_interval_candidate_count': reverse_interval,
        'reverse_step_candidate_count': reverse_step,
        'positive_spike_candidate_count': positive_spikes,
        'max_reverse_pulses': round(max_reverse, 3) if significant_reverse else None,
        'max_positive_spike_pulses': round(max_positive_spike, 3) if positive_spikes else None,
        'flat_count_candidate_count': len(flat_events),
        'longest_flat_count_ms': max((row['duration_ms'] for row in flat_events), default=None),
    }
    return facts, events, pairs, gap_threshold


def false_zero_speed_candidates(samples: list[dict[str, Any]], gap_threshold: float) -> list[dict[str, Any]]:
    pairs = build_pairs(samples)
    positives = [float(p['delta']) for p in pairs if p['dt_ms'] <= gap_threshold and p['delta'] > 0]
    typical = median(positives) or 0.0
    out = []
    for i in range(1, len(samples) - 1):
        previous = samples[i]['count'] - samples[i - 1]['count']
        following = samples[i + 1]['count'] - samples[i]['count']
        if (
            samples[i]['speed'] == 0.0
            and previous > typical * 0.5
            and following > typical * 0.5
            and abs(samples[i - 1]['speed']) > 20.0
            and abs(samples[i + 1]['speed']) > 20.0
        ):
            out.append({
                'type': 'zero_speed_with_rising_count_candidate',
                'at': samples[i]['ts_text'], 'previous_delta': previous, 'next_delta': following,
                'source': samples[i]['source'], 'line': samples[i]['line_no'],
            })
    return out


def severity(event: dict[str, Any]) -> float:
    if event['type'] == 'sampling_gap':
        return float(event.get('dt_ms') or 0.0) / max(1.0, float(event.get('threshold_ms') or 500.0))
    if event['type'] in {'reverse_glitch_candidate', 'reverse_interval_candidate', 'reverse_step_candidate'}:
        return float(event.get('abs_pulse_drop') or 0.0) / max(1.0, float(event.get('candidate_threshold_pulses') or 1.0))
    if event['type'] == 'positive_spike_candidate':
        return abs(float(event.get('pulse_delta') or 0.0)) / max(1.0, float(event.get('candidate_threshold_pulses') or 1.0))
    return 0.0


def select_events(events, maximum):
    """Keep global extrema visible, then rank screening candidates, never stops."""
    candidates = [e for e in events if e['type'] != 'flat_count_candidate']
    selected = []
    for key in ('abs_pulse_drop', 'pulse_delta', 'dt_ms'):
        eligible = [e for e in candidates if e.get(key, 0) > 0]
        if eligible:
            event = max(eligible, key=lambda e: e[key])
            if event not in selected:
                selected.append(event)
    selected.extend(e for e in sorted(candidates, key=severity, reverse=True) if e not in selected)
    return selected[:maximum]


def main() -> int:
    ap = argparse.ArgumentParser(description='Detect bounded encoder sampling gaps, local spikes, reverse/glitch events and flat intervals without assigning hardware root cause.')
    ap.add_argument('logs', nargs='*', type=Path, help='explicit rotated log files')
    ap.add_argument('--log-dir', type=Path, help='test/helper mode; product path should prefer data-locator explicit files')
    ap.add_argument('--start', type=parse_time)
    ap.add_argument('--end', type=parse_time)
    ap.add_argument('--invalid-min', type=int, default=2 ** 63)
    ap.add_argument('--gap-factor', type=float, default=5.0)
    ap.add_argument('--gap-min-ms', type=float, default=500.0)
    ap.add_argument('--flat-ms', type=float, default=1000.0)
    ap.add_argument('--recovery-ms', type=float, default=1000.0)
    ap.add_argument('--top-events', type=int, default=6)
    ap.add_argument('--inspect-events', type=Path, help='inspect saved events without rereading logs')
    ap.add_argument('--out', type=Path)
    ap.add_argument('--events-out', type=Path)
    args = ap.parse_args()

    if not 1 <= args.top_events <= 50 or not 0 < args.recovery_ms <= 10000:
        ap.error('--top-events must be 1..50 and --recovery-ms must be >0..10000')
    if args.inspect_events:
        if args.logs or args.log_dir or args.out or args.events_out:
            ap.error('--inspect-events does not accept logs or output paths')
        saved = json.loads(args.inspect_events.read_text(encoding='utf-8'))
        events = saved['events']
        if args.start:
            events = [e for e in events if parse_time(e.get('end') or e.get('at') or e['start']) >= args.start]
        if args.end:
            events = [e for e in events if parse_time(e.get('start') or e['at']) < args.end]
        print(json.dumps({'scopex_role': 'business_facts', 'source': 'encoder-health',
                          'observed_events_total': len(events),
                          'candidate_events_total': sum(e['type'] != 'flat_count_candidate' for e in events),
                          'top_candidates': select_events(events, args.top_events),
                          'limitations': ['Saved screening candidates only; not confirmed anomalies or physical causes.']},
                         ensure_ascii=False, separators=(',', ':')))
        return 0

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

    main_samples, raw_samples, invalid_raw, per_file = load_samples(logs, args.start, args.end, args.invalid_min)

    if len(main_samples) >= 2:
        primary_name = 'application_encoder'
        primary_samples = main_samples
    else:
        primary_name = 'raw_encoder'
        # Keep invalid records as continuity barriers. Filtering them here
        # manufactures deltas between previously non-adjacent valid readings.
        primary_samples = raw_samples

    # Filter only for coverage/count reporting and the empty-input exit status;
    # event detection always receives the original sequence with its barriers.
    valid_primary_samples = [row for row in primary_samples if not row.get('invalid')]
    facts, candidates, primary_pairs, gap_threshold = detect_count_events(
        primary_samples,
        flat_ms=args.flat_ms,
        gap_factor=args.gap_factor,
        gap_min_ms=args.gap_min_ms,
        recovery_ms=args.recovery_ms,
    ) if len(valid_primary_samples) >= 2 else ({'samples': len(valid_primary_samples)}, [], [], args.gap_min_ms)

    false_zero = false_zero_speed_candidates(main_samples, gap_threshold) if len(main_samples) >= 3 else []
    candidates.extend(false_zero)

    filtered_negative = 0
    raw_negative = 0
    raw_filtered_abs = []
    for a, b in zip(raw_samples, raw_samples[1:]):
        if a.get('invalid') or b.get('invalid'):
            continue
        dt_ms = (b['ts'] - a['ts']).total_seconds() * 1000.0
        if dt_ms <= 0 or dt_ms > gap_threshold:
            continue
        raw_delta = b['count'] - a['count']
        filtered_delta = b['filtered'] - a['filtered']
        raw_negative += int(raw_delta < 0)
        filtered_negative += int(filtered_delta < 0)
    for row in raw_samples:
        if not row.get('invalid'):
            raw_filtered_abs.append(abs(row['count'] - row['filtered']))

    facts.update({
        'primary_stream': primary_name,
        'samples_in_window': len(valid_primary_samples),
        'application_samples': len(main_samples),
        'raw_filtered_samples': len(raw_samples),
        'invalid_samples': invalid_raw,
        'first_ts': valid_primary_samples[0]['ts_text'] if valid_primary_samples else None,
        'last_ts': valid_primary_samples[-1]['ts_text'] if valid_primary_samples else None,
        'candidate_event_count': sum(row['type'] != 'flat_count_candidate' for row in candidates),
        'zero_speed_with_rising_count_candidate_count': len(false_zero),
        'raw_negative_steps': raw_negative,
        'filtered_negative_steps': filtered_negative,
        'raw_filtered_abs_diff_median': round(median([float(v) for v in raw_filtered_abs]), 3) if raw_filtered_abs else None,
        'raw_filtered_abs_diff_max': max(raw_filtered_abs) if raw_filtered_abs else None,
    })

    top_candidates = select_events(candidates, args.top_events)
    result = {
        'scopex_role': 'business_facts',
        'schema': 4,
        'source': 'encoder-health',
        'window': {
            'start': args.start.strftime(TS_FMT) if args.start else None,
            'end': args.end.strftime(TS_FMT) if args.end else None,
        },
        'logs': [str(path) for path in logs],
        'per_file_matching_samples': per_file,
        'facts': facts,
        'top_candidates': top_candidates,
        'observed_events_total': len(candidates),
        'candidate_events_total': facts['candidate_event_count'],
        'stationary_intervals': sorted((e for e in candidates if e['type'] == 'flat_count_candidate'),
                                       key=lambda e: e['duration_ms'], reverse=True)[:2],
        'limitations': [
            'Candidates are not confirmed anomalies. Normal starts, stops and mechanical rebound require motion-context interpretation.',
            'Neighbor motion uses at most 2 seconds each side; it is not an independent motor command or ground truth.',
            'Recovery checks a bounded elapsed-time window and 80% catch-up, not physical recovery or permanent non-recovery.',
        ],
        'semantics': {
            'reverse_glitch_candidate': 'isolated significant count decrease followed by near-term recovery; observed data glitch/reverse candidate, not proof of physical reversal',
            'reverse_interval_candidate': 'two or more consecutive significant negative increments; observed reverse-count interval, physical cause unresolved',
            'reverse_step_candidate': 'single significant negative increment without confirmed short recovery',
            'positive_spike_candidate': 'positive increment unusually large relative to nearby positive increments',
            'small_negative_groups_ignored': 'small negative groups below local data-driven threshold; telemetry only, not promoted as anomaly',
            'sampling_gap': 'timestamp gap above max(500 ms, 5x median interval) by default',
            'flat_count_candidate': 'count unchanged; may be normal stop, listed separately and excluded from candidate_event_count',
            'local_positive_median': 'neighbor positive increments normalized to the event first-step dt; a screening reference, not a normal operating limit',
        },
    }

    if args.events_out:
        args.events_out.parent.mkdir(parents=True, exist_ok=True)
        args.events_out.write_text(json.dumps({'schema': 3, 'events': candidates}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        result['events_out'] = str(args.events_out)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')

    print(json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
    return 0 if valid_primary_samples else 1


if __name__ == '__main__':
    raise SystemExit(main())
