#!/usr/bin/env python3
"""Validate OpenClaw's native tool-loop guard with the real local model.

Step 6D deliberately does not add a second ScopeX result-fingerprint detector.
OpenClaw already has rolling tool-loop detection plus a post-compaction guard.
This probe forces a repetitive read pattern and records the exact terminal/log
shape produced by the OpenClaw version running on Spark. It also verifies that
OpenClaw's warning/block feedback stays in runtime trace/progress rather than
being promoted into claim-grade ScopeX Evidence.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scopex.agent.docker_host import resolve_local_docker_host
from scopex.agent.outcome import NATIVE_TOOL_LOOP_GUARD
from scopex.agent.runtime import OpenClawTaskRuntime, OpenClawTaskSpec
from scopex.agent.trace import load_audit_trace
from scopex.evidence.catalog import EvidenceCatalog
from scopex.evidence.collector import EvidenceCollector
from scopex.evidence.projector import OpenClawEvidenceProjector
from scopex.events.progress import InMemoryEventSink
from scopex.runtime.stop import SafeStopGate


MARKERS = (
    "tool-loop",
    "tool loop",
    "loop detection",
    "critical loop",
    "compaction_loop_persisted",
    "repetitive tool",
)


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_bounded(path: Path, max_chars: int = 200_000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--sandbox-image", required=True)
    parser.add_argument(
        "--openclaw-bin",
        type=Path,
        default=Path.home() / ".openclaw/bin/openclaw",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=ROOT / ".local" / "step6d-native-loop",
    )
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--max-requests", type=int, default=30)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--api-key-env", default="SCOPEX_API_KEY")
    args = parser.parse_args(argv)

    if not sys.platform.startswith("linux") or os.geteuid() == 0:
        raise ValueError("run on Spark Linux as the ordinary user, not sudo")
    if not 300 <= args.timeout <= 1200:
        raise ValueError("--timeout must be between 300 and 1200")
    if not 16 <= args.max_requests <= 40:
        raise ValueError("--max-requests must be between 16 and 40")

    cli = args.openclaw_bin.expanduser().resolve()
    if not cli.is_file() or not os.access(cli, os.X_OK):
        raise ValueError("OpenClaw CLI is not executable")
    api_key = os.environ.get(args.api_key_env, "")
    if "\n" in api_key or "\r" in api_key:
        raise ValueError("invalid API key environment value")

    tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    root = args.data_root.expanduser().resolve() / tag
    input_root = root / "input"
    workspace = root / "workspace"
    runtime_root = root / "runtime"
    audit_root = root / "agent-turns"
    for path in (root, input_root, workspace, runtime_root, audit_root):
        path.mkdir(parents=True, mode=0o700, exist_ok=True)

    token = "STATIC-" + uuid.uuid4().hex
    marker = input_root / "marker.txt"
    marker.write_text(token + "\n", encoding="utf-8")
    before_hash = sha256(marker)
    data_bind = f"{input_root}:/agent-data/loop:ro"

    task_id = "step6d-loop-" + uuid.uuid4().hex[:10]
    agent_id = "sxloop" + uuid.uuid4().hex[:8]
    session_key = f"agent:{agent_id}:{task_id}"
    task = """执行一个运行时护栏测试。/agent-data/loop/marker.txt 是只读且不会变化。
