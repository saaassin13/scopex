#!/usr/bin/env python3
"""Raw encoder evidence tool. No inferred mechanical limits or application fallback."""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
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
COMMANDS = ("scan", "inspect", "report", "plot")
TS_FMT = "%Y-%m-%d %H:%M:%S:%f"
EPOCH = datetime(1970, 1, 1)
MM_PER_COUNT = 501.144 / 4096
DEFAULT_SPEED_WINDOW_MS = 200
DECISION_CONTEXT_MS = 1000
# Dense per-call pages: full evidence stays in the saved snapshot; agents page
# with --offset or --context-ms when a candidate needs a closer look.
INSPECT_TRACE_LIMIT = 4
INSPECT_CONTEXT_SLICE = 3
MAX_PLOT_POINTS = 20000
PLOT_TOP_CANDIDATES = 5
STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}:\d{3})(?!\d)")
RAW = re.compile(r"\bGet EncoderVal, raw\[(-?\d+)\]")
LOG = re.compile(r"^CowDisinfect-(\d{8})-(\d{6})\.log(?:\.\d+)?$")
_CANDIDATE_KEY = re.compile(r"R\d+")
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


def checked_time(text):
    # CLI timestamps must carry exactly the 3-digit millisecond form the logs
    # use; strptime's %f would happily left-pad '5' into 500 ms.
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}:\d{3}", text):
        raise ValueError("Timestamps must be 'YYYY-MM-DD HH:MM:SS:mmm' with 3-digit milliseconds")
    return millis(text)


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
    # Older snapshots mixed window views into "reviews"; keep candidate reviews only.
    state.setdefault("window_reviews", {})
    moved = {key: value for key, value in state["reviews"].items()
             if not _CANDIDATE_KEY.fullmatch(key)}
    if moved:
        state["reviews"] = {key: value for key, value in state["reviews"].items()
                            if _CANDIDATE_KEY.fullmatch(key)}
        state["window_reviews"].update(moved)
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
    count_min = profile.get("count_min")
    count_max = profile.get("count_max")
    if count_min is not None and count_max is not None and count_min > count_max:
        raise ValueError("count_min exceeds count_max")
    for key in ("max_rate_counts_s", "timestamp_uncertainty_ms", "count_tolerance"):
        if key in profile and (type(profile[key]) not in (int, float)
                               or not math.isfinite(profile[key]) or profile[key] < 0):
            raise ValueError(f"{key} must be finite and nonnegative")
    if "max_rate_counts_s" in profile:
        if profile["max_rate_counts_s"] <= 0 or "timestamp_uncertainty_ms" not in profile:
            raise ValueError("A rate limit requires a positive limit and timestamp_uncertainty_ms")
        if profile.get("continuity_verified") is not True:
            raise ValueError("A rate limit requires verified reset/wrap continuity")
    if "continuity_verified" in profile and type(profile["continuity_verified"]) is not bool:
        raise ValueError("continuity_verified must be boolean")
    return profile


def discover_logs(root, start, end):
    if start is None or end is None or end <= start or end - start > 48 * 3600000:
        raise ValueError("--log-dir requires a positive window of at most 48 hours")
    groups = {}
    for path in Path(root).iterdir():
        match = LOG.fullmatch(path.name)
        if match and path.is_file():
            time = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
            groups.setdefault(time, []).append(path)
    starts = sorted(groups)
    paths = []
    for index, group_start in enumerate(starts):
        next_start = starts[index + 1] if index + 1 < len(starts) else group_start + timedelta(hours=1)
        group_start_ms = (group_start - EPOCH) // timedelta(milliseconds=1)
        group_end_ms = (next_start - EPOCH) // timedelta(milliseconds=1)
        if group_start_ms >= end or group_end_ms <= start:
            continue
        paths.extend(sorted(groups[group_start], key=lambda path: path.name))
    if len(paths) > 64:
        raise ValueError("Too many rotation files; narrow the window")
    return sorted(paths)


