#!/usr/bin/env python3
"""Force one long OpenClaw session through context pressure and verify compaction.

This is a Spark integration probe for ScopeX Step 6A. It intentionally does not
use Memory Search or ScopeX-owned context compression. The same OpenClaw session
receives several large turns, then a final recall probe checks whether critical
state from the first turn survived compaction.

The probe writes every turn's OpenClaw audit plus a machine-readable result.json
under .local/step6a-compaction/ by default.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scopex.agent.docker_host import resolve_local_docker_host
from scopex.agent.environment import build_openclaw_env
from scopex.agent.runtime import OpenClawTaskRuntime, OpenClawTaskSpec
from scopex.events.progress import InMemoryEventSink
from scopex.runtime.stop import SafeStopGate


COMPACTION_MARKERS = (
    "auto-compaction start",
    "auto-compaction complete",
    "compaction complete",
    "compacted",
)


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def make_payload(turn_index: int, target_chars: int) -> str:
    """Create deterministic context pressure without adding useful task state."""
    rows: list[str] = []
    index = 0
    current = 0
    while current < target_chars:
        digest = hashlib.sha256(f"{turn_index}:{index}".encode()).hexdigest()[:16]
        row = (
            f"payload turn={turn_index:02d} row={index:05d} token={digest} "
            "此行只是上下文压力测试填充，不是新的事实、证据、目标或约束。"
        )
        rows.append(row)
        current += len(row) + 1
        index += 1
    return "\n".join(rows)


def turn_message(turn_index: int, payload_chars: int, sentinel: str) -> str:
    payload = make_payload(turn_index, payload_chars)
    if turn_index == 1:
        prefix = f"""这是 ScopeX Step 6A 长上下文测试的第一轮。
以下三项是整个会话的关键状态，后续无论上下文如何压缩都必须保持：
TASK_GOAL=验证长任务压缩后仍能继续执行
SAFETY_RULE=不要把未证实假设升级成事实
SENTINEL={sentinel}

下面 PAYLOAD 仅用于制造上下文压力，不包含任何新的任务事实。不要分析或复述 PAYLOAD。
不要调用工具。只回复：ACK-01
"""
    else:
        prefix = f"""继续同一个长任务测试，这是第 {turn_index} 轮。
首轮 TASK_GOAL、SAFETY_RULE 和 SENTINEL 仍然有效，不要修改它们。
下面 PAYLOAD 仍然只是上下文压力，不包含新事实。不要分析或复述 PAYLOAD。
不要调用工具。只回复：ACK-{turn_index:02d}
"""
    return prefix + "\n" + payload


def final_probe_message() -> str:
    return """这是最终上下文保持检查。
