#!/usr/bin/env python3
"""Run the loopback-only ScopeX Local Runtime API.

This is the product entrypoint for the current single-user/single-major-task MVP.
It imports only production modules under ``scopex/`` and does not depend on POC
runners or graders.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scopex.agent.docker_host import resolve_local_docker_host
from scopex.api.factory import LocalRuntimeConfig, OpenClawRuntimeFactory
from scopex.api.http import RuntimeApiServer
from scopex.api.service import TaskService


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
    if not 0 <= args.port <= 65535:
        raise ValueError("port must be between 0 and 65535")

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
    )
    factory = OpenClawRuntimeFactory(config)
    service = TaskService(
        audit_root=data_root / "tasks",
        coordinator_factory=factory.coordinator,
        finalizer_factory=factory.finalizer,
    )
    server = RuntimeApiServer((args.host, args.port), service)
    host, port = server.server_address[:2]
    print(f"ScopeX Runtime API: http://{host}:{port}", flush=True)
    print(f"workspace: {config.workspace}", flush=True)
    print(f"audit root: {data_root / 'tasks'}", flush=True)

    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nScopeX Runtime API stopping...", flush=True)
    finally:
        server.server_close()
        service.shutdown(timeout_s=max(args.timeout, 120) + 10)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print("RUNTIME_API_SETUP_ERROR:", str(exc), file=sys.stderr)
        raise SystemExit(2)
