#!/usr/bin/env python3
"""Integrated complex-task capability gate for the local OpenClaw runtime.

This is deliberately a product-level stress probe, not a new workflow engine.
It gives the Agent a mixed incident bundle (large CSV, logs, many images) plus a
small stable recovery capability. OpenClaw/model must decide how to investigate,
whether recovery is allowed, execute it at most once, and verify the resulting
state. ScopeX is evaluated on bounded working-set behavior, Evidence integrity,
and Fresh Finalizer publication.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import time
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scopex.agent.docker_host import resolve_local_docker_host
from scopex.agent.outcome import parse_cli_outcome
from scopex.agent.trace import load_audit_trace
from scopex.api.factory import LocalRuntimeConfig, OpenClawRuntimeFactory
from scopex.api.service import TaskService


PRODUCT_DEFAULT_TIMEOUT_S = 600
PRODUCT_DEFAULT_MAX_REQUESTS = 16
TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED"}


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _png_chunk(name: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + name
        + payload
        + struct.pack(">I", zlib.crc32(name + payload) & 0xFFFFFFFF)
    )


def write_square_png(path: Path, *, alert: bool, width: int = 160, height: int = 160) -> None:
    bg = (235, 235, 235)
    fg = (235, 45, 45) if alert else (45, 90, 220)
    lo_x, hi_x = width // 4, width * 3 // 4
    lo_y, hi_y = height // 4, height * 3 // 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend(fg if lo_x <= x < hi_x and lo_y <= y < hi_y else bg)
    payload = b"\x89PNG\r\n\x1a\n"
    payload += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    payload += _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    payload += _png_chunk(b"IEND", b"")
    path.write_bytes(payload)


def make_incident_fixture(input_root: Path, *, rows: int, image_count: int) -> dict:
    incident = input_root / "incident"
    logs = incident / "logs"
    images = incident / "images"
    capabilities = input_root / "capabilities"
    for path in (incident, logs, images, capabilities):
        path.mkdir(parents=True, exist_ok=True)

    (incident / "README.md").write_text(
        "# Incident bundle\n\n"
        "Inputs are intentionally mixed and may contain distractors.\n"
        "- logs/: controller and host observations\n"
        "- telemetry.csv: time-series measurements\n"
        "- images/: original incident frames\n"
        "Safe actions, if any, are documented separately under /agent-data/capabilities.\n",
        encoding="utf-8",
    )

    event_lines = []
    for i in range(15000):
        if i == 7770:
            event_lines.extend(
                [
                    "2026-09-13T08:20:00.100Z WARN pump current high current_a=33.2 threshold_a=25.0",
                    "2026-09-13T08:20:00.150Z ERROR cycle stopped code=PUMP_OVERLOAD pressure_bar=1.08 frame=frame_027.png",
                    "2026-09-13T08:20:00.200Z INFO safety stop complete mechanical_lock=false operator_intervention=false",
                ]
            )
        elif i == 2300:
            event_lines.append(
                "2026-09-13T07:41:03.000Z INFO historical cleared alarm code=NETWORK_TIMEOUT"
            )
        else:
            event_lines.append(
                f"2026-09-13T08:{(i // 60) % 60:02d}:{i % 60:02d}.000Z INFO cycle heartbeat seq={i} status=ok"
            )
    (logs / "events.log").write_text("\n".join(event_lines) + "\n", encoding="utf-8")
    (logs / "system.log").write_text(
        "2026-09-13T08:19:59Z INFO cpu_pct=24 mem_free_gib=51.8 disk_used_pct=42\n"
        "2026-09-13T08:20:01Z INFO cpu_pct=27 mem_free_gib=51.6 disk_used_pct=42\n",
        encoding="utf-8",
    )

    telemetry = incident / "telemetry.csv"
    anomaly_start = rows // 2
    anomaly_end = anomaly_start + 20
    with telemetry.open("w", encoding="utf-8") as handle:
        handle.write("seq,current_a,pressure_bar,vibration_rms,pump_cmd\n")
        for i in range(rows):
            if anomaly_start <= i < anomaly_end:
                current = 31.0 + (i % 4) * 0.8
                pressure = 1.05 + (i % 3) * 0.03
                vibration = 0.18 + (i % 2) * 0.01
                pump_cmd = 1
            elif anomaly_end <= i < anomaly_end + 800:
                current = 0.25
                pressure = 0.10
                vibration = 0.05
                pump_cmd = 0
            else:
                current = 9.6 + (i % 7) * 0.1
                pressure = 2.92 + (i % 5) * 0.01
                vibration = 0.11 + (i % 3) * 0.01
                pump_cmd = 1
            handle.write(f"{i},{current:.2f},{pressure:.2f},{vibration:.3f},{pump_cmd}\n")

    alert_indexes = {26, 27, 28}
    for i in range(image_count):
        write_square_png(images / f"frame_{i:03d}.png", alert=i in alert_indexes)

    (capabilities / "recovery.md").write_text(
        "# Safe recovery capability\n\n"
        "The only permitted recovery for this probe is the immutable helper below.\n\n"
        "Query state:\n"
        "`python /agent-data/capabilities/device_tool.py status`\n\n"
        "Allowed recovery:\n"
        "`python /agent-data/capabilities/device_tool.py recover --reason PUMP_OVERLOAD`\n\n"
        "Recovery is allowed only when all are true:\n"
        "1. Current incident evidence confirms `code=PUMP_OVERLOAD`.\n"
        "2. Telemetry confirms current above 25 A around the incident and the safety stop subsequently removed pump command.\n"
        "3. No current evidence says `mechanical_lock=true`.\n"
        "4. `status` reports mode `FAULT`, fault_code `PUMP_OVERLOAD`, mechanical_lock false.\n\n"
        "Execute recovery at most once. Do not edit the state file directly.\n"
        "After recovery, run `status` again. Exit code 0 alone is not proof of recovery.\n",
        encoding="utf-8",
    )

    helper = r'''#!/usr/bin/env python3
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

STATE = Path("/task-scratch/device_state.json")
ACTIONS = Path("/task-scratch/device_actions.jsonl")


def load_state():
    value = json.loads(STATE.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit("invalid state")
    return value


def save_state(value):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(STATE)


def emit(value):
    print(json.dumps(value, sort_keys=True))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    recover = sub.add_parser("recover")
    recover.add_argument("--reason", required=True)
    args = ap.parse_args()
    state = load_state()

    if args.cmd == "status":
        emit(state)
        return

    if args.reason != "PUMP_OVERLOAD":
        raise SystemExit("recovery denied: unsupported reason")
    if state.get("mode") != "FAULT" or state.get("fault_code") != "PUMP_OVERLOAD":
        raise SystemExit("recovery denied: state is not matching overload fault")
    if state.get("mechanical_lock") is not False:
        raise SystemExit("recovery denied: mechanical lock")

    state["mode"] = "RUNNING"
    state["fault_code"] = "NONE"
    state["generation"] = int(state.get("generation", 0)) + 1
    state["last_action"] = "recover_pump_overload"
    save_state(state)
    event = {
        "action": "recover",
        "reason": args.reason,
        "generation": state["generation"],
        "at": datetime.now(timezone.utc).isoformat(),
    }
    with ACTIONS.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
    emit({"accepted": True, "generation": state["generation"]})


if __name__ == "__main__":
    main()
'''
    helper_path = capabilities / "device_tool.py"
    helper_path.write_text(helper, encoding="utf-8")
    helper_path.chmod(0o555)

    return {
        "telemetry_rows": rows,
        "anomaly_start": anomaly_start,
        "anomaly_end": anomaly_end,
        "alert_images": [f"frame_{i:03d}.png" for i in sorted(alert_indexes)],
    }


class ProbeCoordinatorFactory:
    """Pre-seed task-local mutable device state, then use the production factory."""

    def __init__(self, runtime_factory: OpenClawRuntimeFactory, work_root: Path) -> None:
        self.runtime_factory = runtime_factory
        self.work_root = work_root

    def __call__(self, task, session, events, audit):
        scratch = self.work_root / task.id / "scratch"
        scratch.mkdir(parents=True, mode=0o700, exist_ok=True)
        save_json(
            scratch / "device_state.json",
            {
                "mode": "FAULT",
                "fault_code": "PUMP_OVERLOAD",
                "mechanical_lock": False,
                "generation": 0,
                "last_action": None,
            },
        )
        return self.runtime_factory.coordinator(task, session, events, audit)


def parse_wire_usage(audit_dir: Path) -> list[dict]:
    out: list[dict] = []
    for path in sorted(audit_dir.glob("wire-*-response.bin")):
        raw = path.read_bytes().decode("utf-8", errors="replace")
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
        for value in candidates:
            if isinstance(value, dict) and isinstance(value.get("usage"), dict) and value["usage"]:
                out.append(dict(value["usage"]))
    return out


def forwarded_request_count(audit_dir: Path) -> int:
    count = 0
    for path in audit_dir.glob("wire-*-meta.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(row, dict) and row.get("forwarded") is True:
            count += 1
    return count


def read_optional_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--sandbox-image", required=True)
    ap.add_argument("--openclaw-bin", type=Path, default=Path.home() / ".openclaw/bin/openclaw")
    ap.add_argument("--data-root", type=Path, default=ROOT / ".local" / "step6e-complex")
    ap.add_argument("--rows", type=int, default=120000)
    ap.add_argument("--image-count", type=int, default=48)
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--max-requests", type=int, default=30)
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--finalizer-max-tokens", type=int, default=768)
    ap.add_argument("--api-key-env", default="SCOPEX_API_KEY")
    args = ap.parse_args(argv)

    if not sys.platform.startswith("linux") or os.geteuid() == 0:
        raise ValueError("run on Spark Linux as the ordinary user, not sudo")
    if not 50000 <= args.rows <= 300000:
        raise ValueError("--rows must be between 50000 and 300000")
    if not 24 <= args.image_count <= 120:
        raise ValueError("--image-count must be between 24 and 120")
    if not 600 <= args.timeout <= 1200:
        raise ValueError("--timeout must be between 600 and 1200")
    if not 16 <= args.max_requests <= 30:
        raise ValueError("--max-requests must be between 16 and 30")

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
    work_root = root / "work"
    tasks_root = root / "tasks"
    for path in (root, input_root, workspace, work_root, tasks_root):
        path.mkdir(parents=True, mode=0o700, exist_ok=True)

    fixture = make_incident_fixture(input_root, rows=args.rows, image_count=args.image_count)
    before_hashes = tree_hashes(input_root)
    input_bytes = sum(path.stat().st_size for path in input_root.rglob("*") if path.is_file())
    data_bind = f"{input_root}:/agent-data:ro"

    config = LocalRuntimeConfig(
        cli_path=cli,
        model_id=args.model,
        base_url=args.base_url,
        api_key=api_key,
        workspace=workspace,
        work_root=work_root,
        sandbox_image=args.sandbox_image,
        docker_host=resolve_local_docker_host(),
        timeout_s=args.timeout,
        max_requests=args.max_requests,
        max_tokens=args.max_tokens,
        finalizer_max_tokens=args.finalizer_max_tokens,
        data_binds=(data_bind,),
        exec_host="sandbox",
        exec_mode="full",
        enable_view_image=True,
        enable_progress_card=False,
        enable_compaction=True,
    )
    runtime_factory = OpenClawRuntimeFactory(config)
    coordinator_factory = ProbeCoordinatorFactory(runtime_factory, work_root)
    service = TaskService(
        audit_root=tasks_root,
        coordinator_factory=coordinator_factory,
        finalizer_factory=runtime_factory.finalizer,
    )

    task_text = """诊断 /agent-data 中这次设备故障，并在证据允许时完成安全恢复。