不要调用工具，不要解释，不要猜测。
请只输出第一轮明确写入的 SENTINEL 值本身。"""


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
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, UnicodeError, ValueError):
            continue
        messages = payload.get("messages") if isinstance(payload, dict) else None
        message_chars = None
        if isinstance(messages, list):
            message_chars = len(json.dumps(messages, ensure_ascii=False))
        rows.append(
            {
                "file": path.name,
                "request_bytes": len(raw.encode("utf-8")),
                "message_chars": message_chars,
                "message_count": len(messages) if isinstance(messages, list) else None,
            }
        )
    return rows


def compaction_log_lines(audit_dir: Path) -> list[str]:
    path = audit_dir / "openclaw.log"
    if not path.is_file():
        return []
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        low = raw.lower()
        if "compact" in low:
            lines.append(raw[-1200:])
    return lines[-50:]


def flatten_interesting(value: Any, prefix: str = "") -> dict[str, Any]:
    """Keep session fields useful for context/usage/compaction diagnosis."""
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            low = str(key).lower()
            if any(word in low for word in ("token", "compact", "context", "usage")):
                if isinstance(child, (str, int, float, bool)) or child is None:
                    out[path] = child
            out.update(flatten_interesting(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value[:50]):
            out.update(flatten_interesting(child, f"{prefix}[{index}]"))
    return out


def inspect_session(
    *,
    cli: Path,
    runtime_root: Path,
    config_path: Path,
    docker_host: str,
    agent_id: str,
    session_key: str,
) -> dict[str, Any]:
    env = build_openclaw_env(
        runtime_root=runtime_root,
        config_path=config_path,
        docker_host=docker_host,
        base_env=os.environ,
    )
    proc = subprocess.run(
        [str(cli), "sessions", "--agent", agent_id, "--json"],
        cwd=runtime_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    result: dict[str, Any] = {
        "returncode": proc.returncode,
        "stderr": proc.stderr[-2000:],
    }
    if proc.returncode != 0:
        return result
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        result["stdout_tail"] = proc.stdout[-4000:]
        return result
    sessions = payload.get("sessions") if isinstance(payload, dict) else None
    row = None
    if isinstance(sessions, list):
        for candidate in sessions:
            if isinstance(candidate, dict) and candidate.get("key") == session_key:
                row = candidate
                break
    result["found"] = row is not None
    if row is not None:
        result["row"] = row
        result["interesting"] = flatten_interesting(row)
    return result


def numeric_compaction_count(value: Any) -> int:
    best = 0
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("_", "")
            if normalized in {"compactions", "compactioncount"} and isinstance(child, (int, float)):
                best = max(best, int(child))
            best = max(best, numeric_compaction_count(child))
    elif isinstance(value, list):
        for child in value:
            best = max(best, numeric_compaction_count(child))
    return best


def has_token_signal(turns: list[dict[str, Any]]) -> bool:
    for turn in turns:
        for usage in turn.get("wire_usage", []):
            if any(
                isinstance(value, (int, float)) and value > 0
                for key, value in usage.items()
                if "token" in str(key).lower()
            ):
                return True
        interesting = turn.get("session", {}).get("interesting", {})
        if any(
            isinstance(value, (int, float)) and value > 0
            for key, value in interesting.items()
            if "token" in key.lower() or "context" in key.lower()
        ):
            return True
    return False


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
        default=ROOT / ".local" / "step6a-compaction",
    )
    parser.add_argument("--turns", type=int, default=9)
    parser.add_argument("--payload-chars", type=int, default=9000)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--max-requests-per-turn", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--api-key-env", default="SCOPEX_API_KEY")
    parser.add_argument(
        "--disable-compaction",
        action="store_true",
        help="baseline mode only; PASS then does not require a compaction event",
    )
    args = parser.parse_args(argv)

    if not sys.platform.startswith("linux") or os.geteuid() == 0:
        raise ValueError("run on Spark Linux as the ordinary user, not sudo")
    if not 3 <= args.turns <= 30:
        raise ValueError("--turns must be between 3 and 30")
    if not 1000 <= args.payload_chars <= 50_000:
        raise ValueError("--payload-chars must be between 1000 and 50000")
    if not 60 <= args.timeout <= 900:
        raise ValueError("--timeout must be between 60 and 900 seconds")
    if not 2 <= args.max_requests_per_turn <= 20:
        raise ValueError("--max-requests-per-turn must be between 2 and 20")

    cli = args.openclaw_bin.expanduser().resolve()
    if not cli.is_file() or not os.access(cli, os.X_OK):
        raise ValueError("OpenClaw CLI is not executable")

    docker_host = resolve_local_docker_host()
    api_key = os.environ.get(args.api_key_env, "")
    if "\n" in api_key or "\r" in api_key:
        raise ValueError("invalid API key environment value")

    tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    root = args.data_root.expanduser().resolve() / tag
    workspace = root / "workspace"
    runtime_root = root / "runtime"
    audit_root = root / "agent-turns"
    for path in (root, workspace, runtime_root, audit_root):
        path.mkdir(parents=True, mode=0o700, exist_ok=True)

    task_id = "step6a-" + uuid.uuid4().hex[:12]
    agent_id = "sxctx" + uuid.uuid4().hex[:8]
    session_key = f"agent:{agent_id}:{task_id}"
    sentinel = "SX6A-" + uuid.uuid4().hex

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
        max_requests=args.max_requests_per_turn,
        max_tokens=args.max_tokens,
        skills=(),
        tools=("read", "exec", "process"),
        compaction_enabled=not args.disable_compaction,
    )
    events = InMemoryEventSink()
    runtime = OpenClawTaskRuntime(
        task_id=task_id,
        session_key=session_key,
        spec=spec,
        events=events,
        stop_gate=SafeStopGate(),
    )

    result: dict[str, Any] = {
        "status": "STEP6A_COMPACTION_PROBE_FAILED",
        "root": str(root),
        "task_id": task_id,
        "agent_id": agent_id,
        "session_key": session_key,
        "model": args.model,
        "compaction_enabled": spec.compaction_enabled,
        "turns_requested": args.turns,
        "payload_chars": args.payload_chars,
        "sentinel": sentinel,
        "turns": [],
    }

    try:
        turns: list[dict[str, Any]] = []
        all_completed = True
        for turn_index in range(1, args.turns + 1):
            turn = runtime.run_turn(
                turn_message(turn_index, args.payload_chars, sentinel),
                turn_name=f"turn-{turn_index:03d}",
            )
            completed = bool(
                turn.process.returncode == 0
                and turn.process.stop_reason is None
                and turn.cli_outcome is not None
                and turn.cli_outcome.completed
            )
            all_completed = all_completed and completed
            session = inspect_session(
                cli=cli,
                runtime_root=runtime_root,
                config_path=runtime.config_path,
                docker_host=docker_host,
                agent_id=agent_id,
                session_key=session_key,
            )
            row = {
                "turn": turn_index,
                "completed": completed,
                "answer": turn.cli_outcome.answer if turn.cli_outcome else None,
                "returncode": turn.process.returncode,
                "stop_reason": turn.process.stop_reason,
                "wall_s": turn.process.wall_s,
                "proxy_requests": len(turn.proxy_records),
                "forwarded_requests": sum(
                    1 for record in turn.proxy_records if record.get("forwarded") is True
                ),
                "requests": request_metrics(turn.audit_dir),
                "wire_usage": parse_wire_usage(turn.audit_dir),
                "compaction_log_lines": compaction_log_lines(turn.audit_dir),
                "session": session,
            }
            turns.append(row)
            save_json(root / f"turn-{turn_index:03d}-summary.json", row)
            if not completed:
                break

        final_turn_index = len(turns) + 1
        final_turn = runtime.run_turn(
            final_probe_message(),
            turn_name=f"turn-{final_turn_index:03d}-recall",
        )
        final_answer = final_turn.cli_outcome.answer if final_turn.cli_outcome else None
        final_completed = bool(
            final_turn.process.returncode == 0
            and final_turn.process.stop_reason is None
            and final_turn.cli_outcome is not None
            and final_turn.cli_outcome.completed
        )
        final_session = inspect_session(
            cli=cli,
            runtime_root=runtime_root,
            config_path=runtime.config_path,
            docker_host=docker_host,
            agent_id=agent_id,
            session_key=session_key,
        )
        final_row = {
            "turn": final_turn_index,
            "kind": "recall_probe",
            "completed": final_completed,
            "answer": final_answer,
            "returncode": final_turn.process.returncode,
            "stop_reason": final_turn.process.stop_reason,
            "wall_s": final_turn.process.wall_s,
            "proxy_requests": len(final_turn.proxy_records),
            "forwarded_requests": sum(
                1 for record in final_turn.proxy_records if record.get("forwarded") is True
            ),
            "requests": request_metrics(final_turn.audit_dir),
            "wire_usage": parse_wire_usage(final_turn.audit_dir),
            "compaction_log_lines": compaction_log_lines(final_turn.audit_dir),
            "session": final_session,
        }
        turns.append(final_row)
        save_json(root / f"turn-{final_turn_index:03d}-recall-summary.json", final_row)

        all_completed = all_completed and final_completed
        log_markers = [
            line
            for turn in turns
            for line in turn.get("compaction_log_lines", [])
            if any(marker in line.lower() for marker in COMPACTION_MARKERS)
        ]
        compaction_count = max(
            (numeric_compaction_count(turn.get("session")) for turn in turns),
            default=0,
        )
        compaction_observed = bool(log_markers or compaction_count > 0)
        sentinel_preserved = isinstance(final_answer, str) and sentinel in final_answer
        token_signal = has_token_signal(turns)

        result["turns"] = turns
        result["all_turns_completed"] = all_completed
        result["compaction_observed"] = compaction_observed
        result["compaction_count"] = compaction_count
        result["compaction_log_markers"] = log_markers[-50:]
        result["token_signal_observed"] = token_signal
        result["recall_answer"] = final_answer
        result["sentinel_preserved"] = sentinel_preserved

        expected_compaction = not args.disable_compaction
        passed = all_completed and sentinel_preserved and (
            compaction_observed if expected_compaction else True
        )
        if passed:
            result["status"] = (
                "PASS_STEP6A_COMPACTION"
                if expected_compaction else "PASS_STEP6A_BASELINE"
            )
        elif expected_compaction and not compaction_observed:
            result["failure_reason"] = "compaction_not_observed"
        elif not sentinel_preserved:
            result["failure_reason"] = "critical_state_not_preserved"
        elif not all_completed:
            result["failure_reason"] = "turn_failed_before_recall"

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
        print("Step 6A audit:", root, file=sys.stderr)

    return 0 if str(result.get("status", "")).startswith("PASS_") else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print("STEP6A_SETUP_ERROR:", str(exc), file=sys.stderr)
        raise SystemExit(2)
