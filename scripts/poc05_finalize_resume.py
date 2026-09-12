#!/usr/bin/env python3
"""Finalize an existing POC05 stop/resume audit without rerunning investigation.

The stored POC05 run must already prove progress, deterministic STOP, same-session
RESUME, re-steering, and returned system/robot evidence. This script adds the
runtime behavior established by POC03: end Investigation after required evidence
exists, then create a fresh no-tool Finalizer context and emit TASK_COMPLETED.

It does not replay OpenClaw or tools. Exactly one fresh local-model finalization
request is allowed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import poc02_run as p2
import poc03_finalize as f3
import poc04_steering as s

MECHANIC_ONLY_FAILURES = {
    "resume_completed",
    "final_visible_answer",
    "task_completed_progress_event",
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, obj):
    with path.open("x", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


def best_result(trace: dict, filename: str) -> str:
    rows = [
        row for row in trace.get("calls", [])
        if row.get("completed") and s.call_touches(row, filename)
        and isinstance(row.get("result"), str) and row.get("result").strip()
    ]
    if not rows:
        raise ValueError(f"stored investigation has no completed {filename} evidence")
    return max((row["result"] for row in rows), key=len)


def mechanics_ok(stored: dict) -> tuple[bool, list[str]]:
    grade = stored.get("grade") or {}
    checks = grade.get("checks") or {}
    if not isinstance(checks, dict):
        return False, ["stored_grade_checks_missing"]
    errors = [
        key for key, value in checks.items()
        if key not in MECHANIC_ONLY_FAILURES and value is not True
    ]
    required_false_only = set(grade.get("errors") or []).issubset(MECHANIC_ONLY_FAILURES)
    if not required_false_only:
        errors.append("stored_run_failed_nonfinalization_checks")
    return not errors, errors


def build_prompts(app_evidence: str, system_evidence: str, robot_evidence: str):
    system = """你是设备诊断任务的最终结果整理器。调查阶段已经结束，不存在任何工具。
只能使用下面已经取得的证据；不要继续调查，不要要求补充文件。
区分事实、推断和未知。status=137 只证明进程以 137 退出，不能自动等同于 OOM；
机器人日志只能支持当前时间窗口内是否看到机器人侧异常。输出简短中文结论，不要 Markdown 表格。"""
    user = f"""原任务：分析 2026-09-12 10:15 左右任务执行失败的现场证据。
用户恢复任务后要求优先检查 system.log，再检查 robot.log，并保留停止前 app.log 证据。

【停止前已取得的 app.log 证据】
{app_evidence}

【恢复后取得的 system.log 证据】
{system_evidence}

【恢复后取得的 robot.log 证据】
{robot_evidence}

