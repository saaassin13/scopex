#!/usr/bin/env python3
"""Validate Step 6B task scratch and bounded large-data working-set behavior.

The probe creates a multi-megabyte CSV as read-only external input, exposes a
host-backed writable /task-scratch directory, and lets one real OpenClaw session
decide how to analyze it. Passing requires an exact scratch summary without
shipping an unbounded raw tool result into model context.
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
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scopex.agent.docker_host import resolve_local_docker_host
from scopex.agent.runtime import OpenClawTaskRuntime, OpenClawTaskSpec
from scopex.agent.trace import load_audit_trace
from scopex.events.progress import InMemoryEventSink
from scopex.runtime.stop import SafeStopGate


FAULT_SEQS = (137, 2048, 15_000, 55_555, 90_001, 119_999)


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def create_fixture(path: Path, rows: int) -> dict[str, Any]:
    fault_set = {value for value in FAULT_SEQS if value < rows}
    fault_values: list[int] = []
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write("seq,timestamp_ms,value,status,note\n")
        for seq in range(rows):
            value = (seq * 37 + 11) % 10_000
            status = "FAULT" if seq in fault_set else "OK"
            if status == "FAULT":
                fault_values.append(value)
            # A fixed-width-ish note makes the source meaningfully larger than
            # a model context while remaining cheap to generate on Spark.
            note = f"sample-{seq:06d}-telemetry-window"
            handle.write(f"{seq},{1_800_000_000_000 + seq * 20},{value},{status},{note}\n")
    expected = {
        "fault_count": len(fault_set),
        "first_fault_seq": min(fault_set) if fault_set else None,
        "last_fault_seq": max(fault_set) if fault_set else None,
        "fault_value_sum": sum(fault_values),
    }
    return {
        "rows": rows,
        "expected": expected,
        "input_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def parse_wire_usage(audit_dir: Path) -> list[dict[str, Any]]:
    usages: list[dict[str, Any]] = []
    for path in sorted(audit_dir.glob("wire-*-response.bin")):
        raw = path.read_bytes().decode("utf-8", errors="replace")
        candidates: list[Any] = []
        stripped = raw.strip()
        if stripped.startswith("{"):
            try:
                candidates.append(json.loads(stripped))
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
        for value in candidates:
            if not isinstance(value, dict):
                continue
            usage = value.get("usage")
            if isinstance(usage, dict) and usage:
                usages.append(dict(usage))
    return usages


def request_metrics(audit_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(audit_dir.glob("wire-*-request.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        messages = payload.get("messages") if isinstance(payload, dict) else None
        tools = payload.get("tools") if isinstance(payload, dict) else None
        rows.append(
            {
                "file": path.name,
                "message_count": len(messages) if isinstance(messages, list) else None,
                "message_chars": len(json.dumps(messages, ensure_ascii=False))
                if isinstance(messages, list) else None,
                "has_tools_field": "tools" in payload if isinstance(payload, dict) else False,
                "tool_count": len(tools) if isinstance(tools, list) else None,
            }
        )
    return rows


def list_scratch(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not root.is_dir():
        return rows
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rows.append({
            "path": str(path.relative_to(root)),
            "bytes": path.stat().st_size,
        })
    return rows


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
        default=ROOT / ".local" / "step6b-large-data",
    )
    parser.add_argument("--rows", type=int, default=120_000)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--max-requests", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--api-key-env", default="SCOPEX_API_KEY")
    parser.add_argument("--max-tool-result-chars", type=int, default=200_000)
    args = parser.parse_args(argv)

    if not sys.platform.startswith("linux") or os.geteuid() == 0:
        raise ValueError("run on Spark Linux as the ordinary user, not sudo")
    if not 50_000 <= args.rows <= 1_000_000:
        raise ValueError("--rows must be between 50000 and 1000000")
    if not 60 <= args.timeout <= 900:
        raise ValueError("--timeout must be between 60 and 900 seconds")
    if not 2 <= args.max_requests <= 20:
        raise ValueError("--max-requests must be between 2 and 20")

    cli = args.openclaw_bin.expanduser().resolve()
    if not cli.is_file() or not os.access(cli, os.X_OK):
        raise ValueError("OpenClaw CLI is not executable")

    docker_host = resolve_local_docker_host()
    api_key = os.environ.get(args.api_key_env, "")
    if "\n" in api_key or "\r" in api_key:
        raise ValueError("invalid API key environment value")

    tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    root = args.data_root.expanduser().resolve() / tag
    input_root = root / "input"
    scratch_root = root / "scratch"
    workspace = root / "workspace"
    runtime_root = root / "runtime"
    audit_root = root / "agent-turns"
    for path in (root, input_root, scratch_root, workspace, runtime_root, audit_root):
        path.mkdir(parents=True, mode=0o700, exist_ok=True)

    source = input_root / "telemetry.csv"
    fixture = create_fixture(source, args.rows)
    before_sha = fixture["sha256"]

    task_id = "step6b-" + uuid.uuid4().hex[:12]
    agent_id = "sxdata" + uuid.uuid4().hex[:8]
    session_key = f"agent:{agent_id}:{task_id}"
    data_bind = f"{input_root.resolve()}:/agent-data/large:ro"
    scratch_bind = f"{scratch_root.resolve()}:/task-scratch:rw"

    task_message = """分析只读文件 /agent-data/large/telemetry.csv，并给出精确结果：
