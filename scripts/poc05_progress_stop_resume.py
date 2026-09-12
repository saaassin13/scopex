#!/usr/bin/env python3
"""POC05: runtime progress events, deterministic stop, and same-session resume.

This POC validates product control semantics rather than diagnosis quality:

1. A real OpenClaw investigation starts and produces tool activity.
2. ScopeX derives user-visible progress from runtime/model/tool events; it does
   not expose model reasoning or ask the model to narrate progress.
3. A controller-side STOP is applied at a safe tool-round boundary before the
   next model inference. No model/tool work may continue after the stop point.
4. A later user RESUME message reuses the same OpenClaw session, preserves prior
   tool results, and may steer the next investigation direction.

POC02 remains the sandbox/wire security prerequisite. POC03 proves autonomous
investigation/finalization. POC04 proves conversational steering/correction.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import threading
import uuid

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import poc04_steering as s

ORIGINAL_MARKER = "SCOPEX_POC05_ORIGINAL_V1"
RESUME_MARKER = "SCOPEX_POC05_RESUME_V1"

ORIGINAL_TASK = f"""{ORIGINAL_MARKER}
分析 2026-09-12 10:15 左右任务执行失败的现场证据。

先从 /agent/app.log 开始建立失败时间线和重试事实。在 app.log 的失败链还没看清之前，
不要读取 robot.log 或 system.log。之后可继续自主调查其他日志并综合结论。
不要预设深层根因，也不要只停在第一条 ERROR。
"""

RESUME_TASK = f"""{RESUME_MARKER}
继续刚才被停止的调查。保留此前已经取得的 app.log 证据，不要从头重新读取 app.log。

