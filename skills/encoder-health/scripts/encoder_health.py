#!/usr/bin/env python3
"""Raw encoder evidence tool. No inferred mechanical limits or application fallback."""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys
import tempfile

VERSION = "raw-1"
SCHEMA = 6
TS_FMT = "%Y-%m-%d %H:%M:%S:%f"
EPOCH = datetime(1970, 1, 1)
STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}:\d{3})(?!\d)")
RAW = re.compile(r"\bGet EncoderVal, raw\[(-?\d+)\]")
LOG = re.compile(r"^CowDisinfect-(\d{8})-(\d{6})\.log(?:\.\d+)?$")
CONTEXT = re.compile(
    r"ResetEncoderValOnSerialPort|Reset EncoderDevCounterVal|"
    r"Encoder is resetting|Read encoder value via master failed|"
    r"SetEncoderDevParam|Set EncoderModbusWorker stop run|"
    r"清零编码器脉冲采集模块计数值"
)
LIMITATIONS = [
    "Only raw readings are analyzed; application/filtered values are not substitutes.",
    "Trend deviations are candidates, not proof of faulty hardware or unintended motion.",
    "Unlogged resets, unknown integer representation and unobserved motion may be indistinguishable.",
    "Constant scale error and slow drift are not certified by this first-version detector.",
]


def parse_time(text):
    return datetime.strptime(text, TS_FMT)


def millis(text):
    return (parse_time(text) - EPOCH) // timedelta(milliseconds=1)


