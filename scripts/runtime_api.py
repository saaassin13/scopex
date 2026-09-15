#!/usr/bin/env python3
"""Run the loopback-only ScopeX FastAPI product server."""
from __future__ import annotations

import argparse
import fcntl
import ipaddress
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn

from scopex.agent.docker_host import resolve_local_docker_host
from scopex.agent.skills import DEFAULT_BUILTIN_SKILLS, prepare_workspace_skills
from scopex.api.factory import LocalRuntimeConfig, OpenClawRuntimeFactory
from scopex.api.fastapi_app import create_app
from scopex.api.schedules import ScheduleService
from scopex.api.service import TaskService
from scopex.data_catalog import (
    catalog_binds,
    load_data_catalog,
    provision_locator_catalog,
    provision_workspace_catalog,
    render_runtime_catalog_summary,
)


def loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_data_dir(value: str) -> str:
    try:
        host_text, agent_dir = value.rsplit(":", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--data-dir must use HOST_DIR:AGENT_DIR") from exc
    host_dir = Path(host_text).expanduser().resolve()
    if not host_dir.is_dir():
        raise argparse.ArgumentTypeError(f"data directory does not exist: {host_dir}")
    if not agent_dir.startswith("/") or agent_dir == "/" or ":" in agent_dir:
        raise argparse.ArgumentTypeError("agent data directory must be an absolute non-root path")
    return f"{host_dir}:{agent_dir}:ro"


def merge_data_binds(defaults: tuple[str, ...], overrides: tuple[str, ...]) -> tuple[str, ...]:
    ordered: list[str] = []
    target_to_index: dict[str, int] = {}
    for bind in defaults + overrides:
        parts = bind.rsplit(":", 2)
        if len(parts) != 3 or parts[2] != "ro":
            raise ValueError(f"invalid read-only data bind: {bind}")
        target = parts[1]
        if target in target_to_index:
            ordered[target_to_index[target]] = bind
        else:
            target_to_index[target] = len(ordered)
            ordered.append(bind)
    return tuple(ordered)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--sandbox-image", required=True)
    parser.add_argument("--openclaw-bin", type=Path, default=Path.home() / ".openclaw/bin/openclaw")
    parser.add_argument("--data-root", type=Path, default=ROOT / ".local" / "runtime-api")
    parser.add_argument(
        "--data-catalog",
        type=Path,
        default=ROOT / "config" / "data-catalog.json",
        help="ScopeX semantic data catalog copied into host workspace and data-locator Skill",
    )
    parser.add_argument("--no-catalog-binds", action="store_true")
    parser.add_argument("--data-dir", action="append", type=parse_data_dir, default=[], metavar="HOST_DIR:AGENT_DIR")
    parser.add_argument("--exec-host", choices=("sandbox", "gateway", "node"), default="sandbox")
    parser.add_argument("--exec-mode", choices=("deny", "allowlist", "ask", "auto", "full"), default="full")
    parser.add_argument("--enable-view-image", action="store_true")
    parser.add_argument("--enable-progress-card", action="store_true")
    compaction = parser.add_mutually_exclusive_group()
    compaction.add_argument("--enable-compaction", dest="enable_compaction", action="store_true",
                            help="opt in to OpenClaw proactive/post-turn session maintenance")
    compaction.add_argument("--disable-compaction", dest="enable_compaction", action="store_false",
                            help="default for independent tasks; native overflow recovery remains available")
    parser.set_defaults(enable_compaction=False)
    parser.add_argument("--web-dist", type=Path, default=ROOT / "frontend" / "dist")
    parser.add_argument("--api-key-env", default="SCOPEX_API_KEY")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--max-requests", type=int, default=16)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--finalizer-max-tokens", type=int, default=768, help="legacy finalizer compatibility only")
    parser.add_argument("--report-max-tokens", type=int, default=2048)
    parser.add_argument("--max-active-tasks", type=int, default=2, help="independent task slots; not a GPU throughput guarantee")
    parser.add_argument("--max-queued-tasks", type=int, default=16)
    parser.add_argument("--queue-timeout", type=int, default=600)
    parser.add_argument("--finalizer-timeout", type=int, default=180)
    parser.add_argument("--skill", action="append", default=[])
    parser.add_argument("--no-default-skills", action="store_true")
    args = parser.parse_args(argv)

    if not sys.platform.startswith("linux") or os.geteuid() == 0:
        raise ValueError("run Runtime API on Spark Linux as the ordinary user, not sudo")
    if not loopback_host(args.host):
        raise ValueError("Runtime API may bind only to loopback")
    if not 1 <= args.port <= 65535:
        raise ValueError("port must be between 1 and 65535")

    if args.max_active_tasks > 1 and args.exec_host != "sandbox":
        raise ValueError("parallel product runs require isolated sandbox execution")
    os.umask(0o077)
    data_root = args.data_root.expanduser().resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    # The in-process admission queue has exactly one owner. Do not run multiple
    # uvicorn workers against this state root or reconcile another live process.
    runtime_lock = (data_root / ".runtime.lock").open("a")
    try:
        fcntl.flock(runtime_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        runtime_lock.close()
        raise ValueError("another ScopeX Runtime owns this data-root") from exc
    api_key = os.environ.get(args.api_key_env, "")
    if "\n" in api_key or "\r" in api_key:
        raise ValueError("invalid API key environment value")

    workspace = args.workspace.expanduser().resolve()
    requested_skills = (() if args.no_default_skills else DEFAULT_BUILTIN_SKILLS) + tuple(args.skill)
    skills = prepare_workspace_skills(workspace=workspace, skill_names=requested_skills, builtin_root=ROOT / "skills")

    catalog_path = args.data_catalog.expanduser().resolve()
    catalog = load_data_catalog(catalog_path)
    workspace_catalog = provision_workspace_catalog(workspace=workspace, catalog_path=catalog_path)
    locator_catalog = None
    if "data-locator" in skills:
        locator_catalog = provision_locator_catalog(workspace=workspace, catalog_path=catalog_path)
    catalog_summary = render_runtime_catalog_summary(catalog)
    catalog_defaults = () if args.no_catalog_binds else catalog_binds(catalog, existing_only=True)
    data_binds = merge_data_binds(catalog_defaults, tuple(args.data_dir))

    data_root = args.data_root.expanduser().resolve()
    config = LocalRuntimeConfig(
        cli_path=args.openclaw_bin.expanduser().resolve(),
        model_id=args.model,
        base_url=args.base_url,
        api_key=api_key,
        workspace=workspace,
        work_root=data_root / "work",
        sandbox_image=args.sandbox_image,
        docker_host=resolve_local_docker_host(),
        timeout_s=args.timeout,
        max_requests=args.max_requests,
        max_tokens=args.max_tokens,
        finalizer_max_tokens=args.finalizer_max_tokens,
        report_max_tokens=args.report_max_tokens,
        finalizer_timeout_s=args.finalizer_timeout,
        skills=skills,
        data_binds=data_binds,
        data_catalog_summary=catalog_summary,
        exec_host=args.exec_host,
        exec_mode=args.exec_mode,
        enable_view_image=args.enable_view_image,
        enable_progress_card=args.enable_progress_card,
        enable_compaction=args.enable_compaction,
    )
    factory = OpenClawRuntimeFactory(config)
    service = TaskService(
        audit_root=data_root / "tasks",
        coordinator_factory=factory.coordinator,
        finalizer_factory=factory.finalizer,
        max_active_tasks=args.max_active_tasks,
        max_queued_tasks=args.max_queued_tasks,
        queue_timeout_s=args.queue_timeout,
        reconcile_interrupted=True,
    )
    schedules = ScheduleService(data_root / "scheduler", service)
    static_dir = args.web_dist.expanduser().resolve()
    app = create_app(
        service,
        schedules=schedules,
        static_dir=static_dir if static_dir.is_dir() else None,
        shutdown_timeout_s=max(args.timeout, 120) + 10,
    )

    print(f"ScopeX FastAPI: http://{args.host}:{args.port}", flush=True)
    print(f"workspace: {config.workspace}", flush=True)
    print(f"data catalog (host): {workspace_catalog}", flush=True)
    print(f"data catalog (locator): {locator_catalog if locator_catalog is not None else 'not provisioned'}", flush=True)
    print(f"audit root: {data_root / 'tasks'}", flush=True)
    print(f"schedule root: {data_root / 'scheduler'}", flush=True)
    print(f"exec: host={config.exec_host} mode={config.exec_mode}", flush=True)
    print(
        "budgets: "
        f"turn_timeout={config.timeout_s}s "
        f"model_requests_per_turn={config.max_requests} "
        f"finalizer_timeout={config.finalizer_timeout_s}s",
        flush=True,
    )
    print(f"report: text-v2; active task slots={args.max_active_tasks}; queue={args.max_queued_tasks}; queue timeout={args.queue_timeout}s", flush=True)
    print("Model requests may overlap; vLLM batching capacity must be verified separately.", flush=True)
    print(f"view_image: {'enabled' if config.enable_view_image else 'disabled'}", flush=True)
    print(f"progress_card: {'enabled' if config.enable_progress_card else 'disabled'}", flush=True)
    print(f"compaction: {'enabled' if config.enable_compaction else 'disabled'}", flush=True)
    print(f"skills: {', '.join(config.skills) if config.skills else 'none'}", flush=True)
    if config.data_binds:
        print("data binds:", flush=True)
        for bind in config.data_binds:
            print(f"  {bind}", flush=True)
    else:
        print("data binds: none (catalog host paths are absent or auto-mount disabled)", flush=True)
    print(f"web: {static_dir if static_dir.is_dir() else 'not built; API-only mode'}", flush=True)

    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info", access_log=False)
    finally:
        runtime_lock.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print("RUNTIME_API_SETUP_ERROR:", str(exc), file=sys.stderr)
        raise SystemExit(2)