def scan(logs, start=None, end=None, profile=None, max_bytes=256 * 1024**2, max_samples=250000):
    profile = profile_checked(profile or {})
    if start is not None and end is not None and end <= start:
        raise ValueError("end must be after start")
    logs = sorted(set(Path(p).resolve() for p in logs))
    if not logs:
        raise ValueError("No input files")
    if len(logs) > 64:
        raise ValueError("capacity_exceeded: at most 64 explicit files per analysis")
    samples, context, issues, inputs = [], [], [], []
    total_bytes = 0
    pending = False

    def issue(kind, **data):
        issues.append({"kind": kind, **data})
        if len(issues) > max_samples:
            raise ValueError("capacity_exceeded: issue limit; no completed analysis")

    for path in logs:
        hasher = hashlib.sha256()
        max_t = None
        rollback_until = None
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
                if t is not None:
                    in_raw = (start is None or t >= start) and (end is None or t < end)
                    # Decision context is captured with margin on both window edges
                    # (e.g. a reset before --start, a control event after --end) so
                    # edge readings stay explainable.
                    in_context = ((start is None or t >= start - DECISION_CONTEXT_MS)
                                  and (end is None or t < end + DECISION_CONTEXT_MS))
                    if not in_raw and not (contextual and in_context):
                        continue
                source = {"source": str(path), "line_no": number}
                if t is None:
                    issue("unparsed_time", **source)
                    pending = True
                    continue
                if len(line) > 4096:
                    raise ValueError("capacity_exceeded: encoder evidence line exceeds 4096 characters")
                if contextual and in_context:
                    context.append({"t": t, "time": stamp(t), "text": line, **source})
                    if len(context) > max_samples:
                        raise ValueError("capacity_exceeded: context limit; no completed analysis")
                    pending = True
                if not raw_marker or not in_raw:
                    continue
                readings = RAW.findall(line)
                if not readings:
                    issue("unparsed_raw", time=stamp(t), **source)
                    pending = True
                    continue
                if len(readings) > 1:
                    # Every reading is kept; the equal timestamps form a local barrier below.
                    issue("multiple_raw_per_line", time=stamp(t), readings=len(readings), **source)
                if max_t is not None and t < max_t:
                    if rollback_until is None:
                        issue("non_increasing_source_time", time=stamp(t), **source)
                    rollback_until = max_t
                rollback_edge = False
                if rollback_until is not None and t > rollback_until:
                    rollback_until = None
                    # The catch-up row bridges the clock discontinuity: its left
                    # pair spans the rollback, so it carries a barrier of its own.
                    rollback_edge = True
                ambiguous = rollback_until is not None
                for printed_text in readings:
                    printed = int(printed_text)
                    count = printed
                    bits = profile.get("unsigned_print_bits")
                    if bits and 0 <= printed < 2**bits and printed >= 2**(bits - 1):
                        count = printed - 2**bits
                    invalid = (("count_min" in profile and count < profile["count_min"])
                               or ("count_max" in profile and count > profile["count_max"]))
                    samples.append({"t": t, "time": stamp(t), "count": count, "printed": str(printed),
                                    "text": line, "invalid": invalid,
                                    "barrier": pending or ambiguous or rollback_edge, **source})
                    parsed += 1
                pending = False
                max_t = t if max_t is None else max(max_t, t)
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
    # Rollback ambiguity was bounded during the scan to the interval until the
    # source clock passes its pre-rollback maximum; equal timestamps stay local.
    for a, b in zip(samples, samples[1:]):
        if a["t"] == b["t"]:
            a["barrier"] = b["barrier"] = True
            issue("duplicate_timestamp", time=b["time"], source=b["source"], line_no=b["line_no"])
    state = {"schema": SCHEMA, "version": VERSION, "profile": profile,
             "requested": {"start": stamp(start) if start is not None else None,
                           "end": stamp(end) if end is not None else None},
             "inputs": inputs, "samples": samples, "context": context, "issues": issues,
             "reviews": {}, "window_reviews": {}}
    state["analysis_id"] = digest({"inputs": inputs, "requested": state["requested"],
                                  "profile": profile, "version": VERSION})[:20]
    analyze(state)
    return state