def stamp(ms):
    return (EPOCH + timedelta(milliseconds=ms)).strftime(TS_FMT)[:-3]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def save(path, value):
    """Atomic task-local checkpoint; never overwrite an input log."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".encoder-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(canonical(value) + "\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load(path):
    state = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("schema") != SCHEMA or state.get("version") != VERSION:
        raise ValueError("Unsupported saved analysis; rerun scan with the raw-only tool.")
    return state


def stats(values):
    if not values:
        return {"n": 0, "min": None, "median": None, "max": None}
    return {"n": len(values), "min": min(values), "median": statistics.median(values), "max": max(values)}


def profile_checked(profile):
    allowed = {"source", "count_min", "count_max", "unsigned_print_bits",
               "max_rate_counts_s", "timestamp_uncertainty_ms", "count_tolerance",
               "continuity_verified"}
    if not isinstance(profile, dict) or set(profile) - allowed:
        raise ValueError("Unknown profile fields")
    if profile and (not isinstance(profile.get("source"), str) or not profile["source"].strip()):
        raise ValueError("A verified profile requires a nonempty source")
    for key in ("count_min", "count_max", "unsigned_print_bits"):
        if key in profile and type(profile[key]) is not int:
            raise ValueError(f"{key} must be an integer")
    if profile.get("unsigned_print_bits") not in (None, 32, 64):
        raise ValueError("unsigned_print_bits must be 32 or 64")
    if profile.get("count_min", 0) > profile.get("count_max", profile.get("count_min", 0)):
        raise ValueError("count_min exceeds count_max")
    for key in ("max_rate_counts_s", "timestamp_uncertainty_ms", "count_tolerance"):
        if key in profile and (type(profile[key]) not in (int, float)
                               or not math.isfinite(profile[key]) or profile[key] < 0):
            raise ValueError(f"{key} must be finite and nonnegative")
    if "max_rate_counts_s" in profile:
        if not profile["max_rate_counts_s"] or "timestamp_uncertainty_ms" not in profile:
            raise ValueError("A rate limit requires a positive limit and timestamp_uncertainty_ms")
        if profile.get("continuity_verified") is not True:
            raise ValueError("A rate limit requires verified reset/wrap continuity")
    if "continuity_verified" in profile and type(profile["continuity_verified"]) is not bool:
        raise ValueError("continuity_verified must be boolean")
    return profile


def discover_logs(root, start, end):
    if start is None or end is None or end <= start or end - start > 48 * 3600000:
        raise ValueError("--log-dir requires a positive window of at most 48 hours")
    paths = []
    for path in Path(root).iterdir():
        match = LOG.fullmatch(path.name)
        if match and path.is_file():
            time = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
            hour = (time.replace(minute=0, second=0) - EPOCH) // timedelta(milliseconds=1)
            if hour < end and hour + 3600000 > start:
                paths.append(path)
    if len(paths) > 64:
        raise ValueError("Too many rotation files; narrow the window")
    return sorted(paths)


def scan(logs, start=None, end=None, profile=None, max_bytes=256 * 1024**2, max_samples=250000):
    profile = profile_checked(profile or {})
    if start is not None and end is not None and end <= start:
        raise ValueError("end must be after start")
    logs = sorted(set(Path(p).absolute() for p in logs))
    if not logs:
        raise ValueError("No input files")
    if len(logs) > 64:
        raise ValueError("capacity_exceeded: at most 64 explicit files per analysis")
    samples, context, issues, inputs = [], [], [], []
    total_bytes = 0

    def issue(kind, **data):
        issues.append({"kind": kind, **data})
        if len(issues) > max_samples:
            raise ValueError("capacity_exceeded: issue limit; no completed analysis")

    for path in logs:
        hasher = hashlib.sha256()
        previous, pending = None, False
        before_stat = path.stat()
        parsed = 0
        with path.open("rb") as handle:
            for number, raw_line in enumerate(handle, 1):
                total_bytes += len(raw_line)
                if total_bytes > max_bytes:
                    raise ValueError("capacity_exceeded: input byte limit; no completed analysis")
                hasher.update(raw_line)
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                raw_marker = "Get EncoderVal, raw" in line
                contextual = bool(CONTEXT.search(line))
                if not raw_marker and not contextual:
                    continue
                m = STAMP.match(line)
                try:
                    t = millis(m[1]) if m else None
                except ValueError:
                    t = None
                if t is not None and ((start is not None and t < start) or (end is not None and t >= end)):
                    continue
                source = {"source": str(path), "line_no": number}
                if t is None:
                    issue("unparsed_time", **source)
                    pending = True
                    continue
                if len(line) > 4096:
                    raise ValueError("capacity_exceeded: encoder evidence line exceeds 4096 characters")
                if contextual:
                    context.append({"t": t, "time": stamp(t), "text": line, **source})
                    if len(context) > max_samples:
                        raise ValueError("capacity_exceeded: context limit; no completed analysis")
                    pending = True
                if not raw_marker:
                    continue
                m = RAW.search(line)
                if not m:
                    issue("unparsed_raw", time=stamp(t), **source)
                    pending = True
                    continue
                printed = int(m[1])
                count = printed
                bits = profile.get("unsigned_print_bits")
                if bits and 0 <= printed < 2**bits and printed >= 2**(bits - 1):
                    count = printed - 2**bits
                invalid = (("count_min" in profile and count < profile["count_min"])
                           or ("count_max" in profile and count > profile["count_max"]))
                if previous is not None and t <= previous:
                    issue("non_increasing_source_time", time=stamp(t), **source)
                    pending = True
                samples.append({"t": t, "time": stamp(t), "count": count, "printed": str(printed),
                                "text": line, "invalid": invalid, "barrier": pending, **source})
                parsed += 1
                pending, previous = False, t
                if len(samples) > max_samples or len(context) > max_samples or len(issues) > max_samples:
                    raise ValueError("capacity_exceeded: evidence limit; no completed analysis")
        after_stat = path.stat()
        if (before_stat.st_size, before_stat.st_mtime_ns, before_stat.st_ino) != (
                after_stat.st_size, after_stat.st_mtime_ns, after_stat.st_ino):
            raise ValueError("input_changed: file rotated or changed during scan; use a stable copy")
        inputs.append({"path": str(path), "sha256": hasher.hexdigest(), "raw_samples": parsed,
                       "bytes": after_stat.st_size})
    samples.sort(key=lambda r: (r["t"], r["source"], r["line_no"]))
    context.sort(key=lambda r: (r["t"], r["source"], r["line_no"]))
    # A source clock rollback makes its ordered interval ambiguous even after sorting.
    affected = {r["source"] for r in issues if r["kind"] == "non_increasing_source_time"}
    for row in samples:
        if row["source"] in affected:
            row["barrier"] = True
    for a, b in zip(samples, samples[1:]):
        if a["t"] == b["t"]:
            a["barrier"] = b["barrier"] = True
            issue("duplicate_timestamp", time=b["time"], source=b["source"], line_no=b["line_no"])
    state = {"schema": SCHEMA, "version": VERSION, "profile": profile,
             "requested": {"start": stamp(start) if start is not None else None,
                           "end": stamp(end) if end is not None else None},
             "inputs": inputs, "samples": samples, "context": context, "issues": issues,
             "reviews": {}}
    state["analysis_id"] = digest({"inputs": inputs, "requested": state["requested"],
                                  "profile": profile, "version": VERSION})[:20]
    analyze(state)
    return state


def analyze(state):
    rows, profile = state["samples"], state["profile"]
    intervals = [b["t"] - a["t"] for a, b in zip(rows, rows[1:])
                 if b["t"] > a["t"] and not (a["barrier"] or b["barrier"])]
    median_dt = statistics.median(intervals) if intervals else None
    gap_ms = max(500, (median_dt or 100) * 5)
    context_times = [r["t"] for r in state["context"]]
    pairs, gaps, seeds = [], [], []

    def seed(lo, hi, kind):
        seeds.append({"lo": lo, "hi": hi, "kind": kind})

    for i, row in enumerate(rows):
        if row["invalid"]:
            seed(i, i, "profile_range_candidate")
    for i, (a, b) in enumerate(zip(rows, rows[1:])):
        dt = b["t"] - a["t"]
        ci, cj = bisect_right(context_times, a["t"]), bisect_right(context_times, b["t"])
        controls = ci < cj
        blocked = dt <= 0 or dt > gap_ms or a["barrier"] or b["barrier"] or a["invalid"] or b["invalid"] or controls
        if dt > gap_ms:
            gaps.append({"start": a["time"], "end": b["time"], "dt_ms": dt})
        if controls:
            seed(i, i + 1, "counter_or_acquisition_context")
        delta = b["count"] - a["count"]
        pairs.append({"lo": i, "hi": i + 1, "dt_ms": dt, "delta": delta,
                      "rate": delta * 1000 / dt if dt > 0 else None, "blocked": bool(blocked)})
        if not blocked and "max_rate_counts_s" in profile:
            lower_rate = max(0, abs(delta) - profile.get("count_tolerance", 0)) * 1000 / (
                dt + 2 * profile["timestamp_uncertainty_ms"])
            if lower_rate > profile["max_rate_counts_s"]:
                seed(i, i + 1, "profile_rate_candidate")

    # Bounded local references on both sides. Signed rates treat reverse symmetrically.
    local_checked, local_unchecked = 0, 0
    for i, pair in enumerate(pairs):
        if pair["blocked"]:
            continue
        left, right = [], []
        for j in range(i - 1, max(-1, i - 9), -1):
            if pairs[j]["blocked"]:
                break
            left.append(pairs[j]["rate"])
        for j in range(i + 1, min(len(pairs), i + 9)):
            if pairs[j]["blocked"]:
                break
            right.append(pairs[j]["rate"])
        reference = left + right
        if len(reference) < 3:
            local_unchecked += 1
            continue
        local_checked += 1
        center = statistics.median(reference)
        spread = statistics.median(abs(x - center) for x in reference)
        scale = max(2 * 1000 / pair["dt_ms"], abs(center) * .1, 6 * spread)
        residual = pair["delta"] - center * pair["dt_ms"] / 1000
        if abs(pair["rate"] - center) > 8 * scale and abs(residual) > 5:
            seed(i, i + 1, "local_rate_deviation")
        # Persistent changes are observations, not anomalies, including normal starts/stops.
        if len(left) >= 4 and len(right) >= 4:
            l, r = statistics.median(left[:4]), statistics.median(right[:4])
            noise = max(2 * 1000 / pair["dt_ms"],
                        statistics.median(abs(v - l) for v in left[:4]),
                        statistics.median(abs(v - r) for v in right[:4]))
            if abs(l - r) > max(8 * noise, .5 * max(abs(l), abs(r))):
                seed(i, i + 1, "rate_transition")
    seeds.sort(key=lambda s: (s["lo"], s["hi"], s["kind"]))
    candidates = []
    for s in seeds:
        if candidates and s["lo"] <= candidates[-1]["hi"]:
            c = candidates[-1]
            c["hi"] = max(c["hi"], s["hi"])
            c["kinds"] = sorted(set(c["kinds"] + [s["kind"]]))
        else:
            candidates.append({"lo": s["lo"], "hi": s["hi"], "kinds": [s["kind"]]})
    for i, c in enumerate(candidates):
        c.update(id=f"R{i + 1}", start=rows[c["lo"]]["time"], end=rows[c["hi"]]["time"])
    state["pairs"], state["gaps"], state["candidates"] = pairs, gaps, candidates
    state["facts"] = {"primary_stream": "raw_encoder", "samples_in_window": len(rows),
                      "first_ts": rows[0]["time"] if rows else None,
                      "last_ts": rows[-1]["time"] if rows else None,
                      "median_sample_dt_ms": median_dt, "gap_threshold_ms": gap_ms,
                      "sampling_gap_count": len(gaps),
                      "invalid_samples": sum(r["invalid"] for r in rows),
                      "local_pairs_checked": local_checked, "local_pairs_without_reference": local_unchecked,
                      "negative_steps_observed": sum(p["delta"] < 0 for p in pairs if not p["blocked"]),
                      "delta_counts": stats([p["delta"] for p in pairs if not p["blocked"]]),
                      "rate_counts_s": stats([p["rate"] for p in pairs if not p["blocked"]])}


def public_candidate(state, candidate):
    result = dict(candidate)
    result.pop("lo")
    result.pop("hi")
    result["review_status"] = state["reviews"].get(candidate["id"], {}).get("status", "unseen")
    return result


def report(state, offset=0, limit=5):
    reviewed = state["reviews"]
    confirmed = [v for v in reviewed.values() if v["status"] == "confirmed_raw_anomaly"]
    unresolved = [v for v in reviewed.values() if v["status"] == "unresolved"]
    unseen = sum(c["id"] not in reviewed for c in state["candidates"])
    quality = bool(state["issues"] or state["gaps"] or not state["samples"])
    requested, facts = state["requested"], state["facts"]
    quality = quality or bool(facts["local_pairs_without_reference"])
    uncovered_edges = []
    for key, actual in (("start", facts["first_ts"]), ("end", facts["last_ts"])):
        if requested[key] is not None and (actual is None or abs(millis(requested[key]) - millis(actual)) > facts["gap_threshold_ms"]):
            quality = True
            uncovered_edges.append({"edge": key, "requested": requested[key], "observed": actual})
    if confirmed:
        status = "confirmed_raw_anomaly"
    elif not state["samples"] and not state["issues"]:
        status = "no_raw_data"
    elif unresolved or unseen or quality or len(state["samples"]) < 3:
        status = "inconclusive"
    else:
        status = "no_anomaly_evidence_in_completed_checks"
    ordered = sorted(state["candidates"],
                     key=lambda c: (not any(k.startswith("profile_") for k in c["kinds"]),
                                    "local_rate_deviation" not in c["kinds"], c["lo"]))
    return {"scopex_role": "business_facts", "source": "encoder-health", "schema": SCHEMA,
            "analysis_id": state["analysis_id"], "facts": facts, "requested": requested,
            "status": status, "stop_recommended": bool(confirmed),
            "stop_reason": "At least one raw anomaly verified; later candidates need not be inspected." if confirmed else None,
            "confirmed": confirmed[:1], "confirmed_total": len(confirmed),
            "unresolved_examples": unresolved[:2],
            "unresolved_total": len(unresolved), "unseen_total": unseen,
            "candidates_total": len(ordered),
            "coverage": {"uncovered_edges": uncovered_edges, "internal_gap_count": len(state["gaps"])},
            "inconclusive_reasons": (["unreviewed_candidates"] if unseen else [])
                + (["unresolved_checks"] if unresolved else [])
                + (["incomplete_or_uncertain_data"] if quality else [])
                + (["insufficient_local_reference"] if facts["local_pairs_without_reference"] else [])
                + (["fewer_than_three_raw_samples"] if len(state["samples"]) < 3 else []),
            "candidates": [public_candidate(state, c) for c in ordered[offset:offset + limit]],
            "next_offset": offset + limit if offset + limit < len(ordered) else None,
            "gaps": state["gaps"][:8], "gaps_omitted": max(0, len(state["gaps"]) - 8),
            "issues": state["issues"][:8], "issues_total": len(state["issues"]),
            "inputs": state["inputs"], "profile": state["profile"], "limitations": LIMITATIONS}


def inspect(state, candidate_id=None, start=None, end=None, context_ms=1000, offset=0, limit=12):
    rows = state["samples"]
    if not rows:
        raise ValueError("No raw samples in this analysis")
    times = [r["t"] for r in rows]
    if candidate_id:
        candidate = next((c for c in state["candidates"] if c["id"] == candidate_id), None)
        if candidate is None:
            raise ValueError("Unknown candidate ID; list candidates with report")
        lo, hi = candidate["lo"], candidate["hi"]
        key = candidate_id
    else:
        if start is None or end is None or end <= start:
            raise ValueError("inspect requires --candidate or a positive --start/--end window")
        if start < times[0] or end > times[-1] + 1:
            raise ValueError("Requested region extends beyond captured raw data")
        lo, hi = bisect_left(times, start), bisect_left(times, end) - 1
        if lo > hi:
            return {"analysis_id": state["analysis_id"], "status": "no_samples_in_region",
                    "start": stamp(start), "end": stamp(end), "stop_recommended": False}
        key = f"{stamp(start)}/{stamp(end)}"
    a, b = bisect_left(times, times[lo] - context_ms), bisect_right(times, times[hi] + context_ms)
    relevant = [r for r in state["context"] if times[a] <= r["t"] <= times[b - 1]]
    core_pairs = state["pairs"][lo:hi]
    continuous = not any(p["blocked"] for p in core_pairs)
    left, right = [], []
    for j in range(lo - 1, max(-1, lo - 9), -1):
        if state["pairs"][j]["blocked"]:
            break
        left.append(state["pairs"][j]["rate"])
    for j in range(hi, min(len(state["pairs"]), hi + 8)):
        if state["pairs"][j]["blocked"]:
            break
        right.append(state["pairs"][j]["rate"])
    comparison = {"reference": "adjacent_raw_rates_not_verified_normal",
                  "before_rate_counts_s": stats(left), "after_rate_counts_s": stats(right),
                  "endpoint_matches_local_trend": None}
    if continuous and len(left) >= 3 and len(right) >= 3:
        center = statistics.median(left + right)
        noise = max(1, .1 * abs(center), 6 * statistics.median(abs(v - center) for v in left + right))
        duration = (rows[hi]["t"] - rows[lo]["t"]) / 1000
        residuals = [(r["count"] - rows[lo]["count"]) - center * (r["t"] - rows[lo]["t"]) / 1000
                     for r in rows[lo:hi + 1]]
        comparison.update(reference_rate_counts_s=center,
                          max_abs_trend_residual_counts=max(map(abs, residuals)),
                          endpoint_residual_counts=residuals[-1],
                          endpoint_matches_local_trend=(abs(statistics.median(left) - statistics.median(right)) <= noise
                                                and abs(residuals[-1]) <= max(2, noise * duration)))
    reasons = []
    invalid = [r for r in rows[lo:hi + 1] if r["invalid"]]
    if invalid:
        reasons.append({"check": "verified_count_range", "outcome": "violated",
                        "profile_source": state["profile"]["source"],
                        "evidence": [{"time": r["time"], "raw_count": str(r["count"]),
                                      "printed_raw": r["printed"], "source": r["source"], "line_no": r["line_no"]}
                                     for r in invalid[:3]]})
    rate_evidence = []
    p = state["profile"]
    if "max_rate_counts_s" in p:
        for pair in core_pairs:
            if pair["blocked"]:
                continue
            lower_rate = max(0, abs(pair["delta"]) - p.get("count_tolerance", 0)) * 1000 / (
                pair["dt_ms"] + 2 * p["timestamp_uncertainty_ms"])
            if lower_rate > p["max_rate_counts_s"]:
                rate_evidence.append({"start": rows[pair["lo"]]["time"], "end": rows[pair["hi"]]["time"],
                                      "delta_counts": pair["delta"], "dt_ms": pair["dt_ms"],
                                      "minimum_rate_counts_s": lower_rate,
                                      "limit_counts_s": p["max_rate_counts_s"]})
    # Any captured control event in the inspection context keeps a rate explanation unresolved.
    if rate_evidence:
        reasons.append({"check": "verified_rate_limit", "outcome": "insufficient" if relevant else "violated",
                        "profile_source": p["source"], "evidence": rate_evidence[:3],
                        "counter_context_present": bool(relevant)})
    confirmed = any(r["outcome"] == "violated" for r in reasons)
    related = [c for c in state["candidates"] if c["lo"] <= hi and c["hi"] >= lo]
    shape_only = bool(related)
    transition_only = bool(related) and all(c["kinds"] == ["rate_transition"] for c in related)
    context_pairs = state["pairs"][a:b - 1]
    transition_supported = (transition_only and lo - a >= 4 and b - hi - 1 >= 4
                            and not any(q["blocked"] for q in context_pairs))
    status = ("confirmed_raw_anomaly" if confirmed else "compatible_motion" if transition_supported
              else "unresolved" if shape_only or not continuous else "no_anomaly_evidence")
    if not reasons:
        reasons.append({"check": "motion_explanation",
                        "outcome": "compatible_transition" if transition_supported else "insufficient" if shape_only else "no_contradiction_detected",
                        "reason": "Only a rate transition was detected in continuous context; starts, stops and reverse motion are allowed."
                        if transition_supported else "Direction and local shape alone do not distinguish allowed motion from a bad reading."})
    review = {"region": key, "start": rows[lo]["time"], "end": rows[hi]["time"],
              "status": status, "checks": reasons, "comparison": comparison}
    state["reviews"][key] = review
    # Only a full candidate inspection closes its review; opening a slice is not resolving it.
    indices = range(a + offset, min(b, a + offset + limit))
    trace = [{"time": rows[i]["time"], "raw_count": str(rows[i]["count"]),
              "printed_raw": rows[i]["printed"], "source": rows[i]["source"], "line_no": rows[i]["line_no"],
              "dt_ms": state["pairs"][i - 1]["dt_ms"] if i > 0 else None,
              "delta_counts": str(state["pairs"][i - 1]["delta"]) if i > 0 else None,
              "continuous_from_previous": not state["pairs"][i - 1]["blocked"] if i > 0 else False}
             for i in indices]
    peak = max(core_pairs, key=lambda q: abs(q["rate"] or 0)) if core_pairs else None
    evidence_indices = sorted({lo, hi, *([peak["lo"], peak["hi"]] if peak else [])})
    evidence = [{"source": rows[i]["source"], "line_no": rows[i]["line_no"], "text": rows[i]["text"]}
                for i in evidence_indices[:4]]
    return {"scopex_role": "business_facts", "source": "encoder-health", "schema": SCHEMA,
            "analysis_id": state["analysis_id"], **review, "stop_recommended": confirmed,
            "core": {"samples": hi - lo + 1, "continuous": continuous,
                     "delta_counts": stats([q["delta"] for q in core_pairs if not q["blocked"]]),
                     "rate_counts_s": stats([q["rate"] for q in core_pairs if not q["blocked"]])},
            "context": {"start": rows[a]["time"], "end": rows[b - 1]["time"], "samples": b - a,
                        "left_complete": times[lo] - times[0] >= context_ms and not any(q["blocked"] for q in state["pairs"][a:lo]),
                        "right_complete": times[-1] - times[hi] >= context_ms and not any(q["blocked"] for q in state["pairs"][hi:b - 1])},
            "evidence": evidence, "trace": trace, "trace_offset": offset,
            "next_offset": offset + limit if a + offset + limit < b else None,
            "counter_log_context": relevant[:8], "counter_log_context_omitted": max(0, len(relevant) - 8),
            "next_action": "report" if confirmed else "Compare another candidate or inspect a wider region; unresolved is not normal."}


def record_access(state):
    path = Path(os.environ.get("SCOPEX_DATA_ACCESS_LOG", "/task-scratch/data-access.jsonl"))
    if not path.parent.is_dir():
        return
    request = state["requested"]
    row = {"schema": 1, "source": "cowdisinfect_logs", "operation": "analyze",
           "purpose": "encoder_health", "data_kind": "log", "content_filter": "encoder",
           "start": parse_time(request["start"]).strftime(TS_FMT) if request["start"] else state["facts"]["first_ts"],
           "end": parse_time(request["end"]).strftime(TS_FMT) if request["end"] else
               stamp(state["samples"][-1]["t"] + 1) if state["samples"] else None,
           "source_files": [p["path"] for p in state["inputs"]]}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical(row) + "\n")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in {"scan", "inspect", "report", "-h", "--help"}:
        argv.insert(0, "scan")
    ap = argparse.ArgumentParser(description=__doc__)
    commands = ap.add_subparsers(dest="operation", required=True)
    scan_ap = commands.add_parser("scan", help="Scan only raw readings and save evidence")
    scan_ap.add_argument("logs", nargs="*", type=Path)
    scan_ap.add_argument("--log-dir", type=Path)
    scan_ap.add_argument("--start")
    scan_ap.add_argument("--end")
    scan_ap.add_argument("--profile", type=Path)
    scan_ap.add_argument("--out", "--events-out", dest="out", type=Path)
    scan_ap.add_argument("--max-bytes", type=int, default=256 * 1024**2)
    scan_ap.add_argument("--max-samples", type=int, default=250000)
    detail = commands.add_parser("inspect", help="Inspect any captured region or a candidate")
    detail.add_argument("analysis", type=Path)
    detail.add_argument("--candidate")
    detail.add_argument("--start")
    detail.add_argument("--end")
    detail.add_argument("--context-ms", type=int, default=1000)
    final = commands.add_parser("report", help="Resume the evidence summary and list candidates")
    final.add_argument("analysis", type=Path)
    for sub in (detail, final):
        sub.add_argument("--offset", type=int, default=0)
        sub.add_argument("--limit", type=int, default=12 if sub is detail else 5)
    args = ap.parse_args(argv)
    try:
        if args.operation == "scan":
            start = millis(args.start) if args.start else None
            end = millis(args.end) if args.end else None
            if args.log_dir and args.logs:
                raise ValueError("Use explicit files or --log-dir, not both")
            logs = discover_logs(args.log_dir, start, end) if args.log_dir else args.logs
            if args.max_bytes < 1 or args.max_samples < 1:
                raise ValueError("Capacity limits must be positive")
            if args.out and any(args.out.resolve() == Path(p).resolve() for p in logs):
                raise ValueError("Output must not overwrite an input log")
            if args.out and args.profile and args.out.resolve() == args.profile.resolve():
                raise ValueError("Output must not overwrite the device profile")
            profile = json.loads(args.profile.read_text()) if args.profile else {}
            state = scan(logs, start, end, profile, args.max_bytes, args.max_samples)
            record_access(state)
            if args.out:
                save(args.out, state)
            result = report(state)
            result["analysis_path"] = str(args.out) if args.out else None
        else:
            if args.offset < 0 or not 1 <= args.limit <= 32:
                raise ValueError("offset must be nonnegative; limit must be 1..32")
            state = load(args.analysis)
            if args.operation == "report":
                result = report(state, args.offset, args.limit)
            else:
                if not 0 <= args.context_ms <= 60000:
                    raise ValueError("context-ms must be 0..60000")
                if args.candidate and (args.start or args.end):
                    raise ValueError("Use a candidate or a time window, not both")
                result = inspect(state, args.candidate, millis(args.start) if args.start else None,
                                 millis(args.end) if args.end else None,
                                 args.context_ms, args.offset, args.limit)
                save(args.analysis, state)
        print(canonical(result))
        return 0
    except (ValueError, OSError, KeyError, TypeError, OverflowError) as exc:
        print(canonical({"source": "encoder-health", "status": "error", "error": str(exc),
                         "stop_recommended": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
