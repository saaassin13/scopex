#!/usr/bin/env python3
"""Verify that OpenClaw compaction preserves evolving task state, not one sentinel.

Step 6A phase 2 probe. It runs one real OpenClaw session under repeated context
pressure with Memory Search disabled. The first turn establishes several kinds
of task state; a later turn supersedes one field and adds another fact. After
multiple compactions the final turn must return the exact current state as JSON.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import uuid
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
for value in (str(ROOT), str(SCRIPTS)):
    if value not in sys.path:
        sys.path.insert(0, value)

from scopex.agent.docker_host import resolve_local_docker_host
from scopex.agent.runtime import OpenClawTaskRuntime, OpenClawTaskSpec
from scopex.events.progress import InMemoryEventSink
from scopex.runtime.stop import SafeStopGate
from step6a_compaction_probe import (
    compaction_log_lines,
    inspect_session,
    make_payload,
    parse_wire_usage,
    save_json,
)


def code(prefix: str) -> str:
    return prefix + "-" + uuid.uuid4().hex[:16]


def parse_json_answer(text: str | None) -> dict[str, Any] | None:
    if not isinstance(text, str):
        return None
    raw = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", raw, re.I)
    if fenced:
        raw = fenced.group(1).strip()
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def request_metrics(audit_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(audit_dir.glob("wire-*-request.json")):
        try:
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
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
                "has_tools_field": isinstance(payload, dict) and "tools" in payload,
                "tool_count": len(tools) if isinstance(tools, list) else None,
                "max_tokens": payload.get("max_tokens") if isinstance(payload, dict) else None,
            }
        )
    return rows


def turn1_message(state: dict[str, str], payload_chars: int) -> str:
    payload = make_payload(1, payload_chars)
    return f"""这是 ScopeX Step 6A 结构化状态保持测试。
这些是任务的权威状态，后续压缩不得改变含义或角色：
TASK_GOAL={state['task_goal']}
SAFETY_RULE={state['safety_rule']}
CONFIRMED_FACT={state['confirmed_fact']}
REJECTED_HYPOTHESIS={state['rejected_hypothesis']}
KEY_ARTIFACT={state['key_artifact']}
CURRENT_NEXT_ACTION={state['current_next_action']}

注意：REJECTED_HYPOTHESIS 是已明确排除的假设，绝不能在之后变成事实。
下面 PAYLOAD 只制造上下文压力，不含新的事实、目标或约束。
不要调用工具，不要复述状态，只回复 ACK-01。

{payload}"""


def ordinary_turn_message(turn_index: int, payload_chars: int) -> str:
    payload = make_payload(turn_index, payload_chars)
    return f"""继续同一个 Step 6A 长任务，这是第 {turn_index} 轮。
已有任务状态继续有效；不要修改已确认事实、已排除假设、关键路径或当前动作。
下面 PAYLOAD 只是上下文压力，不包含新的任务信息。
不要调用工具，只回复 ACK-{turn_index:02d}。

{payload}"""


def update_turn_message(
    turn_index: int,
    payload_chars: int,
    *,
    old_action: str,
    new_action: str,
    late_fact: str,
) -> str:
    payload = make_payload(turn_index, payload_chars)
    return f"""继续同一个 Step 6A 长任务，这是第 {turn_index} 轮，并更新任务状态。
此前 CURRENT_NEXT_ACTION={old_action} 现在已经被明确替代。
SUPERSEDED_NEXT_ACTION={old_action}
CURRENT_NEXT_ACTION={new_action}
新增一个已确认事实：LATE_CONFIRMED_FACT={late_fact}

旧动作只能作为 superseded 历史保留，最终不得把它误报为当前动作。
其他首轮状态保持不变。
下面 PAYLOAD 只是上下文压力，不含新事实。
不要调用工具，只回复 ACK-{turn_index:02d}。

{payload}"""


def final_probe_message() -> str:
    return """这是最终状态保持检查。不要调用工具，不要解释，不要猜测。