def analyze(state):
    rows, profile = state["samples"], state["profile"]
    intervals = [b["t"] - a["t"] for a, b in zip(rows, rows[1:])
                 if b["t"] > a["t"] and not (a["barrier"] or b["barrier"])]
    median_dt = statistics.median(intervals) if intervals else None
    gap_ms = max(100, (median_dt or 20) * 5)
    context_times = [r["t"] for r in state["context"]]
    pairs, gaps, seeds = [], [], []

    def seed(lo, hi, kind, **metrics):
        row = {"lo": lo, "hi": hi, "kind": kind}
        row.update(metrics)
        seeds.append(row)

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
                seed(i, i + 1, "profile_rate_candidate",
                     excess=round(lower_rate / profile["max_rate_counts_s"], 2))

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
            seed(i, i + 1, "local_rate_deviation",
                 deviation=round(abs(pair["rate"] - center) / scale, 2))
        # Persistent changes are observations, not anomalies, including normal starts/stops.
        if len(left) >= 4 and len(right) >= 4:
            l, r = statistics.median(left[:4]), statistics.median(right[:4])
            noise = max(2 * 1000 / pair["dt_ms"],
                        statistics.median(abs(v - l) for v in left[:4]),
                        statistics.median(abs(v - r) for v in right[:4]))
            if abs(l - r) > max(8 * noise, .5 * max(abs(l), abs(r))):
                seed(i, i + 1, "rate_transition",
                     shift=round(abs(l - r) / max(noise, 1e-9), 2))
    seeds.sort(key=lambda s: (s["lo"], s["hi"], s["kind"]))
    candidates = []
    for s in seeds:
        extra = {key: value for key, value in s.items() if key not in ("lo", "hi", "kind")}
        if candidates and s["lo"] <= candidates[-1]["hi"]:
            c = candidates[-1]
            c["hi"] = max(c["hi"], s["hi"])
            c["kinds"] = sorted(set(c["kinds"] + [s["kind"]]))
            c["metrics"].update(extra)
        else:
            candidates.append({"lo": s["lo"], "hi": s["hi"], "kinds": [s["kind"]], "metrics": extra})
    for i, c in enumerate(candidates):
        core = [p for p in pairs[c["lo"]:c["hi"]] if not p["blocked"]]
        peak = max((abs(p["rate"]) for p in core if p["rate"] is not None), default=None)
        c.update(id=f"R{i + 1}", start=rows[c["lo"]]["time"], end=rows[c["hi"]]["time"],
                 duration_ms=rows[c["hi"]]["t"] - rows[c["lo"]]["t"],
                 samples=c["hi"] - c["lo"] + 1,
                 peak_rate_counts_s=round(peak, 3) if peak is not None else None)
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
    review = state["reviews"].get(candidate["id"])
    if review:
        result["review"] = review
        rows = state["samples"]
        indices = {candidate["lo"], candidate["hi"]}
        pairs = state["pairs"][candidate["lo"]:candidate["hi"]]
        if pairs:
            peak = max(pairs, key=lambda pair: abs(pair["rate"] or 0))
            indices.update((peak["lo"], peak["hi"]))
        result["evidence"] = [{"time": rows[i]["time"], "raw_count": str(rows[i]["count"]),
                               "source": rows[i]["source"], "line_no": rows[i]["line_no"]}
                              for i in sorted(indices)]
    return result


def batch_hint(state, skip=()):
    """A copy-paste batched command for every still-unreviewed candidate.

    Models follow a concrete command far more reliably than a prose rule, so
    the tool itself steers every single-candidate detour back to batching.
    """
    remaining = [candidate["id"] for candidate in state["candidates"]
                 if candidate["id"] not in state["reviews"] and candidate["id"] not in skip]
    if not remaining:
        return None
    batch = " ".join(f"--candidate {candidate_id}" for candidate_id in remaining)
    return (f"{len(remaining)} candidates remain unreviewed; review them in ONE batched "
            f"call: python3 encoder_health.py inspect <analysis-path> {batch}")


def candidate_order(state):
    """Deterministic severity order: profile-backed first, then deviation size."""
    def rank(candidate):
        kinds = candidate["kinds"]
        return (not any(kind.startswith("profile_") for kind in kinds),
                "local_rate_deviation" not in kinds,
                -max(candidate.get("metrics", {}).values(), default=0),
                candidate["lo"])
    return sorted(state["candidates"], key=rank)


