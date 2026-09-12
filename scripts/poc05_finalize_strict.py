#!/usr/bin/env python3
"""Strict finalizer for an existing POC05 stop/resume audit.

Reuses the already-proven stop/resume/tool evidence and submits exactly one
fresh no-tool finalizer request. Compared with the first POC05 finalizer, this
variant also enforces evidence-strength rules: status 137 is a fact, but its
trigger mechanism remains unknown unless directly evidenced; robot evidence is
limited to the observed log window.
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
import poc05_finalize_resume as base


def build_prompts(app_evidence: str, system_evidence: str, robot_evidence: str):
    system = """你是设备诊断任务的最终结果整理器。调查阶段已经结束，不存在任何工具。
只能使用下面已经取得的证据，不要继续调查，也不要补造根因。

证据强度规则：
1. inference-worker 以 status=137 退出是事实；137 的具体触发机制必须标记为未知。
2. 不得声称或比较 OOM、显存不足、资源竞争、内部错误谁“更可能/最可能”；现有证据不支持这种排序。
3. GPU 显存 88% 只能作为观测事实，不能单独推出 OOM。
4. robot.log 只能支持“当前日志窗口未见机器人侧异常”，不得写成“机器人完全健康”或“排除机器人原因”。
5. 区分事实、推断和未知；对因果关系使用与证据强度匹配的措辞。

输出简短中文结论，不要 Markdown 表格。"""
    user = f"""原任务：分析 2026-09-12 10:15 左右任务执行失败的现场证据。
用户恢复任务后要求优先检查 system.log，再检查 robot.log，并保留停止前 app.log 证据。

【停止前 app.log 证据】
{app_evidence}

【恢复后 system.log 证据】
{system_evidence}

【恢复后 robot.log 证据】
{robot_evidence}

只整理：
1. 已确认事实；
2. 当前最有证据支持的解释；
3. 明确列出 status=137 触发机制等仍未知事项。
控制在 300 中文字左右。"""
    return system, user


def final_signals(text: str) -> dict:
    lower = text.lower()
    system_status_137 = bool(re.search(
        r"(?:status\s*=?\s*137|状态\s*=?\s*137|退出(?:状态)?\s*=?\s*137)",
        text, re.I,
    ))
    robot_window_healthy = (
        "joint_fault_code=0" in lower
        or "heartbeat=ok" in lower
        or bool(re.search(r"(?:当前|该|此).{0,10}(?:窗口|时段).{0,24}机器人.{0,28}(?:未见异常|无故障码|状态正常|正常)", text, re.I))
        or bool(re.search(r"机器人.{0,30}(?:当前|该|此).{0,10}(?:窗口|时段).{0,24}(?:未见异常|无故障码|状态正常)", text, re.I))
    )

    clauses = [c.strip() for c in re.split(r"[。；;，,\n]|(?:但是|然而|不过|however|yet)", text, flags=re.I) if c.strip()]
    uncertainty = re.compile(
        r"(?:不能|无法|未知|未确认|尚未确认|不能确认|无法确认|证据不足|未有证据|"
        r"unknown|unproven|cannot|can't|not proven|no evidence|insufficient evidence)",
        re.I,
    )

    unsupported_oom = False
    unsupported_cause_ranking = False
    unsupported_robot_exclusion = False
    cause_terms = re.compile(
        r"(?:oom|out of memory|显存不足|内存不足|资源竞争|内部错误|resource contention|internal error)",
        re.I,
    )
    ranking = re.compile(r"(?:更可能|最可能|很可能|大概率|likely|probably|most likely)", re.I)
    causal = re.compile(r"(?:导致|造成|引起|根因|cause|caused|root cause)", re.I)

    for clause in clauses:
        if re.search(r"(?:机器人|robot).{0,24}(?:完全健康|排除.{0,8}(?:原因|故障)|ruled out|rule out)", clause, re.I):
            unsupported_robot_exclusion = True
        if not cause_terms.search(clause):
            continue
        if uncertainty.search(clause):
            continue
        if "oom" in clause.lower() and causal.search(clause):
            unsupported_oom = True
        if ranking.search(clause) or causal.search(clause):
            unsupported_cause_ranking = True

    status137_cause_unknown = bool(re.search(
        r"(?:137|inference-worker).{0,80}(?:未知|不能确认|无法确认|未确认|证据不足|unknown|unproven|cannot confirm)",
        text, re.I | re.S,
    ) or re.search(
        r"(?:未知|不能确认|无法确认|未确认|证据不足|unknown|unproven).{0,80}(?:137|inference-worker)",
        text, re.I | re.S,
    ))

    return {
        "system_status_137": system_status_137,
        "robot_window_healthy": robot_window_healthy,
        "status137_cause_unknown": status137_cause_unknown,
        "unsupported_oom_assertion": unsupported_oom,
        "unsupported_unproven_cause_ranking": unsupported_cause_ranking,
        "unsupported_robot_exclusion": unsupported_robot_exclusion,
    }


def save(path: Path, obj):
    with path.open("x", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", type=Path, required=True)
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

    stored = base.load(run / "result.json")
    if stored.get("model") != args.model or stored.get("base_url") != args.base_url:
        raise ValueError("stored run/model/base-url mismatch")
    ok, mechanic_errors = base.mechanics_ok(stored)
    if not ok:
        raise ValueError("stored POC05 mechanics not eligible: " + ", ".join(mechanic_errors))

    phase1_trace = s.collect_trace(run / "phase1")
    phase2_trace = s.collect_trace(run / "phase2")
    app_evidence = base.best_result(phase1_trace, "app.log")
    system_evidence = base.best_result(phase2_trace, "system.log")
    robot_evidence = base.best_result(phase2_trace, "robot.log")

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
    out = run / ("finalize-strict-" + stamp)
    out.mkdir(mode=0o700)
    save(out / "request.json", body)
    progress = out / "progress.jsonl"
    base.emit_progress(progress, "INVESTIGATION_COMPLETED", reason="resume_evidence_sufficient")
    base.emit_progress(progress, "FINALIZATION_STARTED", tools_enabled=False, evidence_strength="strict")

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
        and signals["status137_cause_unknown"]
        and not signals["unsupported_oom_assertion"]
        and not signals["unsupported_unproven_cause_ranking"]
        and not signals["unsupported_robot_exclusion"]
    )

    if final_ok:
        base.emit_progress(progress, "FINALIZATION_COMPLETED", elapsed_s=response.get("elapsed_s"))
        base.emit_progress(progress, "TASK_COMPLETED", source="strict_fresh_finalizer")
    else:
        base.emit_progress(progress, "TASK_ENDED_WITH_ERROR", source="strict_fresh_finalizer")

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
        "status": "PASS_POC05_PROGRESS_STOP_RESUME_STRICT" if final_ok else "POC05_STRICT_FINALIZER_FAILED",
        "note": "Reused stored stop/resume/tool audit; no OpenClaw/tool replay. One strict fresh no-tool finalizer call only.",
    }
    save(out / "result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("poc05 strict finalizer audit:", out)
    return 0 if final_ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError) as exc:
        print("POC05_STRICT_FINALIZE_SETUP_ERROR:", str(exc), file=sys.stderr)
        sys.exit(2)
