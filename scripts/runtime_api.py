#!/usr/bin/env python3
"""Run the loopback-only ScopeX FastAPI product server."""
from __future__ import annotations

import argparse
import ipaddress
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn

from scopex.agent.docker_host import resolve_local_docker_host
from scopex.api.factory import LocalRuntimeConfig, OpenClawRuntimeFactory
from scopex.api.fastapi_app import create_app
from scopex.api.service import TaskService


def loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_data_dir(value: str) -> str:
    """Parse HOST_DIR:AGENT_DIR into an OpenClaw read-only bind string."""

    try:
        host_text, agent_dir = value.rsplit(":", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--data-dir must use HOST_DIR:AGENT_DIR"
        ) from exc
    host_dir = Path(host_text).expanduser().resolve()
    if not host_dir.is_dir():
        raise argparse.ArgumentTypeError(f"data directory does not exist: {host_dir}")
    if not agent_dir.startswith("/") or agent_dir == "/" or ":" in agent_dir:
        raise argparse.ArgumentTypeError(
            "agent data directory must be an absolute non-root path"
        )
    return f"{host_dir}:{agent_dir}:ro"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--sandbox-image", required=True)
    parser.add_argument(
        "--openclaw-bin",
        type=Path,
        default=Path.home() / ".openclaw/bin/openclaw",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=ROOT / ".local" / "runtime-api",
    )
    parser.add_argument(
        "--data-dir",
        action="append",
        type=parse_data_dir,
        default=[],
        metavar="HOST_DIR:AGENT_DIR",
        help="read-only host directory exposed to the OpenClaw sandbox; repeatable",
    )
    parser.add_argument(
        "--web-dist",
        type=Path,
        default=ROOT / "frontend" / "dist",
        help="Vue build directory; ignored until it exists",
    )
    parser.add_argument("--api-key-env", default="SCOPEX_API_KEY")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-requests", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--finalizer-max-tokens", type=int, default=768)
    parser.add_argument("--skill", action="append", default=[])
    args = parser.parse_args(argv)

    if not sys.platform.startswith("linux") or os.geteuid() == 0:
        raise ValueError("run Runtime API on Spark Linux as the ordinary user, not sudo")
    if not loopback_host(args.host):
        raise ValueError("Runtime API may bind only to loopback")
    if not 1 <= args.port <= 65535:
        raise ValueError("port must be between 1 and 65535")

    os.umask(0o077)
    api_key = os.environ.get(args.api_key_env, "")
    if "\n" in api_key or "\r" in api_key:
        raise ValueError("invalid API key environment value")

    data_root = args.data_root.expanduser().resolve()
    config = LocalRuntimeConfig(
        cli_path=args.openclaw_bin.expanduser().resolve(),
        model_id=args.model,
        base_url=args.base_url,
        api_key=api_key,
        workspace=args.workspace.expanduser().resolve(),
        work_root=data_root / "work",
        sandbox_image=args.sandbox_image,
        docker_host=resolve_local_docker_host(),
        timeout_s=args.timeout,
        max_requests=args.max_requests,
        max_tokens=args.max_tokens,
        finalizer_max_tokens=args.finalizer_max_tokens,
        skills=tuple(args.skill),
        data_binds=tuple(args.data_dir),
    )
    factory = OpenClawRuntimeFactory(config)
    service = TaskService(
        audit_root=data_root / "tasks",
        coordinator_factory=factory.coordinator,
        finalizer_factory=factory.finalizer,
    )
    static_dir = args.web_dist.expanduser().resolve()
    app = create_app(
        service,
        static_dir=static_dir if static_dir.is_dir() else None,
        shutdown_timeout_s=max(args.timeout, 120) + 10,
    )

    print(f"ScopeX FastAPI: http://{args.host}:{args.port}", flush=True)
    print(f"workspace: {config.workspace}", flush=True)
    print(f"audit root: {data_root / 'tasks'}", flush=True)
    if config.data_binds:
        print("data binds:", flush=True)
        for bind in config.data_binds:
            print(f"  {bind}", flush=True)
    print(
        f"web: {static_dir if static_dir.is_dir() else 'not built; API-only mode'}",
        flush=True,
    )

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print("RUNTIME_API_SETUP_ERROR:", str(exc), file=sys.stderr)
        raise SystemExit(2)