def plot_summary(state, *, include_panels=False):
    plots = state.get("plots")
    if not isinstance(plots, dict):
        return None
    artifacts = plots.get("artifacts")
    compact_artifacts = []
    if isinstance(artifacts, list):
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            keys = ["kind", "candidate_id", "filename", "path", "sha256", "bytes"]
            if include_panels:
                keys.append("panels")
            compact = {key: artifact[key] for key in keys if key in artifact}
            compact_artifacts.append(compact)
    return {
        "status": plots.get("status"),
        "output_dir": plots.get("output_dir"),
        "analysis_id": plots.get("analysis_id"),
        "artifacts": compact_artifacts,
        "candidates_plotted": plots.get("candidates_plotted",
                                        plots.get("candidates_requested", [])),
        "candidates_available": plots.get("candidates_available"),
        "plot_points_limit": plots.get("plot_points_limit"),
        "raw_only": plots.get("raw_only"),
    }


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
    # One source of truth for the inconclusive status and its listed reasons.
    reasons = (["unreviewed_candidates"] if unseen else []) \
        + (["unresolved_checks"] if unresolved else []) \
        + (["incomplete_or_uncertain_data"] if quality else []) \
        + (["insufficient_local_reference"] if facts["local_pairs_without_reference"] else []) \
        + (["fewer_than_three_raw_samples"] if len(state["samples"]) < 3 else [])
    if confirmed:
        status = "confirmed_raw_anomaly"
    elif not state["samples"] and not state["issues"]:
        status = "no_raw_data"
    elif reasons:
        status = "inconclusive"
    else:
        status = "no_anomaly_evidence_in_completed_checks"
    ordered = candidate_order(state)
    return {"scopex_role": "business_facts", "source": "encoder-health", "schema": SCHEMA,
            "analysis_id": state["analysis_id"], "facts": facts, "requested": requested,
            "status": status, "stop_recommended": False,
            "stop_reason": None,
            "confirmed": confirmed, "confirmed_total": len(confirmed),
            "unresolved_examples": unresolved[:2],
            "unresolved_total": len(unresolved), "unseen_total": unseen,
            "candidates_total": len(ordered),
            "coverage": {"uncovered_edges": uncovered_edges, "internal_gap_count": len(state["gaps"])},
            "inconclusive_reasons": reasons,
            "candidates": [public_candidate(state, c) for c in ordered[offset:offset + limit]],
            "next_action": batch_hint(state),
            "next_offset": offset + limit if offset + limit < len(ordered) else None,
            "gaps": state["gaps"][:8], "gaps_omitted": max(0, len(state["gaps"]) - 8),
            "issues": state["issues"][:8], "issues_total": len(state["issues"]),
            "inputs": state["inputs"], "profile": state["profile"], "limitations": LIMITATIONS,
            "plots": plot_summary(state),
            "review_guide": {"ordering": "severity: profile-backed checks first, then deviation size",
                             "metrics": {"excess": "lower-bound rate over the profile limit (x)",
                                         "deviation": "local rate deviation over local scale (x)",
                                         "shift": "persistent rate change over local noise (x)",
                                         "peak_rate_counts_s": "largest |rate| inside the candidate"}}}


