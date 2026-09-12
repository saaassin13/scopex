#!/usr/bin/env python3
"""Run one real ScopeX Runtime MVP task against OpenClaw + local vLLM.

This is an integration smoke, not a new POC. It uses production modules under
scopex/ and the known three-log fixture. The only reused POC artifact is the
already-validated OpenClaw sandbox image reference from a passed POC02 preflight.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scopex.agent.runtime import OpenClawTaskSpec
from scopex.evidence.extractor import ReadResultExtractor
from scopex.events.progress import InMemoryEventSink
from scopex.finalizer.client import StreamingFinalizerClient
from scopex.finalizer.structured import StructuredFinalizer
from scopex.runtime.investigation import InvestigationCoordinator
from scopex.runtime.session import Session
from scopex.runtime.task import Task, TaskState
from scopex.storage.audit import AuditStore
from scopex.storage.runtime_audit import AuditEventSink, RuntimeAudit


FILES = ("app.log", "system.log", "robot.log")
TASK = """分析 2026-09-12 10:15 左右任务执行失败的现场证据。

请实际读取 /agent/app.log、/agent/system.log、/agent/robot.log，结合三份数据建立事实和时间关系。
不要把 status=137 自动等同于 OOM；不要把当前日志窗口未见机器人异常扩大成全局健康结论。
调查方法由你决定。完成必要调查后给出简短结果。"""


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sandbox_image(preflight: Path) -> str:
    result = load_json(preflight / "result.json")
    if result.get("status") != "PREFLIGHT_PASS_NOT_MODEL_EVAL":
        raise ValueError("--preflight must be a passed POC02 native preflight")
    cfg = load_json(preflight / "openclaw.json")
    image = cfg.get("agents", {}).get("defaults", {}).get("sandbox", {}).get("docker", {}).get("image")
    if not isinstance(image, str) or not image:
        raise ValueError("preflight openclaw.json has no sandbox image")
    return image


def docker_host() -> str:
    direct = os.environ.get("DOCKER_HOST", "")
    if direct and not os.environ.get("DOCKER_CONTEXT"):
        host = direct
    else:
        docker = shutil.which("docker")
        if not docker:
            raise ValueError("docker CLI is required")
        proc = subprocess.run(
            [docker, "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode != 0:
            raise ValueError("failed to inspect local Docker context")
        host = json.loads(proc.stdout.strip())
    if not isinstance(host, str) or not host.startswith("unix://"):
        raise ValueError("Runtime MVP smoke requires a local unix Docker socket")
    return host


def stage_fixture(workspace: Path, source: Path) -> dict[str, int]:
    workspace.mkdir(parents=True, mode=0o700)
    sizes = {}
    for name in FILES:
        src = source / name
        if not src.is_file() or src.is_symlink():
            raise ValueError(f"missing fixture file: {src}")
        dst = workspace / name
        shutil.copy2(src, dst)
        sizes[name] = dst.stat().st_size
    return sizes


def evidence_coverage(coordinator: InvestigationCoordinator) -> dict[str, bool]:
    sources = [Path(item.source).name for item in coordinator.catalog.items]
    return {name: name in sources for name in FILES}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--preflight", type=Path, required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--max-requests", type=int, default=6)
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--finalizer-max-tokens", type=int, default=512)
    ap.add_argument("--api-key-env", default="SCOPEX_API_KEY")
    ap.add_argument("--openclaw-bin", type=Path, default=Path.home() / ".openclaw/bin/openclaw")
    ap.add_argument("--fixture", type=Path, default=ROOT / "tests" / "fixtures" / "poc04")
    args = ap.parse_args(argv)

    if not sys.platform.startswith("linux") or os.geteuid() == 0:
        raise ValueError("run on Spark Linux as the ordinary user, not sudo")
    if not 60 <= args.timeout <= 600:
        raise ValueError("timeout must be between 60 and 600 seconds")
    if not 2 <= args.max_requests <= 12:
        raise ValueError("max-requests must be between 2 and 12")

    cli = args.openclaw_bin.expanduser().resolve()
    if not cli.is_file() or not os.access(cli, os.X_OK):
        raise ValueError("OpenClaw CLI is not executable")
    preflight = args.preflight.expanduser().resolve()
    image = sandbox_image(preflight)
    host = docker_host()
    api_key = os.environ.get(args.api_key_env, "")
    if "\n" in api_key or "\r" in api_key:
        raise ValueError("invalid API key environment value")

    tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    root = ROOT / ".local" / "runtime-mvp-smoke" / tag
    workspace = root / "workspace"
    runtime_root = root / "runtime"
    turn_audit = root / "agent-turns"
    task_store_root = root / "tasks"
    for path in (root, runtime_root, turn_audit, task_store_root):
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
    staged = stage_fixture(workspace, args.fixture.resolve())

    task_id = "mvp-" + uuid.uuid4().hex[:12]
    session_key = f"agent:sxmvp:{task_id}"
    task = Task(task_id, TASK, session_key)
    session = Session(task_id, session_key)
    memory_events = InMemoryEventSink()
    store = AuditStore(task_store_root)
    events = AuditEventSink(store, downstream=memory_events)
    audit = RuntimeAudit(store, task_id)

    spec = OpenClawTaskSpec(
        cli_path=cli,
        model_id=args.model,
        upstream_base_url=args.base_url,
        upstream_api_key=api_key,
        workspace=workspace,
        runtime_root=runtime_root,
        audit_root=turn_audit,
        image=image,
        docker_host=host,
        agent_id="sxmvp" + uuid.uuid4().hex[:8],
        uid=os.getuid(),
        gid=os.getgid(),
        timeout_s=args.timeout,
        max_requests=args.max_requests,
        max_tokens=args.max_tokens,
        skills=(),
    )

    coordinator = InvestigationCoordinator.for_openclaw(
        task=task,
        session=session,
        spec=spec,
        events=events,
        extractors=(ReadResultExtractor(max_chars=32768),),
        audit=audit,
    )

    result = {
        "status": "RUNTIME_MVP_SMOKE_FAILED",
        "task_id": task_id,
        "session_key": session_key,
        "root": str(root),
        "model": args.model,
        "staged": staged,
    }
    try:
        turn = coordinator.start(TASK, turn_name="turn-001")
        coverage = evidence_coverage(coordinator)
        result["investigation"] = {
            "returncode": turn.process.returncode,
            "stop_reason": turn.process.stop_reason,
            "wall_s": turn.process.wall_s,
            "proxy_requests": len(turn.proxy_records),
            "forwarded_requests": sum(1 for row in turn.proxy_records if row.get("forwarded") is True),
            "coverage": coverage,
            "evidence_count": len(coordinator.catalog.items),
        }
        if not all(coverage.values()):
            result["error"] = "investigation did not read all three required fixture files"
            coordinator.controller.fail("smoke_missing_required_evidence")
            audit.snapshot_control(task, session, coordinator.catalog)
        else:
            finalizer = StructuredFinalizer(
                StreamingFinalizerClient(args.base_url, api_key=api_key, timeout_s=120),
                model=args.model,
                max_tokens=args.finalizer_max_tokens,
            )
            final = coordinator.finalize_fresh(finalizer, goal_satisfied=True)
            result["finalizer"] = {
                "valid": final.valid,
                "parse_error": final.parse_error,
                "finish_reasons": list(final.transport.finish_reasons),
                "done_seen": final.transport.done_seen,
                "elapsed_s": final.transport.elapsed_s,
                "usage": final.transport.usage,
                "errors": list(final.finalization.errors) if final.finalization else [],
                "rendered": final.finalization.rendered if final.finalization else None,
            }
            result["status"] = (
                "PASS_RUNTIME_MVP_SMOKE"
                if final.valid and task.state is TaskState.COMPLETED
                else "RUNTIME_MVP_SMOKE_FAILED"
            )
    except Exception as exc:
        if not task.terminal:
            coordinator.controller.fail("runtime_mvp_smoke_exception")
        result["error"] = type(exc).__name__ + ": " + str(exc)[:800]
    finally:
        try:
            cleanup = coordinator.close()
            result["sandbox_cleanup"] = cleanup.__dict__ if hasattr(cleanup, "__dict__") else str(cleanup)
        except Exception as exc:
            result["cleanup_error"] = type(exc).__name__ + ": " + str(exc)[:300]
        result["task_state"] = task.state.value
        result["events"] = [event.to_dict() for event in memory_events.events]
        audit.snapshot_control(task, session, coordinator.catalog)
        store.write_json(task_id, "smoke.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        print("runtime mvp smoke audit:", root)

    return 0 if result.get("status") == "PASS_RUNTIME_MVP_SMOKE" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        print("RUNTIME_MVP_SMOKE_SETUP_ERROR:", str(exc), file=sys.stderr)
        sys.exit(2)