只输出一个 JSON 对象，字段必须且只能是：
{
  "task_goal": "...",
  "safety_rule": "...",
  "confirmed_fact": "...",
  "rejected_hypothesis": "...",
  "key_artifact": "...",
  "current_next_action": "...",
  "superseded_next_action": "...",
  "late_confirmed_fact": "..."
}
值必须来自本会话明确写入的任务状态。"""


def session_total(session: dict[str, Any]) -> int | None:
    row = session.get("row") if isinstance(session, dict) else None
    value = row.get("totalTokens") if isinstance(row, dict) else None
    return int(value) if isinstance(value, (int, float)) else None


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
        default=ROOT / ".local" / "step6a-state-retention",
    )
    parser.add_argument("--turns", type=int, default=8)
    parser.add_argument("--update-turn", type=int, default=4)
    parser.add_argument("--payload-chars", type=int, default=9000)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--max-requests-per-turn", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--api-key-env", default="SCOPEX_API_KEY")
    args = parser.parse_args(argv)

    if not sys.platform.startswith("linux") or os.geteuid() == 0:
        raise ValueError("run on Spark Linux as the ordinary user, not sudo")
    if not 5 <= args.turns <= 20:
        raise ValueError("--turns must be between 5 and 20")
    if not 2 <= args.update_turn < args.turns:
        raise ValueError("--update-turn must be after turn 1 and before the last pressure turn")
    if not 1000 <= args.payload_chars <= 50_000:
        raise ValueError("--payload-chars must be between 1000 and 50000")
    if not 60 <= args.timeout <= 900:
        raise ValueError("--timeout must be between 60 and 900 seconds")

    cli = args.openclaw_bin.expanduser().resolve()
    if not cli.is_file() or not os.access(cli, os.X_OK):
        raise ValueError("OpenClaw CLI is not executable")
    docker_host = resolve_local_docker_host()
    api_key = os.environ.get(args.api_key_env, "")
    if "\n" in api_key or "\r" in api_key:
        raise ValueError("invalid API key environment value")

    old_action = code("NEXT-OLD")
    expected = {
        "task_goal": code("GOAL"),
        "safety_rule": code("RULE"),
        "confirmed_fact": code("FACT"),
        "rejected_hypothesis": code("REJECTED"),
        "key_artifact": f"/agent-data/logs/{code('artifact')}.log:L120-L145",
        "current_next_action": code("NEXT-CURRENT"),
        "superseded_next_action": old_action,
        "late_confirmed_fact": code("LATE-FACT"),
    }
    initial_state = dict(expected)
    initial_state["current_next_action"] = old_action

    tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    root = args.data_root.expanduser().resolve() / tag
    workspace = root / "workspace"
    runtime_root = root / "runtime"
    audit_root = root / "agent-turns"
    for path in (root, workspace, runtime_root, audit_root):
        path.mkdir(parents=True, mode=0o700, exist_ok=True)

    task_id = "step6a-state-" + uuid.uuid4().hex[:10]
    agent_id = "sxstate" + uuid.uuid4().hex[:8]
    session_key = f"agent:{agent_id}:{task_id}"
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
        "status": "STEP6A_STATE_RETENTION_FAILED",
        "root": str(root),
        "model": args.model,
        "session_key": session_key,
        "expected": expected,
        "turns": [],
    }

    try:
        turns: list[dict[str, Any]] = []
        all_completed = True
        previous_total: int | None = None
        token_drop_turns: list[int] = []
        summary_request_turns: list[int] = []
        safeguard_warnings: list[dict[str, Any]] = []

        for index in range(1, args.turns + 1):
            if index == 1:
                message = turn1_message(initial_state, args.payload_chars)
            elif index == args.update_turn:
                message = update_turn_message(
                    index,
                    args.payload_chars,
                    old_action=old_action,
                    new_action=expected["current_next_action"],
                    late_fact=expected["late_confirmed_fact"],
                )
            else:
                message = ordinary_turn_message(index, args.payload_chars)

            turn = runtime.run_turn(message, turn_name=f"turn-{index:03d}")
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
            requests = request_metrics(turn.audit_dir)
            usage = parse_wire_usage(turn.audit_dir)
            logs = compaction_log_lines(turn.audit_dir)
            current_total = session_total(session)
            if (
                previous_total is not None
                and current_total is not None
                and current_total < previous_total * 0.70
            ):
                token_drop_turns.append(index)
            if current_total is not None:
                previous_total = current_total

            # Normal agent requests expose the configured tool surface. OpenClaw
            # compaction summarization requests intentionally omit tools.
            if any(row.get("has_tools_field") is False for row in requests[1:]):
                summary_request_turns.append(index)
            for line in logs:
                if "summary-tail" in line or "summarization failed" in line.lower():
                    safeguard_warnings.append({"turn": index, "line": line})

            row = {
                "turn": index,
                "completed": completed,
                "answer": turn.cli_outcome.answer if turn.cli_outcome else None,
                "wall_s": turn.process.wall_s,
                "forwarded_requests": sum(1 for r in turn.proxy_records if r.get("forwarded") is True),
                "requests": requests,
                "wire_usage": usage,
                "session_total_tokens": current_total,
                "compaction_log_lines": logs,
            }
            turns.append(row)
            save_json(root / f"turn-{index:03d}-summary.json", row)
            if not completed:
                break

        final = runtime.run_turn(final_probe_message(), turn_name="turn-final-recall")
        final_answer = final.cli_outcome.answer if final.cli_outcome else None
        actual = parse_json_answer(final_answer)
        final_completed = bool(
            final.process.returncode == 0
            and final.process.stop_reason is None
            and final.cli_outcome is not None
            and final.cli_outcome.completed
        )
        all_completed = all_completed and final_completed
        field_results = {
            key: bool(actual is not None and actual.get(key) == value)
            for key, value in expected.items()
        }
        state_preserved = bool(actual is not None and set(actual) == set(expected) and all(field_results.values()))
        compaction_observed = bool(summary_request_turns and token_drop_turns)

        result.update(
            {
                "turns": turns,
                "final_answer": final_answer,
                "actual": actual,
                "field_results": field_results,
                "state_preserved": state_preserved,
                "all_turns_completed": all_completed,
                "summary_request_turns": summary_request_turns,
                "token_drop_turns": token_drop_turns,
                "compaction_observed": compaction_observed,
                "safeguard_warnings": safeguard_warnings,
            }
        )
        if all_completed and compaction_observed and state_preserved:
            result["status"] = "PASS_STEP6A_STATE_RETENTION"
        elif not compaction_observed:
            result["failure_reason"] = "compaction_not_proven"
        elif not state_preserved:
            result["failure_reason"] = "structured_state_not_preserved"
        else:
            result["failure_reason"] = "turn_failed"
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
        print("Step 6A state-retention audit:", root, file=sys.stderr)

    return 0 if result.get("status") == "PASS_STEP6A_STATE_RETENTION" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print("STEP6A_STATE_SETUP_ERROR:", str(exc), file=sys.stderr)
        raise SystemExit(2)