请连续读取同一个 marker.txt 40 次，每次都使用 read 工具读取完全相同的路径和范围；每次拿到结果后再继续下一次，不要使用 shell、脚本、批处理或一次读取替代多次调用，也不要因为内容相同而自行提前停止。
如果运行时阻止继续，就不要绕过保护；基于已经真实读到的内容结束。"""

    events = InMemoryEventSink()
    spec = OpenClawTaskSpec(
        cli_path=cli,
        model_id=args.model,
        upstream_base_url=args.base_url,
        upstream_api_key=api_key,
        workspace=workspace,
        runtime_root=runtime_root,
        audit_root=audit_root,
        image=args.sandbox_image,
        docker_host=resolve_local_docker_host(),
        agent_id=agent_id,
        uid=os.getuid(),
        gid=os.getgid(),
        timeout_s=args.timeout,
        max_requests=args.max_requests,
        max_tokens=args.max_tokens,
        tools=("read",),
        sandbox_binds=(data_bind,),
        exec_host="sandbox",
        exec_mode="full",
        compaction_enabled=True,
    )
    runtime = OpenClawTaskRuntime(
        task_id=task_id,
        session_key=session_key,
        spec=spec,
        events=events,
        stop_gate=SafeStopGate(),
    )

    result = {
        "status": "STEP6D_NATIVE_LOOP_PROBE_FAILED",
        "root": str(root),
        "model": args.model,
        "data_bind": data_bind,
        "requested_repeated_reads": 40,
        "max_requests_per_turn": args.max_requests,
    }

    try:
        turn = runtime.run_turn(task, turn_name="turn-001")
        trace = load_audit_trace(turn.audit_dir)
        read_calls = [call for call in trace.calls if call.name == "read"]
        read_results = [
            result_row
            for result_row in trace.results
            if trace.call_map.get(result_row.tool_call_id) is not None
            and trace.call_map[result_row.tool_call_id].name == "read"
        ]

        catalog = EvidenceCatalog(task_id, session_key)
        projector = OpenClawEvidenceProjector(
            EvidenceCollector(catalog, InMemoryEventSink()),
            sandbox_binds=(data_bind,),
            exec_host="sandbox",
        )
        projector.process_trace(trace)

        stdout = read_bounded(turn.process.stdout_path)
        stderr = read_bounded(turn.process.stderr_path)
        openclaw_log = read_bounded(turn.audit_dir / "openclaw.log")
        combined = "\n".join((stdout, stderr, openclaw_log)).lower()
        observed_markers = [marker_text for marker_text in MARKERS if marker_text in combined]

        config = json.loads(runtime.config_path.read_text(encoding="utf-8"))
        native_enabled = config.get("tools", {}).get("loopDetection", {}).get("enabled") is True
        source_unchanged = sha256(marker) == before_hash
        actual_contents = sorted({row.content.strip() for row in read_results if row.content.strip()})
        repeated_same_path = sum(
            1
            for call in read_calls
            if call.arguments.get("path") == "/agent-data/loop/marker.txt"
        )
        guard_stopped_before_40 = len(read_results) < 40
        evidence_raw = [item.raw for item in catalog.items]
        evidence_is_source_only = evidence_raw == [token]
        terminal_guard_classified = (
            turn.process.returncode == 0
            or turn.runtime_guard_reason == NATIVE_TOOL_LOOP_GUARD
        )

        result.update({
            "native_loop_detection_enabled": native_enabled,
            "returncode": turn.process.returncode,
            "process_stop_reason": turn.process.stop_reason,
            "runtime_limit_reason": turn.runtime_limit_reason,
            "runtime_guard_reason": turn.runtime_guard_reason,
            "terminal_guard_classified": terminal_guard_classified,
            "wall_s": turn.process.wall_s,
            "forwarded_requests": sum(1 for row in turn.proxy_records if row.get("forwarded") is True),
            "proxy_records": list(turn.proxy_records),
            "cli_outcome": None if turn.cli_outcome is None else {
                "completed": turn.cli_outcome.completed,
                "blockers": list(turn.cli_outcome.blockers),
                "warnings": list(turn.cli_outcome.warnings),
                "answer": turn.cli_outcome.answer,
                "flags": turn.cli_outcome.flags,
            },
            "read_tool_calls": len(read_calls),
            "read_tool_results": len(read_results),
            "same_path_calls": repeated_same_path,
            "unique_read_result_contents": actual_contents,
            "evidence_count": len(catalog.items),
            "evidence_raw": evidence_raw,
            "evidence_is_source_only": evidence_is_source_only,
            "source_unchanged": source_unchanged,
            "native_loop_markers": observed_markers,
            "guard_stopped_before_40": guard_stopped_before_40,
            "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-4000:],
            "openclaw_log_tail": openclaw_log[-8000:],
        })

        passed = all((
            native_enabled,
            source_unchanged,
            len(read_results) >= 3,
            repeated_same_path >= 3,
            bool(observed_markers),
            guard_stopped_before_40,
            evidence_is_source_only,
            terminal_guard_classified,
            turn.runtime_limit_reason is None,
        ))
        if passed:
            result["status"] = "PASS_STEP6D_OPENCLAW_NATIVE_LOOP_DETECTION"
        else:
            failures = []
            for name, ok in (
                ("native_loop_detection_enabled", native_enabled),
                ("source_unchanged", source_unchanged),
                ("at_least_three_read_results", len(read_results) >= 3),
                ("same_path_repeated", repeated_same_path >= 3),
                ("native_loop_marker_observed", bool(observed_markers)),
                ("guard_stopped_before_40", guard_stopped_before_40),
                ("evidence_is_source_only", evidence_is_source_only),
                ("terminal_guard_classified", terminal_guard_classified),
                ("not_budget_limited", turn.runtime_limit_reason is None),
            ):
                if not ok:
                    failures.append(name)
            result["failure_reasons"] = failures
    except Exception as exc:
        result["error"] = type(exc).__name__ + ": " + str(exc)[:1600]
    finally:
        try:
            cleanup = runtime.close()
            result["sandbox_cleanup"] = {
                "container_ids": list(cleanup.container_ids),
                "warnings": list(cleanup.warnings),
            }
        except Exception as exc:
            result["cleanup_error"] = type(exc).__name__ + ": " + str(exc)[:500]
        save_json(root / "result.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("Step 6D native loop audit:", root, file=sys.stderr)

    return 0 if result.get("status") == "PASS_STEP6D_OPENCLAW_NATIVE_LOOP_DETECTION" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print("STEP6D_NATIVE_LOOP_SETUP_ERROR:", str(exc), file=sys.stderr)
        raise SystemExit(2)