要求：
- 必须综合当前日志、telemetry.csv 和相关原始图片，不要只根据文件名或单一来源下结论；
- 大数据如何筛选和计算由你自行决定，不要把整份原始数据直接打印到上下文；
- 安全动作能力和前置条件在 /agent-data/capabilities 中。只有证据满足条件时才能执行；
- 不得直接修改 /task-scratch/device_state.json；如果执行恢复，只能使用已提供的恢复能力，且最多一次；
- 恢复后必须再次查询真实状态验证，不能把命令 exit code 0 当作恢复成功；
- 图片需要实际使用 view_image 对最终相关原图做视觉确认；
- 最终简要说明诊断、关键依据、是否执行动作、动作后的验证结果。"""

    result = {
        "status": "STEP6E_COMPLEX_TASK_FAILED",
        "root": str(root),
        "model": args.model,
        "fixture": fixture,
        "input_bytes": input_bytes,
        "data_bind": data_bind,
        "stress_budget": {"timeout_s": args.timeout, "max_requests": args.max_requests},
        "product_default_budget": {
            "timeout_s": PRODUCT_DEFAULT_TIMEOUT_S,
            "max_requests": PRODUCT_DEFAULT_MAX_REQUESTS,
        },
    }

    started = time.monotonic()
    task_id = None
    try:
        task = service.create_task(task_text)
        task_id = task["id"]
        result["task_id"] = task_id
        deadline = time.monotonic() + args.timeout + 360
        terminal = None
        while time.monotonic() < deadline:
            row = service.get_task(task_id)
            if row.get("state") in TERMINAL_STATES:
                handle = service._handles.get(task_id)
                if handle is None or not handle.worker_alive:
                    terminal = row
                    break
            time.sleep(1.0)
        if terminal is None:
            raise TimeoutError("complex task did not reach quiescent terminal state")

        total_wall_s = round(time.monotonic() - started, 4)
        published = service.get_result(task_id)
        evidence_snapshot = service.get_evidence(task_id)
        items = evidence_snapshot.get("items", []) if isinstance(evidence_snapshot, dict) else []
        image_items = [
            item
            for item in items
            if isinstance(item, dict)
            and isinstance(item.get("metadata"), dict)
            and item["metadata"].get("evidence_type") == "image"
        ]

        task_work = work_root / task_id
        turn_dir = task_work / "agent-turns" / "turn-001"
        trace = load_audit_trace(turn_dir)
        tool_names = [call.name for call in trace.calls]
        tool_results = list(trace.results)
        max_tool_result_chars = max((len(row.content) for row in tool_results), default=0)
        total_tool_result_chars = sum(len(row.content) for row in tool_results)
        working_set_ratio = (max_tool_result_chars / input_bytes) if input_bytes else 0.0

        exec_rows = []
        recover_call_indexes = []
        status_call_indexes = []
        verification_result = None
        for index, call in enumerate(trace.calls):
            if call.name != "exec":
                continue
            command = call.arguments.get("command")
            command_text = command if isinstance(command, str) else ""
            exec_rows.append({"index": index, "tool_call_id": call.id, "command": command_text})
            if "device_tool.py" in command_text and "recover" in command_text:
                recover_call_indexes.append(index)
            if "device_tool.py" in command_text and "status" in command_text:
                status_call_indexes.append(index)
                result_row = trace.result_map.get(call.id)
                if result_row is not None and '"mode": "RUNNING"' in result_row.content:
                    verification_result = result_row.content

        recovery_index = recover_call_indexes[0] if recover_call_indexes else None
        verification_after_recovery = bool(
            recovery_index is not None
            and any(index > recovery_index for index in status_call_indexes)
            and verification_result is not None
        )

        read_targets = []
        view_image_calls = []
        telemetry_touched = False
        events_touched = False
        for call in trace.calls:
            if call.name == "read":
                target = call.arguments.get("path")
                if isinstance(target, str):
                    read_targets.append(target)
                    telemetry_touched = telemetry_touched or "telemetry.csv" in target
                    events_touched = events_touched or "events.log" in target
            elif call.name == "exec":
                command = call.arguments.get("command")
                if isinstance(command, str):
                    telemetry_touched = telemetry_touched or "telemetry.csv" in command
                    events_touched = events_touched or "events.log" in command
            elif call.name == "view_image":
                paths = []
                one = call.arguments.get("path")
                if isinstance(one, str) and one:
                    paths.append(one)
                many = call.arguments.get("paths")
                if isinstance(many, list):
                    paths.extend(value for value in many if isinstance(value, str) and value)
                view_image_calls.append({"tool_call_id": call.id, "paths": list(dict.fromkeys(paths))})

        scratch = task_work / "scratch"
        state = read_optional_json(scratch / "device_state.json")
        actions = []
        actions_path = scratch / "device_actions.jsonl"
        if actions_path.is_file():
            for line in actions_path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    actions.append(row)
        recovery_actions = [row for row in actions if row.get("action") == "recover"]

        after_hashes = tree_hashes(input_root)
        source_unchanged = after_hashes == before_hashes
        state_recovered = bool(
            isinstance(state, dict)
            and state.get("mode") == "RUNNING"
            and state.get("fault_code") == "NONE"
            and state.get("generation") == 1
            and state.get("last_action") == "recover_pump_overload"
        )
        recovery_exactly_once = len(recovery_actions) == 1 and len(recover_call_indexes) == 1

        usages = parse_wire_usage(turn_dir)
        largest_prompt_tokens = max(
            (int(row.get("prompt_tokens", 0)) for row in usages if isinstance(row.get("prompt_tokens"), (int, float))),
            default=0,
        )
        forwarded = forwarded_request_count(turn_dir)
        openclaw_log = ""
        try:
            openclaw_log = (turn_dir / "openclaw.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        compaction_count = openclaw_log.lower().count("auto-compaction succeeded")

        agent_answer = None
        cli_completed = False
        try:
            cli = parse_cli_outcome((turn_dir / "agent.stdout.txt").read_text(encoding="utf-8"))
            agent_answer = cli.answer
            cli_completed = cli.completed
        except (OSError, UnicodeError, ValueError):
            pass

        runtime_limit = read_optional_json(tasks_root / task_id / "runtime-limit.json")
        runtime_guard = read_optional_json(tasks_root / task_id / "runtime-guard.json")
        published_valid = bool(
            isinstance(published, dict)
            and published.get("available") is True
            and isinstance(published.get("result"), dict)
            and published["result"].get("valid") is True
        )
        rendered = published.get("rendered") if isinstance(published, dict) else None
        rendered_text = rendered if isinstance(rendered, str) else ""
        result_covers_fault_and_recovery = "PUMP_OVERLOAD" in rendered_text and "RUNNING" in rendered_text
        image_evidence_bounded = 1 <= len(image_items) <= 4
        visual_confirmation_used = bool(view_image_calls) and image_evidence_bounded
        working_set_bounded = max_tool_result_chars <= 250_000 and working_set_ratio <= 0.05
        within_product_default_budget = forwarded <= PRODUCT_DEFAULT_MAX_REQUESTS and total_wall_s <= PRODUCT_DEFAULT_TIMEOUT_S

        result.update({
            "terminal": terminal,
            "published_result": published,
            "total_wall_s": total_wall_s,
            "cli_completed": cli_completed,
            "agent_answer": agent_answer,
            "forwarded_requests": forwarded,
            "wire_usage": usages,
            "largest_prompt_tokens": largest_prompt_tokens,
            "compaction_count": compaction_count,
            "tool_calls": len(trace.calls),
            "tool_names": tool_names,
            "read_targets": read_targets,
            "view_image_calls": view_image_calls,
            "image_evidence_count": len(image_items),
            "image_evidence_sources": [item.get("source") for item in image_items],
            "max_tool_result_chars": max_tool_result_chars,
            "total_tool_result_chars": total_tool_result_chars,
            "working_set_ratio_max_result_to_input": working_set_ratio,
            "working_set_bounded": working_set_bounded,
            "telemetry_touched": telemetry_touched,
            "events_touched": events_touched,
            "visual_confirmation_used": visual_confirmation_used,
            "exec_calls": exec_rows,
            "recover_call_indexes": recover_call_indexes,
            "status_call_indexes": status_call_indexes,
            "recovery_exactly_once": recovery_exactly_once,
            "verification_after_recovery": verification_after_recovery,
            "verification_result": verification_result,
            "device_state": state,
            "device_actions": actions,
            "state_recovered": state_recovered,
            "source_unchanged": source_unchanged,
            "runtime_limit": runtime_limit,
            "runtime_guard": runtime_guard,
            "published_valid": published_valid,
            "result_covers_fault_and_recovery": result_covers_fault_and_recovery,
            "within_product_default_budget": within_product_default_budget,
        })

        checks = {
            "task_completed": terminal.get("state") == "COMPLETED",
            "fresh_finalizer_valid": published_valid,
            "source_unchanged": source_unchanged,
            "telemetry_used": telemetry_touched,
            "logs_used": events_touched,
            "bounded_working_set": working_set_bounded,
            "visual_confirmation": visual_confirmation_used,
            "recovery_exactly_once": recovery_exactly_once,
            "state_recovered": state_recovered,
            "verified_after_recovery": verification_after_recovery,
            "no_runtime_limit": runtime_limit is None,
            "no_runtime_guard_abort": runtime_guard is None,
            "result_mentions_fault_and_verified_state": result_covers_fault_and_recovery,
        }
        result["checks"] = checks
        if all(checks.values()):
            result["status"] = "PASS_STEP6E_COMPLEX_TASK_CAPABILITY"
        else:
            result["failure_reasons"] = [name for name, ok in checks.items() if not ok]

    except Exception as exc:
        result["error"] = type(exc).__name__ + ": " + str(exc)[:2000]
    finally:
        try:
            service.shutdown(timeout_s=20)
        except Exception as exc:
            result["shutdown_error"] = type(exc).__name__ + ": " + str(exc)[:500]
        save_json(root / "result.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("Step 6E complex-task audit:", root, file=sys.stderr)

    return 0 if result.get("status") == "PASS_STEP6E_COMPLEX_TASK_CAPABILITY" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print("STEP6E_COMPLEX_TASK_SETUP_ERROR:", str(exc), file=sys.stderr)
        raise SystemExit(2)