请只完成最终整理：
1. 已确认事实；
2. 当前最有证据支持的解释；
3. 仍不能确认的事项。
控制在 300 中文字左右。"""
    return system, user


def final_signals(text: str) -> dict:
    lower = text.lower()
    system_crash = "status=137" in lower or "status 137" in lower
    robot_ok = (
        "joint_fault_code=0" in lower
        or "heartbeat=ok" in lower
        or bool(re.search(r"机器人.{0,28}(?:未见异常|无故障|正常|ready)", text, re.I))
    )
    # OOM may be mentioned only as an unknown / unproven hypothesis.
    oom_asserted = False
    for line in text.splitlines():
        if "oom" not in line.lower():
            continue
        if re.search(
            r"(?:不能|无法|未|尚未|证据不足|待确认|无法确认|不能确认|不等同|"
            r"unknown|unproven|cannot|can't|not proven|no evidence)",
            line, re.I,
        ):
            continue
        if re.search(r"(?:oom).{0,20}(?:根因|导致|造成|引起|cause|caused|root cause)", line, re.I):
            oom_asserted = True
            break
    return {
        "system_status_137": system_crash,
        "robot_window_healthy": robot_ok,
        "unsupported_oom_assertion": oom_asserted,
    }


def emit_progress(path: Path, event: str, **fields):
    row = {
        "time": datetime.now(timezone.utc).isoformat(),
        "phase": "finalizer",
        "event": event,
        **fields,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    print("[progress] " + json.dumps(row, ensure_ascii=False), flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", type=Path, required=True, help="existing POC05 audit directory")
    ap.add_argument("--preflight", type=Path, required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--max-tokens", type=int, default=384)
    args = ap.parse_args(argv)

    run = args.run.resolve()
    if not run.is_dir() or not (run / "result.json").is_file():
        raise ValueError("--run must contain POC05 result.json")
    if not 60 <= args.timeout <= 300:
        raise ValueError("timeout must be between 60 and 300 seconds")
    if not 128 <= args.max_tokens <= 512:
        raise ValueError("max-tokens must be between 128 and 512")

    stored = load(run / "result.json")
    if stored.get("model") != args.model or stored.get("base_url") != args.base_url:
        raise ValueError("stored run/model/base-url mismatch")

    ok, mechanic_errors = mechanics_ok(stored)
    if not ok:
        raise ValueError("stored POC05 mechanics not eligible: " + ", ".join(mechanic_errors))

    phase1_trace = s.collect_trace(run / "phase1")
    phase2_trace = s.collect_trace(run / "phase2")
    app_evidence = best_result(phase1_trace, "app.log")
    system_evidence = best_result(phase2_trace, "system.log")
    robot_evidence = best_result(phase2_trace, "robot.log")

    pf = args.preflight.resolve()
    _ref, _cfg, _case, key_env = p2.bundle(pf)
    api_key = os.environ.get(key_env, "")
    if "\n" in api_key or "\r" in api_key:
        raise ValueError("invalid local API key")

    system_prompt, user_prompt = build_prompts(app_evidence, system_evidence, robot_evidence)
    body = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "max_tokens": args.max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": {"enable_thinking": False},
    }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = run / ("finalize-resume-" + stamp)
    out.mkdir(mode=0o700)
    save(out / "request.json", body)
    progress_path = out / "progress.jsonl"
    emit_progress(progress_path, "INVESTIGATION_COMPLETED", reason="resume_evidence_sufficient")
    emit_progress(progress_path, "FINALIZATION_STARTED", tools_enabled=False)

    response = f3.stream_finalizer(args.base_url, api_key, body, args.timeout)
    (out / "response.txt").write_text(response["content"], encoding="utf-8")
    signals = final_signals(response["content"])

    finish_stop = bool(response.get("finish_reasons")) and response["finish_reasons"][-1] == "stop"
    final_ok = (
        response.get("done_seen") is True
        and finish_stop
        and bool(response.get("content", "").strip())
        and signals["system_status_137"]
        and signals["robot_window_healthy"]
        and not signals["unsupported_oom_assertion"]
    )

    if final_ok:
        emit_progress(progress_path, "FINALIZATION_COMPLETED", elapsed_s=response.get("elapsed_s"))
        emit_progress(progress_path, "TASK_COMPLETED", source="fresh_finalizer")
    else:
        emit_progress(progress_path, "TASK_ENDED_WITH_ERROR", source="fresh_finalizer")

    result = {
        "source_run": str(run),
        "original_status": stored.get("status"),
        "model": args.model,
        "session_key": stored.get("session_key"),
        "stored_mechanics_pass": ok,
        "stored_mechanics_errors": mechanic_errors,
        "evidence": {
            "app_chars": len(app_evidence),
            "system_chars": len(system_evidence),
            "robot_chars": len(robot_evidence),
        },
        "finalizer": {k: v for k, v in response.items() if k != "content"},
        "answer_signals": signals,
        "final_answer": response.get("content"),
        "passed": final_ok,
        "status": "PASS_POC05_PROGRESS_STOP_RESUME_FINALIZED" if final_ok else "POC05_FINALIZER_FAILED",
        "note": "Reused stored stop/resume/tool audit; no OpenClaw/tool replay. One fresh no-tool finalizer call only.",
    }
    save(out / "result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("poc05 finalizer audit:", out)
    return 0 if final_ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError) as exc:
        print("POC05_FINALIZE_SETUP_ERROR:", str(exc), file=sys.stderr)
        sys.exit(2)