def inspect(state, candidate_id=None, start=None, end=None, context_ms=1000, offset=0,
            limit=INSPECT_TRACE_LIMIT):
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
    # Filter context by the requested time bounds themselves, not by neighboring
    # sample times: events before the first or after the last raw sample still
    # count as captured decision context.
    relevant = [r for r in state["context"]
                if times[lo] - context_ms <= r["t"] <= times[hi] + context_ms]
    decision_a = bisect_left(times, times[lo] - DECISION_CONTEXT_MS)
    decision_b = bisect_right(times, times[hi] + DECISION_CONTEXT_MS)
    decision_relevant = [r for r in state["context"]
                         if times[lo] - DECISION_CONTEXT_MS <= r["t"] <= times[hi] + DECISION_CONTEXT_MS]
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
        reasons.append({"check": "verified_rate_limit",
                        "outcome": "insufficient" if decision_relevant else "violated",
                        "profile_source": p["source"], "evidence": rate_evidence[:3],
                        "counter_context_present": bool(decision_relevant),
                        "decision_context_ms": DECISION_CONTEXT_MS})
    confirmed = any(r["outcome"] == "violated" for r in reasons)
    related = [c for c in state["candidates"] if c["lo"] <= hi and c["hi"] >= lo]
    shape_only = bool(related)
    transition_only = bool(related) and all(c["kinds"] == ["rate_transition"] for c in related)
    context_pairs = state["pairs"][decision_a:decision_b - 1]
    transition_supported = (transition_only and lo - decision_a >= 4 and decision_b - hi - 1 >= 4
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
    # Window views inform but never close a candidate review; they are kept
    # separately so report counts stay per-candidate.
    bucket = state["reviews"] if candidate_id else state.setdefault("window_reviews", {})
    bucket[key] = review
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
            "analysis_id": state["analysis_id"], **review, "stop_recommended": False,
            "core": {"samples": hi - lo + 1, "continuous": continuous,
                     "delta_counts": stats([q["delta"] for q in core_pairs if not q["blocked"]]),
                     "rate_counts_s": stats([q["rate"] for q in core_pairs if not q["blocked"]])},
            "context": {"start": rows[a]["time"], "end": rows[b - 1]["time"], "samples": b - a,
                        "left_complete": times[lo] - times[0] >= context_ms and not any(q["blocked"] for q in state["pairs"][a:lo]),
                        "right_complete": times[-1] - times[hi] >= context_ms and not any(q["blocked"] for q in state["pairs"][hi:b - 1])},
            "evidence": evidence, "trace": trace, "trace_offset": offset,
            "next_offset": offset + limit if a + offset + limit < b else None,
            "counter_log_context": relevant[:INSPECT_CONTEXT_SLICE],
            "counter_log_context_omitted": max(0, len(relevant) - INSPECT_CONTEXT_SLICE),
            "decision_counter_log_context": decision_relevant[:INSPECT_CONTEXT_SLICE],
            "decision_counter_log_context_omitted": max(0, len(decision_relevant) - INSPECT_CONTEXT_SLICE),
            "hint": batch_hint(state, skip=(key,)),
            "next_action": batch_hint(state, skip=(key,)) or (
                "Continue with the remaining candidates; a confirmed result does not stop review."
                if shape_only or confirmed else
                "report")}


def _speed_values(state, speed_window_ms):
    rows = state["samples"]
    times = [row["t"] for row in rows]
    values = [None] * len(rows)
    for index in range(1, len(rows)):
        target = times[index] - speed_window_ms
        start = bisect_right(times, target, 0, index + 1) - 1
        if start < 0 or start >= index:
            continue
        if any(pair["blocked"] for pair in state["pairs"][start:index]):
            continue
        elapsed_ms = times[index] - times[start]
        if elapsed_ms <= 0:
            continue
        values[index] = ((rows[index]["count"] - rows[start]["count"])
                         * MM_PER_COUNT * 1000 / elapsed_ms)
    return values


