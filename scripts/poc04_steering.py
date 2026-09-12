#!/usr/bin/env python3
"""POC04-A: interrupt an investigation at a tool-round boundary and steer it.

This POC validates one capability only: a second user instruction must reuse the
same OpenClaw session, preserve evidence already gathered in the first turn,
and change subsequent tool use from app.log to robot.log/system.log.

The first local turn is deliberately stopped *before* its next model inference
once at least two completed tool results (including app.log evidence) are visible
in the request history. No running shell/process is killed mid-command. A second
turn is then sent with the same unique session key.

POC02 remains the security prerequisite; POC03 remains the autonomous diagnosis
and two-phase finalization proof. POC04 does not grade root-cause correctness.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "poc04"
FILES = ("app.log", "robot.log", "system.log")
ORIGINAL_MARKER = "SCOPEX_POC04_ORIGINAL_V1"
STEER_MARKER = "SCOPEX_POC04_STEER_V1"

ORIGINAL_TASK = f"""{ORIGINAL_MARKER}
分析 2026-09-12 10:15 左右任务执行失败的现场证据。

先从 /agent/app.log 开始建立初步事实；在 app.log 的失败链还没看清之前，
不要读取 robot.log 或 system.log。不要停在第一条 ERROR，自主选择 read/grep/exec
获取必要上下文。不要预设深层根因，也不要为了快速结束而提前给最终结论。
"""

STEERING_TASK = f"""{STEER_MARKER}
暂停继续调查 app.log。保留刚才已经取得的 app 证据，从现在开始不要再读取 app.log。
优先检查 /agent/robot.log 和 /agent/system.log，判断同一时间机器人状态或系统服务是否
存在独立异常。不要沿着“视觉本身异常”这个早期假设继续推断；根据新证据重新规划。
检查完成后综合前后证据，明确区分事实、推断和未知，并给出简短最终结论。
"""


class SteeringBoundary(RuntimeError):
    pass


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def text_content(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            part.get("text", "")
            for part in value
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def stage_workspace(workspace: Path) -> dict:
    workspace.mkdir(parents=True, mode=0o700)
    hashes = {}
    for name in FILES:
        src = FIXTURE / name
        if src.is_symlink() or not src.is_file():
            raise ValueError(f"missing POC04 fixture: {src}")
        dst = workspace / name
        shutil.copy2(src, dst)
        hashes[name] = sha_bytes(dst.read_bytes())
    return hashes


def current_hashes(workspace: Path) -> dict:
    return {name: sha_bytes((workspace / name).read_bytes()) for name in FILES}


def calls_and_results(messages: list[dict]):
    calls: dict[str, dict] = {}
    results: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict) or not isinstance(call.get("id"), str):
                    continue
                func = call.get("function") or {}
                try:
                    args = json.loads(func.get("arguments", "{}"))
                except (TypeError, ValueError):
                    args = {}
                calls[call["id"]] = {
                    "id": call["id"],
                    "name": func.get("name"),
                    "arguments": args if isinstance(args, dict) else {},
                }
        elif message.get("role") == "tool" and isinstance(message.get("tool_call_id"), str):
            results[message["tool_call_id"]] = text_content(message.get("content"))
    return calls, results


def completed_calls(messages: list[dict]) -> list[dict]:
    calls, results = calls_and_results(messages)
    rows = []
    for cid, call in calls.items():
        if cid in results:
            rows.append({**call, "result": results[cid]})
    return rows


def call_touches(call: dict, filename: str) -> bool:
    args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
    haystack = json.dumps(args, ensure_ascii=False).lower()
    return filename.lower() in haystack


def collect_trace(out: Path) -> dict:
    calls: dict[str, dict] = {}
    results: dict[str, str] = {}
    request_count = 0
    for path in sorted(out.glob("wire-*-request.json")):
        request_count += 1
        payload = json.loads(path.read_text(encoding="utf-8"))
        c, r = calls_and_results(payload.get("messages", []))
        calls.update(c)
        results.update(r)
    rows = []
    for cid, call in calls.items():
        rows.append({
            **call,
            "completed": cid in results,
            "result": results.get(cid, ""),
        })
    return {
        "request_count": request_count,
        "calls": rows,
        "call_ids": sorted(calls),
        "completed_ids": sorted(results),
    }


def first_request(out: Path) -> dict:
    path = out / "wire-01-request.json"
    if not path.is_file():
        raise ValueError(f"missing first request: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def history_check(phase1_trace: dict, phase2_first: dict) -> dict:
    messages = phase2_first.get("messages", [])
    calls, results = calls_and_results(messages)
    old_completed = set(phase1_trace.get("completed_ids", []))
    preserved_ids = sorted(old_completed.intersection(results))
    user_texts = [
        text_content(m.get("content"))
        for m in messages
        if isinstance(m, dict) and m.get("role") == "user"
    ]
    return {
        "original_user_present": any(ORIGINAL_MARKER in text for text in user_texts),
        "steering_user_present": any(STEER_MARKER in text for text in user_texts),
        "preserved_tool_result_ids": preserved_ids,
        "preserved_tool_results": bool(preserved_ids),
        "phase2_history_tool_call_count": len(calls),
        "phase2_history_tool_result_count": len(results),
    }


def grade(phase1_trace: dict, phase2_trace: dict, continuity: dict,
          boundary: dict, phase2_returncode: int | None, final_answer: str | None) -> dict:
    old_ids = set(phase1_trace.get("call_ids", []))
    new_calls = [row for row in phase2_trace.get("calls", []) if row.get("id") not in old_ids]
    new_completed = [row for row in new_calls if row.get("completed")]

    p1_completed = [row for row in phase1_trace.get("calls", []) if row.get("completed")]
    checks = {
        "boundary_reached_after_real_tools": boundary.get("reached") is True
            and boundary.get("completed_tool_results", 0) >= 2,
        "phase1_app_evidence": any(call_touches(row, "app.log") for row in p1_completed),
        "phase1_not_already_robot": not any(call_touches(row, "robot.log") for row in p1_completed),
        "phase1_not_already_system": not any(call_touches(row, "system.log") for row in p1_completed),
        "same_session_original_context": continuity.get("original_user_present") is True,
        "steering_message_in_same_context": continuity.get("steering_user_present") is True,
        "prior_tool_results_preserved": continuity.get("preserved_tool_results") is True,
        "post_steer_robot_tool": any(call_touches(row, "robot.log") for row in new_completed),
        "post_steer_system_tool": any(call_touches(row, "system.log") for row in new_completed),
        "post_steer_no_new_app_tool": not any(call_touches(row, "app.log") for row in new_calls),
        "robot_evidence_returned": any(
            "joint_fault_code=0" in row.get("result", "")
            or "controller heartbeat=ok" in row.get("result", "")
            for row in new_completed
        ),
        "system_evidence_returned": any(
            "inference-worker exited status=137" in row.get("result", "")
            or "inference-worker health=ready" in row.get("result", "")
            for row in new_completed
        ),
        "second_turn_completed": phase2_returncode == 0,
        "final_visible_answer": isinstance(final_answer, str) and bool(final_answer.strip()),
    }
    errors = [key for key, ok in checks.items() if not ok]
    return {
        "passed": not errors,
        "checks": checks,
        "errors": errors,
        "new_phase2_tool_calls": [
            {k: row.get(k) for k in ("id", "name", "arguments", "completed")}
            for row in new_calls
        ],
    }


def run_turn(cli: Path, env: dict, runtime: Path, out: Path, session_key: str,
             message: str, seconds: int, server) -> dict:
    task = out / "task.txt"
    task.write_text(message, encoding="utf-8")
    start = time.monotonic()
    server.started = start
    server.deadline = start + seconds
    code = None
    stop = None
    with (out / "agent.stdout.txt").open("xb") as stdout, \
         (out / "agent.stderr.txt").open("xb") as stderr:
        proc = subprocess.Popen(
            [
                str(cli), "agent", "--local",
                "--session-key", session_key,
                "--thinking", "off",
                "--timeout", str(seconds),
                "--json", "--message-file", str(task),
            ],
            env=env,
            cwd=runtime,
            stdout=stdout,
            stderr=stderr,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            code = proc.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            stop = "timeout"
            server.cancel()
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        finally:
            server.cancel()
    return {
        "returncode": code,
        "stop": stop,
        "wall_s": round(time.monotonic() - start, 4),
    }


def serve(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def stop_server(server, thread):
    server.cancel()
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def write_config(path: Path, cfg: dict):
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cleanup_containers(docker: str, env: dict, prefix: str, out: Path, warnings: list[str]):
    try:
        proc = subprocess.run(
            [docker, "ps", "-aq", "--filter", f"name={prefix}"],
            env=env, cwd=out, capture_output=True, text=True, timeout=15, check=False,
        )
        ids = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        for cid in ids:
            subprocess.run([docker, "stop", "--time", "2", cid], env=env, cwd=out,
                           capture_output=True, timeout=10, check=False)
            subprocess.run([docker, "rm", "-f", cid], env=env, cwd=out,
                           capture_output=True, timeout=10, check=False)
    except Exception as exc:
        warnings.append("sandbox cleanup failed: " + type(exc).__name__ + ": " + str(exc)[:200])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--preflight", type=Path, required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--phase2-max-requests", type=int, default=6)
    ap.add_argument("--openclaw-bin", type=Path, default=Path.home() / ".openclaw/bin/openclaw")
    args = ap.parse_args(argv)

    if not sys.platform.startswith("linux") or os.geteuid() == 0:
        raise ValueError("run as ordinary Spark Linux user, not sudo")
    if args.timeout < 60 or args.timeout > 600:
        raise ValueError("timeout must be between 60 and 600 seconds")
    if args.phase2_max_requests < 2 or args.phase2_max_requests > 10:
        raise ValueError("phase2-max-requests must be between 2 and 10")

    import poc02_preflight as native
    import poc02_run as p2

    os.umask(0o077)
    pf = args.preflight.resolve()
    old_ref, _old_cfg, _case, key_env = p2.bundle(pf)
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

    lock = (pf.parent / "poc04-steering.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise ValueError("another POC04 steering run is active")

    tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    out = pf.parent / ("poc04-" + tag)
    out.mkdir(mode=0o700)
    phase1_out = out / "phase1"
    phase2_out = out / "phase2"
    phase1_out.mkdir(mode=0o700)
    phase2_out.mkdir(mode=0o700)

    runtime = Path.home() / "scopex-poc04-work" / tag
    for directory in (runtime / "home", runtime / "state", runtime / "sandboxes"):
        directory.mkdir(parents=True, mode=0o700)
    workspace = runtime / "workspace"

    result = {
        "status": "SETUP_FAILED",
        "model": args.model,
        "base_url": args.base_url,
        "session_key": None,
        "boundary": {},
        "cleanup_warnings": [],
    }

    prefix = ""
    server1 = server2 = thread1 = thread2 = None
    try:
        staged = stage_workspace(workspace)
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
                metadata_env, out, out, "poc04-docker-context"
            ))
        if not isinstance(host, str) or not host.startswith("unix://"):
            raise ValueError("local Docker socket required")

        config_path = out / "openclaw.json"
        env = native.clean_env(runtime, config_path, host)
        image_id = _old_cfg["agents"]["defaults"]["sandbox"]["docker"]["image"]
        native.run_command(
            [docker, "image", "inspect", image_id, "--format", "{{.Id}}"],
            env, out, out, "poc04-image-check"
        )

        info = p2.model_info(ref, api_key)
        result["served_model"] = info
        if info.get("max_model_len") is not None and info["max_model_len"] < 32768:
            raise ValueError("served model context shorter than 32768")

        agent_id = "sx4" + uuid.uuid4().hex[:9]
        session_key = f"agent:{agent_id}:poc04-{uuid.uuid4().hex[:12]}"
        token = secrets.token_urlsafe(24)
        prefix = "sxpoc04-" + agent_id + "-"
        result["session_key"] = session_key
        result["sandbox_prefix"] = prefix

        boundary = {
            "reached": False,
            "request_index": None,
            "completed_tool_results": 0,
            "app_completed_tools": 0,
        }
        holder = {"server": None}

        def phase1_gate():
            server = holder["server"]
            idx = len(server.records)
            payload = json.loads((phase1_out / f"wire-{idx:02d}-request.json").read_text(encoding="utf-8"))
            done = completed_calls(payload.get("messages", []))
            app_done = [row for row in done if call_touches(row, "app.log")]
            if len(done) >= 2 and app_done:
                boundary.update({
                    "reached": True,
                    "request_index": idx,
                    "completed_tool_results": len(done),
                    "app_completed_tools": len(app_done),
                })
                raise SteeringBoundary("POC04 safe steering boundary reached")

        server1 = p2.Recorder(phase1_out, ref, api_key, token, native, phase1_gate, 8)
        holder["server"] = server1
        thread1 = serve(server1)

        cfg = native.build_config(
            ref, runtime, out, f"http://127.0.0.1:{server1.server_port}/v1", token,
            image_id, agent_id, os.getuid(), os.getgid()
        )
        cfg["agents"]["defaults"]["sandbox"]["docker"]["containerPrefix"] = prefix
        write_config(config_path, cfg)

        print("[poc04] phase1: start initial investigation", flush=True)
        phase1_timing = run_turn(
            cli, env, runtime, phase1_out, session_key, ORIGINAL_TASK, args.timeout, server1
        )
        stop_server(server1, thread1)
        server1 = thread1 = None
        result["phase1"] = phase1_timing
        result["boundary"] = boundary

        phase1_trace = collect_trace(phase1_out)
        result["phase1_trace"] = {
            "request_count": phase1_trace["request_count"],
            "call_count": len(phase1_trace["calls"]),
            "completed_count": len(phase1_trace["completed_ids"]),
        }
        if not boundary["reached"]:
            result["status"] = "STEERING_BOUNDARY_NOT_REACHED"
            raise SteeringBoundary("phase1 ended before safe steering boundary")

        server2 = p2.Recorder(
            phase2_out, ref, api_key, token, native, lambda: None,
            args.phase2_max_requests,
        )
        thread2 = serve(server2)
        cfg["models"]["providers"]["vllm"]["baseUrl"] = f"http://127.0.0.1:{server2.server_port}/v1"
        cfg["models"]["providers"]["vllm"]["apiKey"] = token
        write_config(config_path, cfg)

        print("[poc04] phase2: send steering message in same session", flush=True)
        phase2_timing = run_turn(
            cli, env, runtime, phase2_out, session_key, STEERING_TASK, args.timeout, server2
        )
        stop_server(server2, thread2)
        server2 = thread2 = None
        result["phase2"] = phase2_timing

        phase2_trace = collect_trace(phase2_out)
        continuity = history_check(phase1_trace, first_request(phase2_out))
        result["continuity"] = continuity

        final_answer = None
        if phase2_timing.get("returncode") == 0:
            cli_text = (phase2_out / "agent.stdout.txt").read_text(encoding="utf-8")
            try:
                final_answer, _payloads = p2.extract_answer(cli_text)
            except ValueError as exc:
                result["phase2_cli_error"] = str(exc)

        graded = grade(
            phase1_trace, phase2_trace, continuity, boundary,
            phase2_timing.get("returncode"), final_answer,
        )
        result["grade"] = graded
        result["final_answer"] = final_answer
        result["status"] = "PASS_POC04A_STEERING" if graded["passed"] else "POC04A_FAILED"

        if current_hashes(workspace) != staged:
            result["status"] = "POC04A_FAILED"
            result["grade"]["passed"] = False
            result["grade"]["errors"].append("workspace_modified")

        event_trace = [
            {"event": "USER_ORIGINAL", "marker": ORIGINAL_MARKER},
            {"event": "SAFE_BOUNDARY", **boundary},
            {"event": "USER_STEER", "marker": STEER_MARKER},
            {"event": "CONTINUITY", **continuity},
            {"event": "GRADE", "passed": graded["passed"], "errors": graded["errors"]},
        ]
        (out / "event-trace.json").write_text(
            json.dumps(event_trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    except SteeringBoundary as exc:
        if result["status"] == "SETUP_FAILED":
            result["status"] = "POC04A_FAILED"
        result["error"] = str(exc)
    except Exception as exc:
        result["status"] = "SETUP_FAILED" if result["status"] == "SETUP_FAILED" else "POC04A_FAILED"
        result["error"] = type(exc).__name__ + ": " + str(exc)[:500]
    finally:
        if server1 is not None and thread1 is not None:
            try:
                stop_server(server1, thread1)
            except Exception:
                pass
        if server2 is not None and thread2 is not None:
            try:
                stop_server(server2, thread2)
            except Exception:
                pass
        if prefix:
            cleanup_containers(docker, env if "env" in locals() else os.environ.copy(),
                               prefix, out, result["cleanup_warnings"])
        (out / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("poc04 audit:", out)
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            lock.close()

    return 0 if result.get("status") == "PASS_POC04A_STEERING" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError) as exc:
        print("POC04_SETUP_ERROR:", str(exc), file=sys.stderr)
        sys.exit(2)
