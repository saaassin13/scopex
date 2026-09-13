#!/usr/bin/env python3
"""Validate hard model-request budget semantics with real OpenClaw + TaskService.

The fixture is a three-hop pointer chain whose next path is random and only
becomes knowable after reading the previous file. With max_requests=2 the agent
can collect some real Evidence but cannot finish the chain. ScopeX should then
finalize the facts already collected instead of reporting a generic turn error.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scopex.agent.docker_host import resolve_local_docker_host
from scopex.api.factory import LocalRuntimeConfig, OpenClawRuntimeFactory
from scopex.api.service import TaskService


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def wait_terminal(service: TaskService, task_id: str, timeout_s: float) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        row = service.get_task(task_id)
        if row.get("state") in {"COMPLETED", "FAILED", "CANCELLED"}:
            handle = service._handles.get(task_id)
            while handle is not None and handle.worker_alive and time.monotonic() < deadline:
                time.sleep(0.05)
            return service.get_task(task_id)
        time.sleep(0.1)
    raise TimeoutError("budget probe task did not become terminal")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--sandbox-image", required=True)
    ap.add_argument("--openclaw-bin", type=Path, default=Path.home() / ".openclaw/bin/openclaw")
    ap.add_argument("--data-root", type=Path, default=ROOT / ".local" / "step6c-budget")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--api-key-env", default="SCOPEX_API_KEY")
    args = ap.parse_args(argv)

    if not sys.platform.startswith("linux") or os.geteuid() == 0:
        raise ValueError("run on Spark Linux as the ordinary user, not sudo")
    cli = args.openclaw_bin.expanduser().resolve()
    if not cli.is_file() or not os.access(cli, os.X_OK):
        raise ValueError("OpenClaw CLI is not executable")
    api_key = os.environ.get(args.api_key_env, "")
    if "\n" in api_key or "\r" in api_key:
        raise ValueError("invalid API key environment value")

    tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    root = args.data_root.expanduser().resolve() / tag
    input_root = root / "input" / "budget"
    workspace = root / "workspace"
    for path in (root, input_root, workspace):
        path.mkdir(parents=True, mode=0o700, exist_ok=True)

    level2 = "level2-" + uuid.uuid4().hex + ".txt"
    level3 = "level3-" + uuid.uuid4().hex + ".txt"
    final_token = "FINAL-" + uuid.uuid4().hex
    (input_root / "start.txt").write_text(
        f"NEXT=/agent-data/budget/{level2}\n",
        encoding="utf-8",
    )
    (input_root / level2).write_text(
        f"NEXT=/agent-data/budget/{level3}\n",
        encoding="utf-8",
    )
    (input_root / level3).write_text(
        f"FINAL={final_token}\n",
        encoding="utf-8",
    )

    config = LocalRuntimeConfig(
        cli_path=cli,
        model_id=args.model,
        base_url=args.base_url,
        api_key=api_key,
        workspace=workspace,
        work_root=root / "work",
        sandbox_image=args.sandbox_image,
        docker_host=resolve_local_docker_host(),
        timeout_s=args.timeout,
        max_requests=2,
        max_tokens=2048,
        finalizer_max_tokens=768,
        finalizer_timeout_s=180,
        data_binds=(f"{root / 'input'}:/agent-data:ro",),
        exec_host="sandbox",
        exec_mode="full",
        enable_compaction=True,
    )
    factory = OpenClawRuntimeFactory(config)
    service = TaskService(
        audit_root=root / "tasks",
        coordinator_factory=factory.coordinator,
        finalizer_factory=factory.finalizer,
    )

    task_text = """从 /agent-data/budget/start.txt 开始逐层读取 NEXT 指向的文件，直到读到 FINAL=...。
NEXT 文件名是随机生成的，必须基于刚刚实际读到的内容决定下一路径，禁止猜测。
不要用一个 shell 命令递归读取多个文件；每一层都要在拿到上一层结果后再决定下一步。
最终报告你实际确认到的内容；如果运行时不允许继续，就只基于已经拿到的事实结束，不要编造 FINAL。"""

    result = {
        "status": "STEP6C_BUDGET_PROBE_FAILED",
        "root": str(root),
        "model": args.model,
        "max_requests_per_turn": 2,
        "expected_final_token": final_token,
    }

    try:
        task = service.create_task(task_text)
        task_id = task["id"]
        terminal = wait_terminal(service, task_id, args.timeout + 240)
        evidence = service.get_evidence(task_id)
        published = service.get_result(task_id)
        events = service.get_events(task_id)
        try:
            runtime_limit = service.store.read_json(task_id, "runtime-limit.json")
        except FileNotFoundError:
            runtime_limit = None

        investigation_reasons = []
        if published.get("available") and isinstance(published.get("result"), dict):
            value = published["result"].get("investigation_reasons")
            if isinstance(value, list):
                investigation_reasons = value

        evidence_items = evidence.get("items", []) if isinstance(evidence, dict) else []
        evidence_text = "\n".join(
            str(item.get("raw", ""))
            for item in evidence_items
            if isinstance(item, dict)
        )
        final_was_not_fabricated = final_token not in evidence_text
        reason_ok = (
            isinstance(runtime_limit, dict)
            and runtime_limit.get("reason") == "model_request_budget"
        )
        reasons_ok = investigation_reasons == ["budget_reached", "model_request_budget"]
        passed = all((
            terminal.get("state") == "COMPLETED",
            len(evidence_items) > 0,
            published.get("available") is True,
            reason_ok,
            reasons_ok,
            final_was_not_fabricated,
        ))

        result.update({
            "task_id": task_id,
            "terminal": terminal,
            "evidence_count": len(evidence_items),
            "evidence_raw": [
                item.get("raw") for item in evidence_items if isinstance(item, dict)
            ],
            "published_result": published,
            "runtime_limit": runtime_limit,
            "investigation_reasons": investigation_reasons,
            "event_types": [row.get("type") for row in events if isinstance(row, dict)],
            "final_was_not_in_evidence": final_was_not_fabricated,
        })
        if passed:
            result["status"] = "PASS_STEP6C_BUDGET_SEMANTICS"
        else:
            failures = []
            for name, ok in (
                ("completed_from_partial_evidence", terminal.get("state") == "COMPLETED"),
                ("evidence_present", len(evidence_items) > 0),
                ("result_published", published.get("available") is True),
                ("runtime_limit_reason", reason_ok),
                ("investigation_reasons", reasons_ok),
                ("no_fabricated_final", final_was_not_fabricated),
            ):
                if not ok:
                    failures.append(name)
            result["failure_reasons"] = failures
    except Exception as exc:
        result["error"] = type(exc).__name__ + ": " + str(exc)[:1600]
    finally:
        try:
            service.shutdown(timeout_s=10)
        except Exception as exc:
            result["shutdown_error"] = type(exc).__name__ + ": " + str(exc)[:500]
        save_json(root / "result.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("Step 6C budget audit:", root, file=sys.stderr)

    return 0 if result.get("status") == "PASS_STEP6C_BUDGET_SEMANTICS" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print("STEP6C_BUDGET_SETUP_ERROR:", str(exc), file=sys.stderr)
        raise SystemExit(2)
