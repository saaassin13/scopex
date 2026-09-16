#!/usr/bin/env python3
from __future__ import annotations

import argparse
from bisect import bisect_left
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
ACCESS_LOG = Path(os.environ.get('SCOPEX_DATA_ACCESS_LOG', '/task-scratch/data-access.jsonl'))


def parse_time(text: str) -> datetime:
    try:
        return datetime.strptime(text, TS_FMT)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f'invalid timestamp: {text}') from exc


def record_data_access(payload: dict[str, Any]) -> None:
    if not ACCESS_LOG.parent.is_dir():
        return
    line = json.dumps({'schema': 1, **payload}, ensure_ascii=False, separators=(',', ':')) + '\n'
    fd = os.open(ACCESS_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, 'a', encoding='utf-8') as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


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


def rounded_stats(values: list[float]) -> dict[str, float | int | None]:
    return {
        key: round(value, 3) if isinstance(value, float) else value
        for key, value in basic_stats(values).items()
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
                filtered = int(m.group('filtered'))
                invalid = value < 0 or value >= invalid_min or filtered < 0 or filtered >= invalid_min
                if invalid:
                    invalid_raw += 1
                raw.append({
                    'ts': ts, 'ts_text': m.group('ts'), 'count': value,
                    'filtered': filtered, 'invalid': invalid,
                    'line_no': line_no, 'source': str(log), 'file_index': file_index,
                })
                raw_here += 1
        per_file[str(log)] = {'application': main_here, 'raw_filtered': raw_here}
    main.sort(key=lambda row: (row['ts'], row['file_index'], row['line_no']))
    raw.sort(key=lambda row: (row['ts'], row['file_index'], row['line_no']))
    return main, raw, invalid_raw, per_file


def is_near_zero_counter_boundary(a: dict[str, Any], b: dict[str, Any], *, count_key: str = 'count') -> bool:
    """Recognize a large accumulated count returning close to zero in one sample.

    This is a continuity boundary, not proof of why the counter restarted and
    not a hardware-health judgement. Keep the rule deliberately structural so
    ordinary reverse movement remains available to motion analysis.
    """
    before, after = a[count_key], b[count_key]
    return before > after and (after == 0 or (0 < after <= 1_000 and before - after >= 10_000))


def counter_followup(samples, index, *, count_key='count'):
    """Describe bounded observations, without assigning a reset or hardware cause."""
    a, b = samples[index:index + 2]
    observed = []
    previous = b
    for row in samples[index + 2:]:
        dt = (row['ts'] - previous['ts']).total_seconds() * 1000
        elapsed = (row['ts'] - b['ts']).total_seconds() * 1000
        if row.get('invalid') or dt <= 0 or dt > 500 or elapsed > 2000:
            break
        observed.append(row)
        previous = row
    drop = a[count_key] - b[count_key]
    # A return to the old value through ordinary accumulation is not a glitch.
    # Only an immediate, disproportionate return is kept connected for the
    # stricter two-sided off-trend detector.
    next_count = observed[0][count_key] if observed else None
    later_rates = [abs((y[count_key] - x[count_key]) / (y['ts'] - x['ts']).total_seconds())
                   for x, y in zip(observed, observed[1:])]
    return_rate = ((next_count - b[count_key]) / (observed[0]['ts'] - b['ts']).total_seconds()) if observed else 0
    immediate_return = bool(observed and next_count >= a[count_key] - .2 * drop and
                            return_rate > 8 * max(1, statistics.median(later_rates) if later_rates else 1))
    accumulating = len(observed) >= 2 and all(y[count_key] >= x[count_key]
                      for x, y in zip([b] + observed, observed))
    return {
        'pattern': ('return_toward_previous_level' if immediate_return else
                    'restart_or_stop_compatible' if accumulating else 'unresolved'),
        'following_samples': len(observed),
        'observed_ms': (observed[-1]['ts'] - b['ts']).total_seconds() * 1000 if observed else 0,
        'return_after_ms': (observed[0]['ts'] - b['ts']).total_seconds() * 1000 if immediate_return else None,
        'next_count': next_count,
        'last_count': observed[-1][count_key] if observed else None,
        'limits': 'At most 2000ms, stops at invalid samples or gaps above 500ms. Reset and subsequent accumulation/stop are allowed; this pattern alone is not a fault or proof of reset.',
    }


def counter_continuity_boundaries(samples: list[dict[str, Any]], *, count_key: str = 'count') -> list[dict[str, Any]]:
    boundaries = []
    for index, (a, b) in enumerate(zip(samples, samples[1:])):
        if a.get('invalid') or b.get('invalid') or not is_near_zero_counter_boundary(a, b, count_key=count_key):
            continue
        dt_ms = (b['ts'] - a['ts']).total_seconds() * 1000.0
        if dt_ms <= 0:
            continue
        boundaries.append({
            'type': 'counter_near_zero_boundary', 'at': b['ts_text'],
            'followup': counter_followup(samples, index, count_key=count_key),
            'count_before': a[count_key], 'count_after': b[count_key],
            'pulse_delta': b[count_key] - a[count_key], 'dt_ms': round(dt_ms, 3),
            'source_from': a['source'], 'source_to': b['source'],
            'line_from': a['line_no'], 'line_to': b['line_no'],
            'interpretation': 'possible reset boundary (reset is allowed operation), not a reverse displacement or fault; isolated returns require off-trend checks',
        })
    return boundaries


def build_pairs(samples: list[dict[str, Any]], *, count_key: str = 'count') -> list[dict[str, Any]]:
    pairs = []
    for seq, (a, b) in enumerate(zip(samples, samples[1:])):
        if a.get('invalid') or b.get('invalid'):
            continue
        if is_near_zero_counter_boundary(a, b, count_key=count_key):
            followup = counter_followup(samples, seq, count_key=count_key)
            # Keep an immediate return connected so off-trend detection can
            # inspect both sides. Other boundaries retain their own evidence.
            if not (followup['pattern'] == 'return_toward_previous_level' and
                    followup['next_count'] >= a[count_key] - 0.2 * (a[count_key] - b[count_key])):
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
    time_pairs = [{'a': a, 'b': b, 'dt_ms': (b['ts'] - a['ts']).total_seconds() * 1000}
                  for a, b in zip(samples, samples[1:]) if b['ts'] > a['ts']]
    intervals = [p['dt_ms'] for p in time_pairs]
    median_dt = median(intervals)
    gap_threshold = max(gap_min_ms, (median_dt or gap_min_ms) * gap_factor)

    events: list[dict[str, Any]] = []
    gap_count = 0
    for p in time_pairs:
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


def motion_episodes(samples, gap_threshold, settle_ms=2000.0):
    """Describe sign-change episodes; grouping time is NOT a normality limit."""
    pairs = build_pairs(samples)
    blocks = []
    for p in pairs:
        if p['dt_ms'] > gap_threshold:
            continue
        if not blocks or p['seq'] != blocks[-1][-1]['seq'] + 1:
            blocks.append([])
        blocks[-1].append(p)
    episodes = []
    for block in blocks:
        rates = [p['delta'] * 1000 / p['dt_ms'] for p in block]
        # Isolated off-trend points must rejoin the same trend on BOTH sides.
        jumps = {}
        for i in range(2, len(block) - 3):
            left, right = rates[i - 2:i], rates[i + 2:i + 4]
            baseline = statistics.median(left + right)
            scale = max(1.0, abs(baseline) * .1, (mad(left + right) or 0) * 6)
            p, q = block[i:i + 2]
            bridge = (p['delta'] + q['delta']) * 1000 / (p['dt_ms'] + q['dt_ms'])
            if (p['delta'] * q['delta'] < 0
                    and abs(statistics.median(left) - statistics.median(right)) <= scale
                    and abs(bridge - baseline) <= scale
                    and abs(p['delta'] - baseline * p['dt_ms'] / 1000) > 2
                    and min(abs(rates[i] - baseline), abs(rates[i + 1] - baseline)) > 8 * scale):
                jumps[i] = abs(p['delta'] - baseline * p['dt_ms'] / 1000)
        transitions = set()
        # Include starts/stops and abrupt persistent changes, even without reverse.
        times = [p['a']['ts'] for p in block]
        for i in range(1, len(block) - 1):
            if (block[i - 1]['delta'] == 0) != (block[i]['delta'] == 0):
                transitions.add(i)
            if abs(rates[i] - rates[i - 1]) <= max(1.0, abs(rates[i - 1]) * .5):
                continue
            left = bisect_left(times, times[i] - timedelta(seconds=1))
            right = bisect_left(times, times[i] + timedelta(seconds=1))
            if i - left < 3 or right - i < 3:
                continue
            a, b = rates[left:i], rates[i:right]
            center = statistics.median(a)
            scale = max(1.0, abs(center) * .1, (mad(a) or 0) * 6)
            if abs(statistics.median(b) - center) > 8 * scale:
                transitions.add(i)
        seeds = sorted(set(i for i, p in enumerate(block) if p['delta'] < 0) | set(jumps) | transitions)
        groups = []
        for i in seeds:
            if groups and (block[i]['a']['ts'] - block[groups[-1][-1]]['b']['ts']).total_seconds() * 1000 < settle_ms:
                groups[-1].append(i)
            else:
                groups.append([i])
        for group in groups:
            first, last = group[0], group[-1]
            if last in jumps:
                last += 1
            lo, hi = first, last
            while lo > 0 and (block[first]['a']['ts'] - block[lo - 1]['a']['ts']).total_seconds() * 1000 <= settle_ms:
                lo -= 1
            while hi + 1 < len(block) and (block[hi + 1]['b']['ts'] - block[last]['b']['ts']).total_seconds() * 1000 <= settle_ms:
                hi += 1
            selected = block[lo:hi + 1]
            rows = [selected[0]['a']] + [p['b'] for p in selected]
            core = block[first:last + 1]
            # Drawdown measures peak-to-trough displacement, not sum of all oscillations.
            peak, drawdown = core[0]['a']['count'], 0
            reverse_lobes, lobe = [], 0
            for p in core:
                peak = max(peak, p['b']['count'])
                drawdown = max(drawdown, peak - p['b']['count'])
                if p['delta'] < 0:
                    lobe -= p['delta']
                elif lobe:
                    reverse_lobes.append(lobe)
                    lobe = 0
            if lobe:
                reverse_lobes.append(lobe)
            before, after = block[lo:first], block[last + 1:hi + 1]
            def context(part):
                if not part:
                    return {'observed_ms': 0, 'state': 'unobserved'}
                duration = sum(p['dt_ms'] for p in part)
                zero_ms = sum(p['dt_ms'] for p in part if p['delta'] == 0)
                speeds = [p['delta'] * 1000 / p['dt_ms'] for p in part]
                return {'observed_ms': round(duration, 3),
                        'state': 'stationary' if zero_ms == duration else 'reverse' if max(speeds) <= 0 else 'mixed' if min(speeds) < 0 else 'forward',
                        'rate_first': round(speeds[0], 3), 'rate_last': round(speeds[-1], 3),
                        'rate_median': round(statistics.median(speeds), 3)}
            before_context, after_context = context(before), context(after)
            core_rates = [p['delta'] * 1000 / p['dt_ms'] for p in core]
            core_rate_stats = rounded_stats(core_rates)
            material_signs = []
            for offset, pair in enumerate(core):
                center, spread = local_positive_profile(block, first + offset, gap_threshold)
                movement_threshold, _ = anomaly_thresholds(center, spread)
                if abs(pair['delta']) >= movement_threshold:
                    material_signs.append(1 if pair['delta'] > 0 else -1)
            material_direction_changes = sum(a != b for a, b in zip(material_signs, material_signs[1:]))
            lobes_decreasing = (len(reverse_lobes) >= 2
                                and all(a > b for a, b in zip(reverse_lobes, reverse_lobes[1:])))
            rebound_supported = (lobes_decreasing
                                 and before_context['state'] in {'forward', 'mixed'}
                                 and after_context['state'] == 'stationary')
            episodes.append({
                'id': f'M{len(episodes) + 1}',
                'start': core[0]['a']['ts_text'], 'end': core[-1]['b']['ts_text'],
                'duration_ms': round(sum(p['dt_ms'] for p in core), 3),
                'drawdown_counts': drawdown,
                'motion_pattern': ('off_trend_return' if any(i in jumps for i in group) else
                                   'reverse_motion' if all(p['delta'] <= 0 for p in core) else
                                   'mixed_direction_motion' if reverse_lobes else 'forward_or_stop_transition'),
                'rate_transition_observed': any(i in transitions for i in group),
                'rate_counts_s': {key: core_rate_stats[key] for key in ('min', 'median', 'max')},
                'material_direction_change_count': material_direction_changes,
                'reverse_duration_ms': round(sum(p['dt_ms'] for p in core if p['delta'] < 0), 3),
                'negative_lobes': reverse_lobes,
                'lobes_decreasing': lobes_decreasing,
                'rebound_supported': rebound_supported,
                'off_trend_return_counts': max((jumps.get(i, 0) for i in group), default=0),
                'before': before_context, 'after': after_context,
                'context_complete': sum(p['dt_ms'] for p in before) >= settle_ms * .9 and sum(p['dt_ms'] for p in after) >= settle_ms * .9,
                'series': [[r['ts_text'], r['count'], r['source'], r['line_no']] for r in rows],
            })
    for episode in episodes:
        def comparable_rate(other):
            a = abs(episode['before'].get('rate_median', 0))
            b = abs(other['before'].get('rate_median', 0))
            return (a == b == 0) or (min(a, b) > 0 and max(a, b) / min(a, b) <= 2)
        peers = [e for e in episodes if e is not episode and comparable_rate(e)
                 and e['motion_pattern'] == episode['motion_pattern']
                 and e['before']['state'] == episode['before']['state']
                 and e['after']['state'] == episode['after']['state']
                 and bool(e['off_trend_return_counts']) == bool(episode['off_trend_return_counts'])]
        comparisons = {}
        if len(peers) >= 5 and episode['off_trend_return_counts']:
            for key in ('off_trend_return_counts',):
                values = [e[key] for e in peers]
                center = statistics.median(values)
                scale = max(1.0, mad(values) * 1.4826)
                comparisons[key] = {'median': round(center, 3), 'mad_scale': round(scale, 3),
                                    'robust_deviation': round((episode[key] - center) / scale, 3)}
        peer_center = statistics.median([p['drawdown_counts'] for p in peers]) if peers else 0
        episode['comparison'] = {'peer_count': len(peers), 'reference': 'same_window_observed_not_verified_normal',
                                 'limits': 'No fault ranking by reverse displacement/duration; peers are not commanded motion cycles.',
                                 'features': comparisons,
                                 'examples': [{'id': e['id'], 'drawdown_counts': e['drawdown_counts'],
                                               'duration_ms': e['duration_ms']} for e in
                                              sorted(peers, key=lambda e: abs(e['drawdown_counts'] - peer_center))[:2]]}

    return episodes


def episode_view(episode, maximum=32):
    """Full-span envelope: preserve endpoints and per-bin extrema, never a prefix."""
    result = {k: v for k, v in episode.items() if k != 'series'}
    rows = episode['series']
    if len(rows) <= maximum:
        indices = list(range(len(rows)))
    else:
        indices = {0, len(rows) - 1}
        bins = (maximum - 2) // 2
        for i in range(bins):
            lo, hi = i * len(rows) // bins, (i + 1) * len(rows) // bins
            indices.add(min(range(lo, hi), key=lambda j: rows[j][1]))
            indices.add(max(range(lo, hi), key=lambda j: rows[j][1]))
        indices = sorted(indices)
    result['trace_columns'] = ['time', 'count']
    result['trace'] = [[rows[i][0], rows[i][1]] for i in indices]
    result['trace_coverage'] = {'original_samples': len(rows), 'shown': len(indices),
                              'method': 'full_span_bin_extrema', 'all_samples_shown': len(indices) == len(rows)}
    result['sources'] = list(dict.fromkeys(r[2] for r in rows))
    result['source_bounds'] = [[rows[0][2], rows[0][3]], [rows[-1][2], rows[-1][3]]]
    # Many oscillations must not create unbounded JSON; full details remain saved.
    result['negative_lobe_count'] = len(result['negative_lobes'])
    result['negative_lobes'] = result['negative_lobes'][:12]
    return result


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
    ap.add_argument('--motion-report', action='store_true')
    ap.add_argument('--episode', help='inspect one process ID from --inspect-events')
    ap.add_argument('--out', type=Path)
    ap.add_argument('--events-out', type=Path)
    args = ap.parse_args()

    if not 1 <= args.top_events <= 50 or not 0 < args.recovery_ms <= 10000:
        ap.error('--top-events must be 1..50 and --recovery-ms must be >0..10000')
    if args.inspect_events:
        if args.logs or args.log_dir or args.out or args.events_out:
            ap.error('--inspect-events does not accept logs or output paths')
        saved = json.loads(args.inspect_events.read_text(encoding='utf-8'))
        if args.episode:
            episode = next((e for e in saved.get('episodes', []) if e['id'] == args.episode), None)
            if episode is None:
                ap.error('episode not found; use an ID returned by the motion report')
            print(json.dumps(episode_view(episode, 64), ensure_ascii=False, separators=(',', ':')))
            return 0
        events = saved.get('events')
        if not isinstance(events, list):
            ap.error('motion report files require --episode with a returned process ID')
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

    if args.episode:
        ap.error('--episode requires --inspect-events')
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

    if args.start is not None and args.end is not None:
        record_data_access({
            'source': 'cowdisinfect_logs',
            'operation': 'analyze',
            'purpose': 'encoder_health',
            'data_kind': 'log',
            'content_filter': 'encoder',
            'start': args.start.strftime(TS_FMT),
            'end': args.end.strftime(TS_FMT),
            'source_files': [str(path) for path in logs],
        })

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
        'primary_invalid_samples': sum(bool(row.get('invalid')) for row in primary_samples),
        'raw_filtered_invalid_samples': invalid_raw,
        'first_ts': valid_primary_samples[0]['ts_text'] if valid_primary_samples else None,
        'last_ts': valid_primary_samples[-1]['ts_text'] if valid_primary_samples else None,
        'candidate_event_count': sum(row['type'] != 'flat_count_candidate' for row in candidates),
        'zero_speed_with_rising_count_candidate_count': len(false_zero),
        'raw_negative_steps': raw_negative,
        'filtered_negative_steps': filtered_negative,
        'raw_filtered_abs_diff_median': round(median([float(v) for v in raw_filtered_abs]), 3) if raw_filtered_abs else None,
        'raw_filtered_abs_diff_max': max(raw_filtered_abs) if raw_filtered_abs else None,
    })
    if main_samples:
        facts['reported_speed_mm_s'] = rounded_stats([float(row['speed']) for row in main_samples])

    counter_boundaries = counter_continuity_boundaries(primary_samples)
    facts['counter_boundary_count'] = len(counter_boundaries)

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

    episodes = motion_episodes(primary_samples, gap_threshold) if args.motion_report else []
    if episodes:
        raw_times = [r['ts'] for r in raw_samples]
        for episode in episodes:
            start_time, end_time = parse_time(episode['series'][0][0]), parse_time(episode['series'][-1][0])
            lo = bisect_left(raw_times, start_time)
            hi = bisect_left(raw_times, end_time + timedelta(microseconds=1))
            nearby = raw_samples[lo:hi]
            valid = [r for r in nearby if not r.get('invalid')]
            episode['raw_filtered_context'] = {
                'samples': len(valid), 'invalid_samples': len(nearby) - len(valid),
                'max_abs_difference': max((abs(r['count'] - r['filtered']) for r in valid), default=None),
                'raw_negative_steps': sum(p['delta'] < 0 for p in build_pairs(nearby) if p['dt_ms'] <= gap_threshold),
                'filtered_negative_steps': sum(p['delta'] < 0 for p in build_pairs(nearby, count_key='filtered') if p['dt_ms'] <= gap_threshold),
                'limits': 'Same time window, not independent sensors; filter delay is not automatically an error.',
            }

    if args.motion_report:
        def priority(e):
            deviations = [abs(v['robust_deviation']) for v in e['comparison']['features'].values()]
            return (bool(e['off_trend_return_counts']), max(deviations, default=0), e['drawdown_counts'])
        ordered = []
        # Preserve extremes as well as relative deviations; tiny return-to-trend
        # events must never crowd out the largest motion process.
        for key in ('drawdown_counts', 'off_trend_return_counts'):
            if episodes:
                e = max(episodes, key=lambda e: e[key])
                if e[key] > 0 and e not in ordered:
                    ordered.append(e)
        ordered.extend(e for e in sorted(episodes, key=priority, reverse=True) if e not in ordered)
        pattern_counts = {
            pattern: sum(e['motion_pattern'] == pattern for e in episodes)
            for pattern in ('off_trend_return', 'reverse_motion', 'mixed_direction_motion',
                            'forward_or_stop_transition')
        }
        result = {
            'scopex_role': 'business_facts', 'source': 'encoder-health', 'schema': 5,
            'facts': {k: facts[k] for k in ('primary_stream', 'samples_in_window', 'first_ts', 'last_ts',
                'median_sample_dt_ms', 'sampling_gap_count', 'primary_invalid_samples',
                'raw_filtered_invalid_samples', 'counter_boundary_count', 'signed_delta_min',
                'signed_delta_median', 'signed_delta_max', 'negative_steps_observed',
                'significant_reverse_event_count', 'reverse_glitch_candidate_count',
                'reverse_interval_candidate_count', 'reverse_step_candidate_count',
                'positive_spike_candidate_count', 'max_reverse_pulses',
                'max_positive_spike_pulses', 'reported_speed_mm_s', 'raw_negative_steps',
                'filtered_negative_steps') if k in facts},
            'episode_count': len(episodes),
            'episode_summary': {
                'pattern_counts': pattern_counts,
                'rebound_supported_count': sum(e['rebound_supported'] for e in episodes),
                'max_material_direction_change_count': max(
                    (e['material_direction_change_count'] for e in episodes), default=0),
                'expectedness_from_encoder_data':
                    'unresolved_without_command_or_verified_operating_context',
            },
            'counter_boundaries': counter_boundaries[:8],
            'counter_boundaries_omitted': max(0, len(counter_boundaries) - 8),
            'episodes': [episode_view(e, 8) for e in ordered[:3]],
            'episodes_omitted': max(0, len(episodes) - 3),
            'limitations': [
                'Observed deviations and motion groups are not physical-fault diagnoses. Same-window peers are not verified normal.',
                'A possible normal reverse or rebound explanation does not establish that an observed deviation is normal.',
                'Grouping uses 2000ms without reversal; this is not an allowable rebound duration.',
                'Starts/stops and abrupt rate transitions are observed patterns, not faults; slow drift and a uniformly faulty reference may be missed.',
                'Trace spans the full saved context but is reduced; do not infer absent fine-scale behavior.',
            ],
        }
    if args.events_out:
        args.events_out.parent.mkdir(parents=True, exist_ok=True)
        saved = ({'schema': 5, 'episodes': episodes, 'counter_boundaries': counter_boundaries}
                 if args.motion_report else {'schema': 4, 'events': candidates})
        args.events_out.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        result['events_out'] = str(args.events_out)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')

    print(json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
    return 0 if valid_primary_samples else 1


if __name__ == '__main__':
    raise SystemExit(main())
