#!/usr/bin/env python3
"""Profile an existing Step 6E run without rerunning the expensive task."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import urllib.parse
import urllib.request


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def parse_usage(response_path: Path) -> dict | None:
    try:
        raw = response_path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return None
    candidates = []
    if raw.strip().startswith("{"):
        try:
            candidates.append(json.loads(raw))
        except ValueError:
            pass
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        body = line[5:].strip()
        if not body or body == "[DONE]":
            continue
        try:
            candidates.append(json.loads(body))
        except ValueError:
            continue
    for value in reversed(candidates):
        if isinstance(value, dict) and isinstance(value.get("usage"), dict) and value["usage"]:
            return dict(value["usage"])
    return None


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * p
    lo = int(position)
    hi = min(lo + 1, len(ordered) - 1)
    fraction = position - lo
    return ordered[lo] * (1 - fraction) + ordered[hi] * fraction


def correlation(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    denom = sum(x * x for x in dx) * sum(y * y for y in dy)
    if denom <= 0:
        return None
    return sum(x * y for x, y in zip(dx, dy)) / (denom ** 0.5)


def fetch_cache_metrics(base_url: str) -> dict:
    parsed = urllib.parse.urlsplit(base_url)
    metrics_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/metrics", "", ""))
    result = {"url": metrics_url, "available": False, "matching_lines": []}
    try:
        with urllib.request.urlopen(metrics_url, timeout=5) as response:
            text = response.read(2_000_000).decode("utf-8", errors="replace")
    except Exception as exc:
        result["error"] = type(exc).__name__ + ": " + str(exc)[:300]
        return result
    needles = ("prefix_cache", "cache_hit", "cached_token", "prefix_hit")
    result["available"] = True
    result["matching_lines"] = [
        line for line in text.splitlines()
        if line and not line.startswith("#") and any(n in line.lower() for n in needles)
    ][:200]
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-root", type=Path, required=True)
    ap.add_argument("--base-url", default="")
    args = ap.parse_args(argv)

    root = args.run_root.expanduser().resolve()
    result_path = root / "result.json"
    if not result_path.is_file():
        raise ValueError(f"result.json not found under {root}")
    result = read_json(result_path)
    task_id = result.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("run result is missing task_id")

    turn_dir = root / "work" / task_id / "agent-turns" / "turn-001"
    if not turn_dir.is_dir():
        candidates = sorted((root / "work" / task_id / "agent-turns").glob("turn-*"))
        if not candidates:
            raise ValueError("no OpenClaw turn audit directory found")
        turn_dir = candidates[0]

    request_rows = []
    durations: list[float] = []
    prompt_tokens: list[int] = []
    completion_tokens: list[int] = []
    paired_duration: list[float] = []
    paired_prompt: list[float] = []
    paired_completion: list[float] = []
    for meta_path in sorted(turn_dir.glob("wire-*-meta.json")):
        meta = read_json(meta_path)
        if meta.get("forwarded") is not True:
            continue
        index = int(meta.get("index") or 0)
        start_s, end_s = meta.get("start_s"), meta.get("end_s")
        duration = None
        if isinstance(start_s, (int, float)) and isinstance(end_s, (int, float)):
            duration = max(0.0, float(end_s) - float(start_s))
            durations.append(duration)
        usage = parse_usage(turn_dir / f"wire-{index:02d}-response.bin") or {}
        pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if isinstance(pt, int):
            prompt_tokens.append(pt)
        if isinstance(ct, int):
            completion_tokens.append(ct)
        if duration is not None and isinstance(pt, int) and isinstance(ct, int):
            paired_duration.append(duration)
            paired_prompt.append(float(pt))
            paired_completion.append(float(ct))
        request_rows.append({
            "index": index,
            "duration_s": None if duration is None else round(duration, 4),
            "message_count": meta.get("message_count"),
            "prompt_tokens": pt,
            "completion_tokens": ct,
        })

    exec_calls = result.get("exec_calls") if isinstance(result.get("exec_calls"), list) else []
    commands = [
        row.get("command", "")
        for row in exec_calls
        if isinstance(row, dict) and isinstance(row.get("command"), str)
    ]
    package_install_calls = [cmd for cmd in commands if "pip install" in cmd or "pip3 install" in cmd]
    manual_image_codec_calls = [
        cmd for cmd in commands
        if "/incident/images" in cmd and ("import zlib" in cmd or "struct.unpack" in cmd)
    ]
    image_related_exec_calls = [
        cmd for cmd in commands
        if "/incident/images" in cmd or "contact_sheet" in cmd or "final_sheet" in cmd
    ]

    total_wall_s = result.get("total_wall_s") if isinstance(result.get("total_wall_s"), (int, float)) else None
    model_request_wall = sum(durations)
    sum_completion = sum(completion_tokens)
    completion_rate = None if model_request_wall <= 0 else sum_completion / model_request_wall
    duration_completion_corr = correlation(paired_duration, paired_completion)
    duration_prompt_corr = correlation(paired_duration, paired_prompt)
    profile = {
        "status": "PASS_STEP6F_PROFILE_CAPTURED",
        "source_run_root": str(root),
        "task_id": task_id,
        "model": result.get("model"),
        "source_status": result.get("status"),
        "source_within_product_default_budget": result.get("within_product_default_budget"),
        "task_wall_s": total_wall_s,
        "forwarded_requests": len(request_rows),
        "request_timing": {
            "sum_s": round(model_request_wall, 4),
            "share_of_task_wall": None if not total_wall_s else round(model_request_wall / float(total_wall_s), 4),
            "mean_s": None if not durations else round(statistics.mean(durations), 4),
            "median_s": None if not durations else round(statistics.median(durations), 4),
            "p95_s": None if not durations else round(percentile(durations, 0.95), 4),
            "max_s": None if not durations else round(max(durations), 4),
        },
        "token_profile": {
            "sum_prompt_tokens": sum(prompt_tokens),
            "sum_completion_tokens": sum_completion,
            "largest_prompt_tokens": max(prompt_tokens) if prompt_tokens else None,
            "first_prompt_tokens": prompt_tokens[0] if prompt_tokens else None,
            "last_prompt_tokens": prompt_tokens[-1] if prompt_tokens else None,
        },
        "latency_attribution": {
            "completion_tokens_per_model_second": None if completion_rate is None else round(completion_rate, 4),
            "seconds_per_completion_token": None if not completion_rate else round(1.0 / completion_rate, 6),
            "duration_vs_completion_token_correlation": None if duration_completion_corr is None else round(duration_completion_corr, 6),
            "duration_vs_prompt_token_correlation": None if duration_prompt_corr is None else round(duration_prompt_corr, 6),
            "decode_dominated_signal": bool(
                duration_completion_corr is not None
                and duration_completion_corr >= 0.95
                and (duration_prompt_corr is None or abs(duration_prompt_corr) < 0.5)
            ),
        },
        "request_rows": request_rows,
        "generic_toolbox_gap_signals": {
            "package_install_attempt_count": len(package_install_calls),
            "manual_image_codec_exec_count": len(manual_image_codec_calls),
            "image_related_exec_count": len(image_related_exec_calls),
            "package_install_commands": package_install_calls,
        },
        "source_working_set": {
            "input_bytes": result.get("input_bytes"),
            "max_tool_result_chars": result.get("max_tool_result_chars"),
            "working_set_ratio_max_result_to_input": result.get("working_set_ratio_max_result_to_input"),
            "largest_prompt_tokens": result.get("largest_prompt_tokens"),
            "compaction_count": result.get("compaction_count"),
        },
    }
    if args.base_url:
        profile["vllm_cache_metrics"] = fetch_cache_metrics(args.base_url)

    out = root / "step6f-profile.json"
    out.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(profile, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print("STEP6F_PROFILE_ERROR:", str(exc))
        raise SystemExit(2)