- status=FAULT 的总行数 fault_count
- 最小 seq first_fault_seq
- 最大 seq last_fault_seq
- 所有 FAULT 行 value 的总和 fault_value_sum

将结果写入 /task-scratch/summary.json，JSON 必须只包含上述四个键且值为数字或 null。
输入文件可能很大。调查方法由你决定；不要修改源文件，不要把完整原始文件输出到模型上下文。
完成必要处理后只给一个简短结果。"""

    spec = OpenClawTaskSpec(
        cli_path=cli,
        model_id=args.model,
        upstream_base_url=args.base_url,
        upstream_api_key=api_key,
        workspace=workspace,
        runtime_root=runtime_root,
        audit_root=audit_root,
        image=args.sandbox_image,
        docker_host=docker_host,
        agent_id=agent_id,
        uid=os.getuid(),
        gid=os.getgid(),
        timeout_s=args.timeout,
        max_requests=args.max_requests,
        max_tokens=args.max_tokens,
        skills=(),
        tools=("read", "exec", "process"),
        sandbox_binds=(data_bind,),
        task_scratch_bind=scratch_bind,
        exec_host="sandbox",
        exec_mode="full",
        compaction_enabled=True,
    )
    runtime = OpenClawTaskRuntime(
        task_id=task_id,
        session_key=session_key,
        spec=spec,
        events=InMemoryEventSink(),
        stop_gate=SafeStopGate(),
    )

    result: dict[str, Any] = {
        "status": "STEP6B_LARGE_DATA_PROBE_FAILED",
        "root": str(root),
        "model": args.model,
        "fixture": fixture,
        "data_bind": data_bind,
        "scratch_bind": scratch_bind,
    }

    try:
        turn = runtime.run_turn(task_message, turn_name="turn-001")
        completed = bool(
            turn.process.returncode == 0
            and turn.process.stop_reason is None
            and turn.cli_outcome is not None
            and turn.cli_outcome.completed
        )
        trace = load_audit_trace(turn.audit_dir)
        tool_result_sizes = [len(row.content) for row in trace.results]
        max_tool_result_chars = max(tool_result_sizes, default=0)
        total_tool_result_chars = sum(tool_result_sizes)
        usage = parse_wire_usage(turn.audit_dir)
        prompt_tokens = [
            int(row["prompt_tokens"])
            for row in usage
            if isinstance(row.get("prompt_tokens"), (int, float))
        ]
        largest_prompt_tokens = max(prompt_tokens, default=0)
        summary_path = scratch_root / "summary.json"
        actual_summary = None
        summary_error = None
        if summary_path.is_file():
            try:
                actual_summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as exc:
                summary_error = type(exc).__name__ + ": " + str(exc)

        source_unchanged = sha256_file(source) == before_sha
        expected = fixture["expected"]
        summary_correct = actual_summary == expected
        tool_results_bounded = max_tool_result_chars <= args.max_tool_result_chars
        scratch_files = list_scratch(scratch_root)

        result.update({
            "completed": completed,
            "answer": turn.cli_outcome.answer if turn.cli_outcome else None,
            "wall_s": turn.process.wall_s,
            "forwarded_requests": sum(
                1 for row in turn.proxy_records if row.get("forwarded") is True
            ),
            "requests": request_metrics(turn.audit_dir),
            "wire_usage": usage,
            "largest_prompt_tokens": largest_prompt_tokens,
            "tool_calls": len(trace.calls),
            "tool_results": len(trace.results),
            "max_tool_result_chars": max_tool_result_chars,
            "total_tool_result_chars": total_tool_result_chars,
            "tool_results_bounded": tool_results_bounded,
            "summary_path": str(summary_path),
            "summary_error": summary_error,
            "actual_summary": actual_summary,
            "summary_correct": summary_correct,
            "source_unchanged": source_unchanged,
            "scratch_files": scratch_files,
            "working_set_ratio_max_result_to_input": (
                round(max_tool_result_chars / fixture["input_bytes"], 6)
                if fixture["input_bytes"] else None
            ),
        })

        passed = (
            completed
            and summary_correct
            and source_unchanged
            and tool_results_bounded
            and summary_path.is_file()
        )
        if passed:
            result["status"] = "PASS_STEP6B_LARGE_DATA_WORKING_SET"
        elif not completed:
            result["failure_reason"] = "agent_turn_incomplete"
        elif not summary_path.is_file():
            result["failure_reason"] = "scratch_summary_missing"
        elif not summary_correct:
            result["failure_reason"] = "scratch_summary_incorrect"
        elif not source_unchanged:
            result["failure_reason"] = "read_only_source_changed"
        elif not tool_results_bounded:
            result["failure_reason"] = "unbounded_tool_result"

    except Exception as exc:
        result["error"] = type(exc).__name__ + ": " + str(exc)[:1200]
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
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        print("Step 6B large-data audit:", root, file=sys.stderr)

    return 0 if str(result.get("status", "")).startswith("PASS_") else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print("STEP6B_SETUP_ERROR:", str(exc), file=sys.stderr)
        raise SystemExit(2)