恢复后改变下一步优先级：先检查 /agent/system.log，再检查 /agent/robot.log；根据新证据
继续原任务，最后给出简短结论并区分事实、推断和未知。不要把 status=137 自动等同于 OOM，
除非现有日志有直接证据。
"""


class UserStopBoundary(RuntimeError):
    pass


class ProgressTracker:
    """Derive compact progress events from observable runtime state only."""

    def __init__(self, path: Path, phase: str, *, seen_calls=None, seen_results=None):
        self.path = path
        self.phase = phase
        self.seen_calls = set(seen_calls or [])
        self.seen_results = set(seen_results or [])
        self.seq = 0
        self.events: list[dict] = []

    def emit(self, event: str, **fields):
        self.seq += 1
        row = {
            "seq": self.seq,
            "time": datetime.now(timezone.utc).isoformat(),
            "phase": self.phase,
            "event": event,
            **fields,
        }
        self.events.append(row)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        print("[progress] " + json.dumps(row, ensure_ascii=False), flush=True)
        return row

    @staticmethod
    def targets(call: dict) -> list[str]:
        args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        text = json.dumps(args, ensure_ascii=False).lower()
        return [name for name in s.FILES if name.lower() in text]

    def observe_request(self, request_index: int, messages: list[dict]):
        self.emit("MODEL_REQUEST", request_index=request_index)
        calls, results = s.calls_and_results(messages)
        for cid, call in calls.items():
            if cid not in self.seen_calls:
                self.seen_calls.add(cid)
                self.emit(
                    "TOOL_CALL",
                    tool_call_id=cid,
                    tool=call.get("name"),
                    targets=self.targets(call),
                )
        for cid, result in results.items():
            if cid not in self.seen_results:
                self.seen_results.add(cid)
                call = calls.get(cid, {})
                self.emit(
                    "TOOL_RESULT",
                    tool_call_id=cid,
                    tool=call.get("name"),
                    targets=self.targets(call),
                    result_chars=len(result),
                )


def request_payload(out: Path, index: int) -> dict:
    return json.loads((out / f"wire-{index:02d}-request.json").read_text(encoding="utf-8"))


def request_meta(out: Path, index: int) -> dict:
    path = out / f"wire-{index:02d}-meta.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def continuity_check(phase1_trace: dict, phase2_first: dict) -> dict:
    messages = phase2_first.get("messages", [])
    _calls, results = s.calls_and_results(messages)
    old_completed = set(phase1_trace.get("completed_ids", []))
    preserved = sorted(old_completed.intersection(results))
    user_texts = [
        s.text_content(m.get("content"))
        for m in messages
        if isinstance(m, dict) and m.get("role") == "user"
    ]
    return {
        "original_user_present": any(ORIGINAL_MARKER in text for text in user_texts),
        "resume_user_present": any(RESUME_MARKER in text for text in user_texts),
        "prior_tool_results_preserved": bool(preserved),
        "preserved_tool_result_ids": preserved,
    }


def first_new_file_target(phase1_trace: dict, phase2_trace: dict) -> str | None:
    old_ids = set(phase1_trace.get("call_ids", []))
    for row in phase2_trace.get("calls", []):
        if row.get("id") in old_ids:
            continue
        for name in s.FILES:
            if s.call_touches(row, name):
                return name
    return None


def grade(phase1_trace: dict, phase2_trace: dict, continuity: dict, stop: dict,
          phase1_progress: list[dict], phase2_progress: list[dict],
          phase2_returncode: int | None, final_answer: str | None,
          stop_meta: dict) -> dict:
    old_ids = set(phase1_trace.get("call_ids", []))
    p1_completed = [row for row in phase1_trace.get("calls", []) if row.get("completed")]
    new_calls = [row for row in phase2_trace.get("calls", []) if row.get("id") not in old_ids]
    new_completed = [row for row in new_calls if row.get("completed")]

    pre_stop_progress = [
        row for row in phase1_progress
        if row.get("event") in {"MODEL_REQUEST", "TOOL_CALL", "TOOL_RESULT"}
    ]
    tool_results_before_stop = [row for row in pre_stop_progress if row.get("event") == "TOOL_RESULT"]
    resumed_events = [row.get("event") for row in phase2_progress]

    checks = {
        "progress_visible_before_stop": len(pre_stop_progress) >= 3 and len(tool_results_before_stop) >= 2,
        "progress_contains_app_activity": any(
            "app.log" in row.get("targets", []) for row in pre_stop_progress
        ),
        "stop_reached_at_safe_boundary": stop.get("reached") is True
            and stop.get("completed_tool_results", 0) >= 2,
        "stop_request_not_forwarded": stop_meta.get("forwarded") is False,
        "no_phase1_request_after_stop": phase1_trace.get("request_count") == stop.get("request_index"),
        "phase1_app_evidence": any(s.call_touches(row, "app.log") for row in p1_completed),
        "phase1_not_already_robot": not any(s.call_touches(row, "robot.log") for row in p1_completed),
        "phase1_not_already_system": not any(s.call_touches(row, "system.log") for row in p1_completed),
        "same_session_original_context": continuity.get("original_user_present") is True,
        "resume_message_in_same_context": continuity.get("resume_user_present") is True,
        "prior_tool_results_preserved": continuity.get("prior_tool_results_preserved") is True,
        "resume_progress_event": "USER_RESUME" in resumed_events,
        "first_resumed_file_target_is_system": first_new_file_target(phase1_trace, phase2_trace) == "system.log",
        "post_resume_system_tool": any(s.call_touches(row, "system.log") for row in new_completed),
        "post_resume_robot_tool": any(s.call_touches(row, "robot.log") for row in new_completed),
        "post_resume_no_new_app_tool": not any(s.call_touches(row, "app.log") for row in new_calls),
        "system_evidence_returned": any(
            "inference-worker exited status=137" in row.get("result", "")
            for row in new_completed
        ),
        "robot_evidence_returned": any(
            "joint_fault_code=0" in row.get("result", "")
            or "controller heartbeat=ok" in row.get("result", "")
            for row in new_completed
        ),
        "resume_completed": phase2_returncode == 0,
        "final_visible_answer": isinstance(final_answer, str) and bool(final_answer.strip()),
        "task_completed_progress_event": "TASK_COMPLETED" in resumed_events,
    }
    errors = [key for key, ok in checks.items() if not ok]
    return {
        "passed": not errors,
        "checks": checks,
        "errors": errors,
        "first_new_file_target": first_new_file_target(phase1_trace, phase2_trace),
        "new_phase2_tool_calls": [
            {k: row.get(k) for k in ("id", "name", "arguments", "completed")}
            for row in new_calls
        ],
    }


def read_progress(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


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
    if not 60 <= args.timeout <= 600:
        raise ValueError("timeout must be between 60 and 600 seconds")
    if not 2 <= args.phase2_max_requests <= 10:
        raise ValueError("phase2-max-requests must be between 2 and 10")

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

    lock = (pf.parent / "poc05-progress-stop-resume.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise ValueError("another POC05 run is active")

    tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    out = pf.parent / ("poc05-" + tag)
    out.mkdir(mode=0o700)
    phase1_out, phase2_out = out / "phase1", out / "phase2"
    phase1_out.mkdir(mode=0o700)
    phase2_out.mkdir(mode=0o700)

    runtime = Path.home() / "scopex-poc05-work" / tag
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
                metadata_env, out, out, "poc05-docker-context"
            ))
        if not isinstance(host, str) or not host.startswith("unix://"):
            raise ValueError("local Docker socket required")

        config_path = out / "openclaw.json"
        env = native.clean_env(runtime, config_path, host)
        image_id = old_cfg["agents"]["defaults"]["sandbox"]["docker"]["image"]
        native.run_command(
            [docker, "image", "inspect", image_id, "--format", "{{.Id}}"],
            env, out, out, "poc05-image-check"
        )
        info = p2.model_info(ref, api_key)
        result["served_model"] = info

        agent_id = "sx5" + uuid.uuid4().hex[:9]
        session_key = f"agent:{agent_id}:poc05-{uuid.uuid4().hex[:12]}"
        token = secrets.token_urlsafe(24)
        prefix = "sxpoc05-" + agent_id + "-"
        result["session_key"] = session_key
        result["sandbox_prefix"] = prefix

        progress1_path = phase1_out / "progress.jsonl"
        tracker1 = ProgressTracker(progress1_path, "phase1")
        stop = {
            "requested": False,
            "reached": False,
            "request_index": None,
            "completed_tool_results": 0,
            "app_completed_tools": 0,
        }
        holder1 = {"server": None}

        def phase1_gate():
            server = holder1["server"]
            idx = len(server.records)
            payload = request_payload(phase1_out, idx)
            messages = payload.get("messages", [])
            tracker1.observe_request(idx, messages)
            done = s.completed_calls(messages)
            app_done = [row for row in done if s.call_touches(row, "app.log")]
            if len(done) >= 2 and app_done:
                stop.update({
                    "requested": True,
                    "reached": True,
                    "request_index": idx,
                    "completed_tool_results": len(done),
                    "app_completed_tools": len(app_done),
                })
                tracker1.emit(
                    "USER_STOP",
                    reason="user_requested_stop",
                    completed_tool_results=len(done),
                )
                tracker1.emit(
                    "SAFE_STOP",
                    before_model_request=idx,
                    running_tool_cancelled=False,
                )
                raise UserStopBoundary("POC05 user stop reached safe tool-round boundary")

        server1 = p2.Recorder(phase1_out, ref, api_key, token, native, phase1_gate, 8)
        holder1["server"] = server1
        thread1 = s.serve(server1)

        cfg = native.build_config(
            ref, runtime, out, f"http://127.0.0.1:{server1.server_port}/v1", token,
            image_id, agent_id, os.getuid(), os.getgid()
        )
        cfg["agents"]["defaults"]["sandbox"]["docker"]["containerPrefix"] = prefix
        s.write_config(config_path, cfg)

        tracker1.emit("TASK_STARTED", marker=ORIGINAL_MARKER)
        print("[poc05] phase1: start task and expose runtime progress", flush=True)
        phase1_timing = s.run_turn(
            cli, env, runtime, phase1_out, session_key, ORIGINAL_TASK, args.timeout, server1
        )
        s.stop_server(server1, thread1)
        server1 = thread1 = None
        phase1_trace = s.collect_trace(phase1_out)
        result["phase1"] = phase1_timing
        result["stop"] = stop

        if not stop["reached"]:
            result["status"] = "STOP_BOUNDARY_NOT_REACHED"
            raise RuntimeError("phase1 completed before STOP boundary")

        stop_meta = request_meta(phase1_out, stop["request_index"])
        result["stop_request_meta"] = stop_meta

        progress2_path = phase2_out / "progress.jsonl"
        tracker2 = ProgressTracker(
            progress2_path,
            "phase2",
            seen_calls=phase1_trace.get("call_ids", []),
            seen_results=phase1_trace.get("completed_ids", []),
        )
        holder2 = {"server": None}

        def phase2_gate():
            server = holder2["server"]
            idx = len(server.records)
            payload = request_payload(phase2_out, idx)
            tracker2.observe_request(idx, payload.get("messages", []))

        server2 = p2.Recorder(
            phase2_out, ref, api_key, token, native, phase2_gate,
            args.phase2_max_requests,
        )
        holder2["server"] = server2
        thread2 = s.serve(server2)
        cfg["models"]["providers"]["vllm"]["baseUrl"] = f"http://127.0.0.1:{server2.server_port}/v1"
        cfg["models"]["providers"]["vllm"]["apiKey"] = token
        s.write_config(config_path, cfg)

        tracker2.emit("USER_RESUME", marker=RESUME_MARKER, steering="system.log_then_robot.log")
        print("[poc05] phase2: resume same session with new priority", flush=True)
        phase2_timing = s.run_turn(
            cli, env, runtime, phase2_out, session_key, RESUME_TASK, args.timeout, server2
        )
        s.stop_server(server2, thread2)
        server2 = thread2 = None
        result["phase2"] = phase2_timing

        phase2_trace = s.collect_trace(phase2_out)
        continuity = s.history_check(phase1_trace, s.first_request(phase2_out))
        # POC04 marker names differ; validate POC05 messages explicitly.
        phase2_first = s.first_request(phase2_out)
        messages = phase2_first.get("messages", [])
        user_texts = [
            s.text_content(m.get("content")) for m in messages
            if isinstance(m, dict) and m.get("role") == "user"
        ]
        _calls, phase2_first_results = s.calls_and_results(messages)
        old_completed = set(phase1_trace.get("completed_ids", []))
        continuity = {
            "original_user_present": any(ORIGINAL_MARKER in text for text in user_texts),
            "resume_user_present": any(RESUME_MARKER in text for text in user_texts),
            "prior_tool_results_preserved": bool(old_completed.intersection(phase2_first_results)),
            "preserved_tool_result_ids": sorted(old_completed.intersection(phase2_first_results)),
        }
        result["continuity"] = continuity

        final_answer = None
        if phase2_timing.get("returncode") == 0:
            try:
                import poc02_run as p2_again
                final_answer, _ = p2_again.extract_answer(
                    (phase2_out / "agent.stdout.txt").read_text(encoding="utf-8")
                )
            except ValueError as exc:
                result["phase2_cli_error"] = str(exc)

        tracker2.emit(
            "TASK_COMPLETED" if phase2_timing.get("returncode") == 0 else "TASK_ENDED_WITH_ERROR",
            returncode=phase2_timing.get("returncode"),
        )

        phase1_progress = read_progress(progress1_path)
        phase2_progress = read_progress(progress2_path)
        graded = grade(
            phase1_trace, phase2_trace, continuity, stop,
            phase1_progress, phase2_progress,
            phase2_timing.get("returncode"), final_answer, stop_meta,
        )
        result["grade"] = graded
        result["final_answer"] = final_answer
        result["progress"] = {
            "phase1_event_count": len(phase1_progress),
            "phase2_event_count": len(phase2_progress),
            "phase1_file": str(progress1_path),
            "phase2_file": str(progress2_path),
        }
        result["status"] = "PASS_POC05_PROGRESS_STOP_RESUME" if graded["passed"] else "POC05_FAILED"

        if s.current_hashes(workspace) != staged:
            result["status"] = "POC05_FAILED"
            result["grade"]["passed"] = False
            result["grade"]["errors"].append("workspace_modified")

        event_trace = phase1_progress + phase2_progress
        (out / "event-trace.json").write_text(
            json.dumps(event_trace, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    except Exception as exc:
        if result["status"] == "SETUP_FAILED":
            result["status"] = "POC05_FAILED"
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
        print("poc05 audit:", out)
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            lock.close()

    return 0 if result.get("status") == "PASS_POC05_PROGRESS_STOP_RESUME" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError) as exc:
        print("POC05_SETUP_ERROR:", str(exc), file=sys.stderr)
        sys.exit(2)
