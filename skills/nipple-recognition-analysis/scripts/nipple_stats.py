#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Iterable


def parse_time_value(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 1e12:
            number /= 1000.0
        return datetime.fromtimestamp(number).astimezone()
    text = str(value).strip().replace('Z', '+00:00')
    for candidate in (text, text.replace(' ', 'T')):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            pass
    for fmt in ('%Y-%m-%d %H:%M:%S:%f', '%Y%m%d-%H%M%S%f'):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise ValueError(f'unsupported timestamp: {value!r}')


def parse_cli_time(text: str) -> datetime:
    try:
        return parse_time_value(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def field(value: Any, path: str) -> Any:
    cur = value
    if not path:
        return cur
    for token in path.split('.'):
        if isinstance(cur, dict):
            if token not in cur:
                raise KeyError(path)
            cur = cur[token]
        elif isinstance(cur, list) and token.isdigit():
            index = int(token)
            if index >= len(cur):
                raise KeyError(path)
            cur = cur[index]
        else:
            raise KeyError(path)
    return cur


def extract_records(document: Any, records_field: str | None) -> list[dict[str, Any]]:
    value = field(document, records_field) if records_field else document
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def count_nipples(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError('boolean is not a nipple count')
    if isinstance(value, int):
        if value < 0:
            raise ValueError('negative nipple count')
        return value
    if isinstance(value, float):
        if value < 0 or not value.is_integer():
            raise ValueError('nipple count must be a non-negative integer')
        return int(value)
    if isinstance(value, list):
        return len(value)
    raise ValueError(f'unsupported nipple value: {type(value).__name__}')


def normalize_for_compare(dt: datetime, ref: datetime) -> datetime:
    if dt.tzinfo is not None and ref.tzinfo is None:
        return dt.replace(tzinfo=None)
    if dt.tzinfo is None and ref.tzinfo is not None:
        return dt.replace(tzinfo=ref.tzinfo)
    return dt


def in_window(ts: datetime, start: datetime | None, end: datetime | None) -> bool:
    if start is not None:
        ts0 = normalize_for_compare(ts, start)
        if ts0 < start:
            return False
    if end is not None:
        ts1 = normalize_for_compare(ts, end)
        if ts1 >= end:
            return False
    return True


def iter_json_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
        return
    for item in sorted(path.rglob('*.json')):
        if item.is_file():
            yield item


def cow_key(record: dict[str, Any], cow_fields: list[str]) -> str:
    parts = []
    for path in cow_fields:
        value = field(record, path)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f'empty cow identity field: {path}')
        parts.append(str(value))
    return '|'.join(parts)


def sort_time(row: dict[str, Any]) -> float:
    dt = row['_ts']
    return dt.timestamp()


def selected_true(value: Any) -> bool:
    # Do not treat non-empty strings such as "false" as true. Schema-specific
    # string conventions must be normalized upstream or explicitly added later.
    return value is True or value == 1


def choose(rows: list[dict[str, Any]], policy: str, selected_field: str | None) -> dict[str, Any] | None:
    if policy == 'selected':
        if not selected_field:
            raise ValueError('--selected-field is required for --policy selected')
        selected = []
        for row in rows:
            try:
                if selected_true(field(row['_record'], selected_field)):
                    selected.append(row)
            except KeyError:
                continue
        rows = selected
        if not rows:
            return None
    if policy == 'max':
        return max(rows, key=lambda row: (row['nipple_count'], sort_time(row)))
    return max(rows, key=sort_time)


def main() -> int:
    ap = argparse.ArgumentParser(description='Compute cow-level nipple recognition statistics from explicit JSON schema mappings.')
    ap.add_argument('input', type=Path, help='JSON file or directory')
    ap.add_argument('--records-field', help='dot path to a record list; omit when each JSON/root list is already records')
    ap.add_argument('--time-field', required=True, help='dot path to record timestamp')
    ap.add_argument('--cow-field', action='append', required=True, help='dot path forming cow identity; repeatable for composite key')
    ap.add_argument('--nipple-field', required=True, help='dot path to nipple count or nipple list')
    ap.add_argument('--selected-field', help='dot path to final/selected boolean marker')
    ap.add_argument('--policy', choices=('latest', 'max', 'selected'), required=True,
                    help='per-cow record selection; must reflect confirmed JSON semantics')
    ap.add_argument('--start', type=parse_cli_time)
    ap.add_argument('--end', type=parse_cli_time)
    ap.add_argument('--out', type=Path)
    args = ap.parse_args()

    if not args.input.exists():
        ap.error(f'input does not exist: {args.input}')

    observed_cows: set[str] = set()
    by_cow: dict[str, list[dict[str, Any]]] = {}
    file_count = record_count = 0
    malformed_files = missing_records_field_files = missing_fields = bad_values = 0
    for path in iter_json_files(args.input):
        file_count += 1
        try:
            document = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError):
            malformed_files += 1
            continue
        try:
            records = extract_records(document, args.records_field)
        except (KeyError, IndexError):
            missing_records_field_files += 1
            continue
        for record in records:
            record_count += 1
            try:
                ts = parse_time_value(field(record, args.time_field))
                if not in_window(ts, args.start, args.end):
                    continue
                key = cow_key(record, args.cow_field)
            except KeyError:
                missing_fields += 1
                continue
            except (ValueError, TypeError, OverflowError):
                bad_values += 1
                continue

            # A valid time + cow key establishes that this cow is represented in
            # the JSON window even if its nipple result is missing/bad. Keep this
            # denominator separate from result coverage so KPI cannot silently
            # improve by dropping cows without a usable selected result.
            observed_cows.add(key)
            try:
                nipple_count = count_nipples(field(record, args.nipple_field))
            except KeyError:
                missing_fields += 1
                continue
            except (ValueError, TypeError, OverflowError):
                bad_values += 1
                continue

            by_cow.setdefault(key, []).append({
                '_ts': ts,
                'ts': ts.isoformat(),
                'nipple_count': nipple_count,
                'source': str(path),
                '_record': record,
            })

    selected_rows = []
    for key, rows in sorted(by_cow.items()):
        try:
            picked = choose(rows, args.policy, args.selected_field)
        except ValueError as exc:
            ap.error(str(exc))
        if picked is None:
            continue
        selected_rows.append({
            'cow_id': key,
            'ts': picked['ts'],
            'nipple_count': picked['nipple_count'],
            'source': picked['source'],
        })

    selected_ids = {row['cow_id'] for row in selected_rows}
    cows_without_selected = sorted(observed_cows - selected_ids)
    distribution: dict[str, int] = {}
    for row in selected_rows:
        k = str(row['nipple_count'])
        distribution[k] = distribution.get(k, 0) + 1

    observed_total = len(observed_cows)
    selected_total = len(selected_rows)
    exactly_four = sum(1 for row in selected_rows if row['nipple_count'] == 4)
    over_four = sum(1 for row in selected_rows if row['nipple_count'] > 4)
    raw_nipples = sum(row['nipple_count'] for row in selected_rows)
    capped_nipples = sum(min(row['nipple_count'], 4) for row in selected_rows)
    selected_denominator = selected_total * 4
    observed_denominator = observed_total * 4

    result = {
        'schema': 1,
        'input': str(args.input),
        'window': {
            'start': args.start.isoformat() if args.start else None,
            'end': args.end.isoformat() if args.end else None,
        },
        'mapping': {
            'records_field': args.records_field,
            'time_field': args.time_field,
            'cow_fields': args.cow_field,
            'nipple_field': args.nipple_field,
            'selected_field': args.selected_field,
            'selection_policy': args.policy,
        },
        'quality': {
            'json_files': file_count,
            'records_seen': record_count,
            'malformed_files': malformed_files,
            'files_missing_records_field': missing_records_field_files,
            'records_missing_required_fields': missing_fields,
            'records_with_bad_values': bad_values,
            'cows_without_selected_result': cows_without_selected,
        },
        'summary': {
            # total_cows means cows represented by a valid time+cow key in JSON,
            # not only cows for which a usable final result happened to exist.
            'total_cows': observed_total,
            'cows_with_selected_result': selected_total,
            'selected_result_coverage_rate': round(selected_total / observed_total, 6) if observed_total else None,
            'exactly_four_cows': exactly_four,
            'over_four_cows': over_four,
            'complete_four_nipple_rate': round(exactly_four / observed_total, 6) if observed_total else None,
            'complete_four_nipple_rate_selected_only': round(exactly_four / selected_total, 6) if selected_total else None,
            'raw_detected_nipples': raw_nipples,
            'capped_detected_nipples': capped_nipples,
            'expected_nipples': observed_denominator,
            'selected_expected_nipples': selected_denominator,
            # Missing selected results contribute zero to the conservative main
            # KPI. The selected-only rate is also exposed so data-coverage issues
            # are never hidden by one denominator.
            'nipple_recognition_rate': round(capped_nipples / observed_denominator, 6) if observed_denominator else None,
            'nipple_recognition_rate_selected_only': round(capped_nipples / selected_denominator, 6) if selected_denominator else None,
            'distribution_by_selected_nipple_count': distribution,
        },
        'cows': selected_rows,
    }

    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + '\n', encoding='utf-8')
    return 0 if observed_total else 1


if __name__ == '__main__':
    raise SystemExit(main())
