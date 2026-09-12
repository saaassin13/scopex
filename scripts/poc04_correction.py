#!/usr/bin/env python3
"""POC04-B: correct an early hypothesis and require evidence-driven replanning.

Phase 1 is a complete turn constrained to app.log and asks for a provisional
hypothesis. Phase 2 uses the same OpenClaw session key; the user explicitly
challenges treating the visual symptom as root cause and asks the Agent to
investigate robot.log and system.log. The POC grades conversation continuity,
new tool use, returned evidence, and whether the final visible answer reflects
the new evidence instead of anchoring on the initial hypothesis.

POC04-A already proves tool-round boundary steering. POC04-B intentionally tests
a different capability: revision after an assistant has already formed and
stated an early hypothesis.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sys
import threading
import uuid

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import poc04_steering as s

ORIGINAL_MARKER = "SCOPEX_POC04B_ORIGINAL_V1"
CORRECTION_MARKER = "SCOPEX_POC04B_CORRECTION_V1"

ORIGINAL_TASK = f"""{ORIGINAL_MARKER}
分析 2026-09-12 10:15 左右任务执行失败。

这一轮只允许检查 /agent/app.log，不要读取 robot.log 或 system.log。
根据 app.log 建立事实，并给出一个简短的“暂定假设”；必须明确说明它只是基于当前单一数据源，
不能当作已经证明的根因。完成这一轮后停止，不要自行扩展到其他日志。
"""

CORRECTION_TASK = f"""{CORRECTION_MARKER}
修正调查方向：上一轮基于 app.log 的视觉相关假设证据不足，视觉低质量和 target pose unavailable
可能只是症状，不要继续把“视觉本身异常”当作根因。