def _decimate_segment(xs, ys, limit):
    # limit bounds the output point count; each bucket keeps at most five
    # points (first, last, min, max, largest |value|).
    if len(xs) <= limit:
        return list(zip(xs, ys))
    buckets = max(1, limit // 5)
    bucket_size = max(1, math.ceil(len(xs) / buckets))
    selected = set()
    for start in range(0, len(xs), bucket_size):
        end = min(len(xs), start + bucket_size)
        finite = [i for i in range(start, end) if math.isfinite(ys[i])]
        if not finite:
            continue
        selected.update((finite[0], finite[-1], min(finite, key=lambda i: ys[i]),
                         max(finite, key=lambda i: ys[i]),
                         max(finite, key=lambda i: abs(ys[i]))))
    return [(xs[i], ys[i]) for i in sorted(selected)]


def _decimate_series(xs, ys, limit):
    # Split at non-finite values, then budget points across segments by length
    # so a break anywhere no longer starves the segments after it.
    segments = []
    start = 0
    for index in range(len(xs) + 1):
        if index == len(xs) or not math.isfinite(ys[index]):
            if start < index:
                segments.append((start, index))
            start = index + 1
    if not segments:
        return [], []
    total = sum(end - seg_start for seg_start, end in segments)
    points = []
    for order, (seg_start, end) in enumerate(segments):
        quota = max(2, limit * (end - seg_start) // total)
        points.extend(_decimate_segment(xs[seg_start:end], ys[seg_start:end], quota))
        if order < len(segments) - 1:
            points.append((math.nan, math.nan))
    return [point[0] for point in points], [point[1] for point in points]


def _plot_series(state, start, end, speed_window_ms, max_points=MAX_PLOT_POINTS):
    rows = state["samples"]
    base = rows[start]["t"]
    raw_offset = rows[start]["count"]
    raw_x, raw_y = [], []
    for index in range(start, end):
        if index > start and state["pairs"][index - 1]["blocked"]:
            raw_x.append((rows[index]["t"] - base) / 1000)
            raw_y.append(math.nan)
        raw_x.append((rows[index]["t"] - base) / 1000)
        raw_y.append(rows[index]["count"] - raw_offset)
    dt_x, dt_y, delta_x, delta_y = [], [], [], []
    for pair in state["pairs"][start:max(start, end - 1)]:
        endpoint = pair["hi"]
        dt_x.append((rows[endpoint]["t"] - base) / 1000)
        delta_x.append((rows[endpoint]["t"] - base) / 1000)
        if pair["blocked"]:
            dt_y.append(math.nan)
            delta_y.append(math.nan)
        else:
            dt_y.append(pair["dt_ms"])
            delta_y.append(pair["delta"])
    speed = _speed_values(state, speed_window_ms)
    speed_x = [(rows[index]["t"] - base) / 1000 for index in range(start, end)]
    speed_y = [value if value is not None else math.nan for value in speed[start:end]]
    return {
        "raw": _decimate_series(raw_x, raw_y, max_points),
        "dt": _decimate_series(dt_x, dt_y, max_points),
        "delta": _decimate_series(delta_x, delta_y, max_points),
        "speed": _decimate_series(speed_x, speed_y, max_points),
        "raw_offset": raw_offset,
        "sample_count": end - start,
        "blocked_pair_count": sum(pair["blocked"] for pair in
                                   state["pairs"][start:max(start, end - 1)]),
    }


def _write_plot(state, path, start, end, title, core=None, speed_window_ms=DEFAULT_SPEED_WINDOW_MS,
                max_points=MAX_PLOT_POINTS, candidates=(), label_ids=frozenset()):
    if end <= start:
        raise ValueError("plot region has no samples")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLBACKEND", "Agg")
    import matplotlib
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker

    payload = _plot_series(state, start, end, speed_window_ms, max_points)
    fig, axes = plt.subplots(4, 1, figsize=(14, 11), sharex=True)
    panels = (("raw", f"raw cumulative count (offset {payload['raw_offset']}; plotted Δcount)", "count Δ"),
              ("dt", "raw sampling interval", "ms"),
              ("delta", "signed raw increment", "counts"),
              ("speed", f"raw-derived speed ({speed_window_ms} ms window)", "mm/s"))
    for axis, (key, label, unit) in zip(axes, panels):
        xs, ys = payload[key]
        axis.plot(xs, ys, color="#1f4e79", linewidth=0.8)
        axis.set_ylabel(unit)
        axis.set_title(label, loc="left", fontsize=10)
        axis.grid(True, alpha=0.25)
    # Absolute wall-clock ticks so anomalies point at real log times directly.
    base_ms = state["samples"][start]["t"]
    if core is not None:
        core_start, core_end = core
        axes[0].axvspan((state["samples"][core_start]["t"] - base_ms) / 1000,
                        (state["samples"][core_end]["t"] - base_ms) / 1000,
                        color="#f2c14e", alpha=0.22, label="candidate core")
        axes[0].legend(loc="upper right")
    axes[-1].xaxis.set_major_formatter(
        ticker.FuncFormatter(lambda v, _: stamp(base_ms + v * 1000)[11:23]))
    plt.setp(axes[-1].get_xticklabels(), rotation=30, ha="right", fontsize=7)
    if candidates:
        for candidate in candidates:
            x = (state["samples"][candidate["lo"]]["t"] - base_ms) / 1000
            for axis in axes:
                axis.axvline(x, color="#c0392b", alpha=0.35, linewidth=0.8)
            if candidate["id"] in label_ids:
                axes[0].annotate(f"{candidate['id']} {candidate['start'][11:23]}",
                                 xy=(x, 1), xycoords=("data", "axes fraction"),
                                 rotation=90, fontsize=7, color="#c0392b",
                                 va="top", ha="right")
    axes[-1].set_xlabel("time of day (HH:MM:SS:mmm)")
    requested = state["requested"]
    fig.suptitle(title, fontsize=12)
    fig.text(0.01, 0.01,
             f"raw samples={payload['sample_count']}  blocked pairs={payload['blocked_pair_count']}  "
             f"requested={requested['start']}–{requested['end']}", fontsize=8)
    fig.tight_layout(rect=(0, 0.03, 1, 0.96))
    fig.savefig(path, dpi=120, format="png")
    plt.close(fig)
    data = path.read_bytes()
    return {"path": str(path), "filename": path.name, "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "sample_count": payload["sample_count"],
            "blocked_pair_count": payload["blocked_pair_count"],
            "panels": ["raw_count", "sample_dt_ms", "signed_delta_raw", "raw_speed_mm_s"],
            "speed_window_ms": speed_window_ms, "mm_per_count": MM_PER_COUNT,
            "raw_offset_count": payload["raw_offset"]}


def plot_snapshot(state, out_dir, candidate_ids=None, context_ms=1000,
                  speed_window_ms=DEFAULT_SPEED_WINDOW_MS, max_points=MAX_PLOT_POINTS):
    if not state["samples"]:
        raise ValueError("No raw samples in this analysis")
    if not 0 <= context_ms <= 60000:
        raise ValueError("context-ms must be 0..60000")
    if not 1 <= speed_window_ms <= 60000:
        raise ValueError("speed-window-ms must be 1..60000")
    if not 100 <= max_points <= 200000:
        raise ValueError("max-points must be 100..200000")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ordered = candidate_order(state)
    selected = (ordered[:PLOT_TOP_CANDIDATES] if candidate_ids is None else
                [next((candidate for candidate in ordered if candidate["id"] == candidate_id), None)
                 for candidate_id in candidate_ids])
    if any(candidate is None for candidate in selected):
        raise ValueError("Unknown candidate ID")
    label_ids = {candidate["id"] for candidate in selected}
    artifacts = []
    rows = state["samples"]
    overview = _write_plot(state, out_dir / "overview.png", 0, len(rows),
                           "Encoder raw overview", speed_window_ms=speed_window_ms,
                           max_points=max_points, candidates=ordered, label_ids=label_ids)
    overview.update(kind="overview", media_type="image/png", analysis_id=state["analysis_id"])
    artifacts.append(overview)
    times = [row["t"] for row in rows]
    for candidate in selected:
        lo, hi = candidate["lo"], candidate["hi"]
        start = bisect_left(times, times[lo] - context_ms)
        end = bisect_right(times, times[hi] + context_ms)
        artifact = _write_plot(
            state, out_dir / f"candidate-{candidate['id']}.png", start, end,
            f"Encoder raw candidate {candidate['id']}  {candidate['start']} – {candidate['end']}",
            core=(lo, hi), speed_window_ms=speed_window_ms, max_points=max_points)
        artifact.update(kind="candidate", candidate_id=candidate["id"],
                        media_type="image/png", analysis_id=state["analysis_id"])
        artifacts.append(artifact)
    return {"scopex_role": "business_facts", "source": "encoder-health", "schema": SCHEMA,
            "analysis_id": state["analysis_id"], "status": "plots_written",
            "output_dir": str(out_dir), "artifacts": artifacts,
            "candidates_plotted": [candidate["id"] for candidate in selected],
            "candidates_available": len(ordered),
            "plot_note": (None if candidate_ids is not None else
                          "overview marks every candidate; candidate plots default to the "
                          f"{PLOT_TOP_CANDIDATES} highest-severity candidates; pass --candidate to select specific ones"),
            "plot_points_limit": max_points,
            "raw_only": True}


def _append_access(row):
    path = Path(os.environ.get("SCOPEX_DATA_ACCESS_LOG", "/task-scratch/data-access.jsonl"))
    if not path.parent.is_dir():
        return
    # Best-effort audit trail: a failing append must never discard a completed
    # analysis, so failures are swallowed on purpose.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(canonical(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        pass


def record_access(state):
    request = state["requested"]
    start = request["start"] or state["facts"]["first_ts"]
    end = request["end"] or (stamp(state["samples"][-1]["t"] + 1) if state["samples"] else None)
    # Only complete windows are recorded: a null bound would break consumers
    # that parse every access row.
    if not (isinstance(start, str) and isinstance(end, str)):
        return
    _append_access({"schema": 1, "source": "cowdisinfect_logs", "operation": "analyze",
                    "purpose": "encoder_health", "data_kind": "log", "content_filter": "encoder",
                    "start": start, "end": end,
                    "source_files": [p["path"] for p in state["inputs"]]})


def record_failed_access(logs, start, end):
    """A scan that fails mid-read still touched real log bytes; audit that."""
    if not logs:
        return
    _append_access({"schema": 1, "source": "cowdisinfect_logs", "operation": "analyze",
                    "purpose": "encoder_health", "data_kind": "log", "content_filter": "encoder",
                    "start": start, "end": end, "completed": False,
                    "source_files": [str(Path(p).resolve()) for p in logs]})


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        # Chinese control-log lines must survive non-UTF-8 locales.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in {*COMMANDS, "-h", "--help"}:
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
    detail.add_argument("--candidate", action="append")
    detail.add_argument("--start")
    detail.add_argument("--end")
    detail.add_argument("--context-ms", type=int, default=1000)
    final = commands.add_parser("report", help="Resume the evidence summary and list candidates")
    final.add_argument("analysis", type=Path)
    plot_ap = commands.add_parser("plot", help="Render raw-only plots from a saved snapshot")
    plot_ap.add_argument("analysis", type=Path)
    plot_ap.add_argument("--out-dir", required=True, type=Path)
    plot_ap.add_argument("--candidate", action="append")
    plot_ap.add_argument("--context-ms", type=int, default=1000)
    plot_ap.add_argument("--speed-window-ms", type=int, default=DEFAULT_SPEED_WINDOW_MS)
    plot_ap.add_argument("--max-points", type=int, default=MAX_PLOT_POINTS)
    for sub in (detail, final):
        sub.add_argument("--offset", type=int, default=0)
        sub.add_argument("--limit", type=int, default=INSPECT_TRACE_LIMIT if sub is detail else 5)
    args = ap.parse_args(argv)
    scan_files = []
    try:
        if args.operation == "scan":
            start = checked_time(args.start) if args.start else None
            end = checked_time(args.end) if args.end else None
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
            scan_files = logs
            state = scan(logs, start, end, profile, args.max_bytes, args.max_samples)
            if args.out:
                save(args.out, state)
            record_access(state)
            result = report(state)
            result["analysis_path"] = str(args.out) if args.out else None
        elif args.operation == "plot":
            if not 0 <= args.context_ms <= 60000:
                raise ValueError("context-ms must be 0..60000")
            state = load(args.analysis)
            result = plot_snapshot(state, args.out_dir, args.candidate, args.context_ms,
                                   args.speed_window_ms, args.max_points)
            runs = state.setdefault("plot_runs", [])
            runs.append({"output_dir": result["output_dir"],
                         "candidates_plotted": result["candidates_plotted"],
                         "artifacts": len(result["artifacts"])})
            del runs[:-8]
            state["plots"] = result
            save(args.analysis, state)
            result = plot_summary(state, include_panels=True)
            result["plot_runs"] = runs
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
                if len(args.candidate or []) > 1:
                    # One load/save per batch; each item keeps its review durable.
                    results = [inspect(state, candidate, None, None,
                                       args.context_ms, args.offset, args.limit)
                               for candidate in args.candidate]
                    save(args.analysis, state)
                    result = {"scopex_role": "business_facts", "source": "encoder-health",
                              "schema": SCHEMA, "analysis_id": state["analysis_id"],
                              "status": "batch_inspected",
                              "inspections": [{"region": r["region"], "start": r["start"],
                                               "end": r["end"], "status": r["status"],
                                               "core": r["core"],
                                               "checks": {c["check"]: c["outcome"] for c in r["checks"]}}
                                              for r in results],
                              "next_action": "report",
                              "hint": batch_hint(state)}
                else:
                    result = inspect(state, (args.candidate or [None])[0],
                                     checked_time(args.start) if args.start else None,
                                     checked_time(args.end) if args.end else None,
                                     args.context_ms, args.offset, args.limit)
                    save(args.analysis, state)
        print(canonical(result))
        return 0
    except (ValueError, OSError, KeyError, TypeError, OverflowError, ImportError) as exc:
        if args.operation == "scan":
            record_failed_access(scan_files, args.start, args.end)
        print(canonical({"source": "encoder-health", "status": "error", "error": str(exc),
                         "stop_recommended": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