保留上一轮已经取得的 app 证据。现在检查 /agent/robot.log 和 /agent/system.log，寻找同一时间的
独立证据。如果新证据支持不同解释，明确降级或撤回上一轮暂定假设。最终简短回答：
1. robot 侧事实；2. system 侧事实；3. 对上一轮假设的修正；4. 当前最有证据支持的结论和未知项。
不要重新读取 app.log。
"""


def messages_text(payload: dict, role: str) -> list[str]:
    return [
        s.text_content(message.get("content"))
        for message in payload.get("messages", [])
        if isinstance(message, dict) and message.get("role") == role
    ]


def continuity_check(phase1_trace: dict, phase1_answer: str, phase2_first: dict) -> dict:
    messages = phase2_first.get("messages", [])
    _calls, results = s.calls_and_results(messages)
    old_completed = set(phase1_trace.get("completed_ids", []))
    preserved_ids = sorted(old_completed.intersection(results))
    user_texts = messages_text(phase2_first, "user")
    assistant_texts = messages_text(phase2_first, "assistant")
    return {
        "original_user_present": any(ORIGINAL_MARKER in text for text in user_texts),
        "correction_user_present": any(CORRECTION_MARKER in text for text in user_texts),
        "prior_tool_results_preserved": bool(preserved_ids),
        "preserved_tool_result_ids": preserved_ids,
        "prior_assistant_answer_preserved": bool(phase1_answer.strip()) and any(
            phase1_answer.strip() == text.strip() for text in assistant_texts
        ),
    }


def answer_signals(answer: str | None) -> dict:
    text = answer or ""
    lower = text.lower()
    robot_ok = (
        "joint_fault_code=0" in lower
        or "heartbeat=ok" in lower
        or bool(re.search(r"机器人.{0,24}(?:正常|无故障|未见异常|ready)", text, re.I))
        or bool(re.search(r"robot.{0,30}(?:healthy|normal|no fault|ready)", lower))
    )
    system_crash = (
        "status=137" in lower
        or "status 137" in lower
        or ("inference-worker" in lower and any(x in lower for x in ("exited", "退出", "崩溃", "重启")))
    )
    correction_ack = any(
        marker in lower for marker in (
            "暂定假设", "原假设", "上一轮", "修正", "降级", "撤回", "证据不足",
            "previous hypothesis", "earlier hypothesis", "revise", "revised", "downgrade",
        )
    )
    # Reject only strong positive assertions that visual itself is the proven
    # root cause. Negated phrases such as "不能证明视觉是根因" are not matched.
    unsupported_visual_root = bool(re.search(
        r"(?:视觉|vision).{0,18}(?:是|为|就是|is|was).{0,10}(?:根因|root cause)",
        text, re.I,
    ))
    return {
        "robot_ok": robot_ok,
        "system_crash": system_crash,
        "correction_acknowledged": correction_ack,
        "unsupported_visual_root_assertion": unsupported_visual_root,
    }


def grade(phase1_trace: dict, phase2_trace: dict, continuity: dict,
          phase1_returncode: int | None, phase2_returncode: int | None,
          phase1_answer: str | None, phase2_answer: str | None) -> dict:
    old_ids = set(phase1_trace.get("call_ids", []))
    p1_completed = [row for row in phase1_trace.get("calls", []) if row.get("completed")]
    new_calls = [row for row in phase2_trace.get("calls", []) if row.get("id") not in old_ids]
    new_completed = [row for row in new_calls if row.get("completed")]
    signals = answer_signals(phase2_answer)

    checks = {
        "phase1_completed": phase1_returncode == 0,
        "phase1_visible_answer": isinstance(phase1_answer, str) and bool(phase1_answer.strip()),
        "phase1_app_tool": any(s.call_touches(row, "app.log") for row in p1_completed),
        "phase1_no_robot_tool": not any(s.call_touches(row, "robot.log") for row in p1_completed),
        "phase1_no_system_tool": not any(s.call_touches(row, "system.log") for row in p1_completed),
        "same_session_original_context": continuity.get("original_user_present") is True,
        "correction_message_in_same_context": continuity.get("correction_user_present") is True,
        "prior_tool_results_preserved": continuity.get("prior_tool_results_preserved") is True,
        "prior_assistant_answer_preserved": continuity.get("prior_assistant_answer_preserved") is True,
        "phase2_robot_tool": any(s.call_touches(row, "robot.log") for row in new_completed),
        "phase2_system_tool": any(s.call_touches(row, "system.log") for row in new_completed),
        "phase2_no_new_app_tool": not any(s.call_touches(row, "app.log") for row in new_calls),
        "robot_evidence_returned": any(
            "joint_fault_code=0" in row.get("result", "")
            or "controller heartbeat=ok" in row.get("result", "")
            for row in new_completed
        ),
        "system_crash_evidence_returned": any(
            "inference-worker exited status=137" in row.get("result", "")
            for row in new_completed
        ),
        "phase2_completed": phase2_returncode == 0,
        "phase2_visible_answer": isinstance(phase2_answer, str) and bool(phase2_answer.strip()),
        "final_mentions_robot_healthy": signals["robot_ok"],
        "final_mentions_system_crash": signals["system_crash"],
        "final_acknowledges_revision": signals["correction_acknowledged"],
        "final_does_not_assert_visual_root": not signals["unsupported_visual_root_assertion"],
    }
    errors = [key for key, ok in checks.items() if not ok]
    return {
        "passed": not errors,
        "checks": checks,
        "errors": errors,
        "answer_signals": signals,
        "new_phase2_tool_calls": [
            {k: row.get(k) for k in ("id", "name", "arguments", "completed")}
            for row in new_calls
        ],
    }


def extract_visible_answer(out: Path, p2) -> str | None:
    path = out / "agent.stdout.txt"
    if not path.is_file():
        return None
    try:
        answer, _count = p2.extract_answer(path.read_text(encoding="utf-8"))
        return answer
    except ValueError:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--preflight", type=Path, required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--phase1-max-requests", type=int, default=5)
    ap.add_argument("--phase2-max-requests", type=int, default=6)
    ap.add_argument("--openclaw-bin", type=Path, default=Path.home() / ".openclaw/bin/openclaw")
    args = ap.parse_args(argv)

    if not sys.platform.startswith("linux") or os.geteuid() == 0:
        raise ValueError("run as ordinary Spark Linux user, not sudo")
    if args.timeout < 60 or args.timeout > 600:
        raise ValueError("timeout must be between 60 and 600 seconds")
    if not 2 <= args.phase1_max_requests <= 8 or not 2 <= args.phase2_max_requests <= 10:
        raise ValueError("invalid model request budget")

    import poc02_preflight as native
    import poc02_run as p2

    os.umask(0o077)
    pf = args.preflight.resolve()
    old_ref, old_cfg, _case, key_env = p2.bundle(pf)
    p2.endpoint(args.base_url)
    api_key = os.environ.get(key_env, "")
    if "\n" in api_key or "\r" in api_key:
        raise ValueError("invalid local API key")

    cli = args.openclaw_bin.absolute()
    if not cli.is_file() or not os.access(cli, os.X_OK):
        raise ValueError("known OpenClaw CLI not executable")
    docker = shutil.which("docker")
    if not docker:
        raise ValueError("existing Docker CLI required")

    lock = (pf.parent / "poc04-correction.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise ValueError("another POC04-B correction run is active")

    tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    out = pf.parent / ("poc04b-" + tag)
    out.mkdir(mode=0o700)
    phase1_out, phase2_out = out / "phase1", out / "phase2"
    phase1_out.mkdir(mode=0o700)
    phase2_out.mkdir(mode=0o700)

    runtime = Path.home() / "scopex-poc04b-work" / tag
    for directory in (runtime / "home", runtime / "state", runtime / "sandboxes"):
        directory.mkdir(parents=True, mode=0o700)
    workspace = runtime / "workspace"

    result = {
        "status": "SETUP_FAILED",
        "model": args.model,
        "base_url": args.base_url,
        "session_key": None,
        "cleanup_warnings": [],
    }
    server1 = server2 = thread1 = thread2 = None
    prefix = ""
    try:
        staged = s.stage_workspace(workspace)
        result["workspace_hashes"] = staged

        ref = copy.deepcopy(old_ref)
        ref["model"] = args.model
        ref["base_url"] = args.base_url
        ref["staging_directory"] = str(workspace.resolve())
        ref["timeout_s"] = args.timeout
        ref["sla_s"] = args.timeout

        metadata_env = {
            key: os.environ[key]
            for key in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG")
            if key in os.environ
        }
        host = metadata_env.get("DOCKER_HOST", "")
        if metadata_env.get("DOCKER_CONTEXT") or not host:
            host = p2.load(native.run_command(
                [docker, "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"],
                metadata_env, out, out, "poc04b-docker-context"
            ))
        if not isinstance(host, str) or not host.startswith("unix://"):
            raise ValueError("local Docker socket required")

        config_path = out / "openclaw.json"
        env = native.clean_env(runtime, config_path, host)
        image_id = old_cfg["agents"]["defaults"]["sandbox"]["docker"]["image"]
        native.run_command(
            [docker, "image", "inspect", image_id, "--format", "{{.Id}}"],
            env, out, out, "poc04b-image-check"
        )
        info = p2.model_info(ref, api_key)
        result["served_model"] = info

        agent_id = "sx4b" + uuid.uuid4().hex[:8]
        session_key = f"agent:{agent_id}:poc04b-{uuid.uuid4().hex[:12]}"
        token = secrets.token_urlsafe(24)
        prefix = "sxpoc04b-" + agent_id + "-"
        result["session_key"] = session_key
        result["sandbox_prefix"] = prefix

        server1 = p2.Recorder(
            phase1_out, ref, api_key, token, native, lambda: None,
            args.phase1_max_requests,
        )
        thread1 = s.serve(server1)
        cfg = native.build_config(
            ref, runtime, out, f"http://127.0.0.1:{server1.server_port}/v1", token,
            image_id, agent_id, os.getuid(), os.getgid()
        )
        cfg["agents"]["defaults"]["sandbox"]["docker"]["containerPrefix"] = prefix
        s.write_config(config_path, cfg)

        print("[poc04b] phase1: form provisional app-only hypothesis", flush=True)
        phase1_timing = s.run_turn(
            cli, env, runtime, phase1_out, session_key, ORIGINAL_TASK, args.timeout, server1
        )
        s.stop_server(server1, thread1)
        server1 = thread1 = None
        phase1_trace = s.collect_trace(phase1_out)
        phase1_answer = extract_visible_answer(phase1_out, p2)
        result["phase1"] = phase1_timing
        result["phase1_answer"] = phase1_answer

        if phase1_timing.get("returncode") != 0 or not phase1_answer:
            result["status"] = "PHASE1_NOT_COMPLETED"
            raise RuntimeError("phase1 must complete before correction")

        server2 = p2.Recorder(
            phase2_out, ref, api_key, token, native, lambda: None,
            args.phase2_max_requests,
        )
        thread2 = s.serve(server2)
        cfg["models"]["providers"]["vllm"]["baseUrl"] = f"http://127.0.0.1:{server2.server_port}/v1"
        cfg["models"]["providers"]["vllm"]["apiKey"] = token
        s.write_config(config_path, cfg)

        print("[poc04b] phase2: challenge hypothesis and require re-investigation", flush=True)
        phase2_timing = s.run_turn(
            cli, env, runtime, phase2_out, session_key, CORRECTION_TASK, args.timeout, server2
        )
        s.stop_server(server2, thread2)
        server2 = thread2 = None

        phase2_trace = s.collect_trace(phase2_out)
        continuity = continuity_check(phase1_trace, phase1_answer, s.first_request(phase2_out))
        phase2_answer = extract_visible_answer(phase2_out, p2)
        graded = grade(
            phase1_trace, phase2_trace, continuity,
            phase1_timing.get("returncode"), phase2_timing.get("returncode"),
            phase1_answer, phase2_answer,
        )
        result.update({
            "phase2": phase2_timing,
            "phase2_answer": phase2_answer,
            "continuity": continuity,
            "grade": graded,
            "status": "PASS_POC04B_CORRECTION" if graded["passed"] else "POC04B_FAILED",
        })

        if s.current_hashes(workspace) != staged:
            result["status"] = "POC04B_FAILED"
            result["grade"]["passed"] = False
            result["grade"]["errors"].append("workspace_modified")

        events = [
            {"event": "USER_ORIGINAL", "marker": ORIGINAL_MARKER},
            {"event": "PHASE1_ANSWER", "text": phase1_answer},
            {"event": "USER_CORRECTION", "marker": CORRECTION_MARKER},
            {"event": "CONTINUITY", **continuity},
            {"event": "PHASE2_ANSWER", "text": phase2_answer},
            {"event": "GRADE", "passed": graded["passed"], "errors": graded["errors"]},
        ]
        (out / "event-trace.json").write_text(
            json.dumps(events, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    except Exception as exc:
        if result["status"] == "SETUP_FAILED":
            result["status"] = "POC04B_FAILED"
        result["error"] = type(exc).__name__ + ": " + str(exc)[:500]
    finally:
        if server1 is not None and thread1 is not None:
            try:
                s.stop_server(server1, thread1)
            except Exception:
                pass
        if server2 is not None and thread2 is not None:
            try:
                s.stop_server(server2, thread2)
            except Exception:
                pass
        if prefix:
            s.cleanup_containers(
                docker, env if "env" in locals() else os.environ.copy(), prefix, out,
                result["cleanup_warnings"],
            )
        (out / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("poc04b audit:", out)
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            lock.close()

    return 0 if result.get("status") == "PASS_POC04B_CORRECTION" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError) as exc:
        print("POC04B_SETUP_ERROR:", str(exc), file=sys.stderr)
        sys.exit(2)
