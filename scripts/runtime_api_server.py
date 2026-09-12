#!/usr/bin/env python3
"""Run the local ScopeX Runtime HTTP API on loopback.

The server is intentionally single-host/single-user for the MVP. It exposes the
TaskService API only; HTTP handlers never call OpenClaw directly.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scopex.api.factory import LocalRuntimeConfig, OpenClawRuntimeFactory
from scopex.api.http import RuntimeApiServer
from scopex.api.service import TaskService


def docker_host() -> str:
    direct = os.environ.get("DOCKER_HOST", "")
    if direct and not os.environ.get("DOCKER_CONTEXT"):
        host = direct
    else:
        docker = shutil.which("docker")
        if not docker:
            raise ValueError("docker CLI is required")
        process = subprocess.run(
            [docker, "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if process.returncode != 0:
            raise ValueError("failed to inspect local Docker context")
        host = json.loads(process.stdout.strip())
    if not isinstance(host, str) or not host.startswith("unix://"):
        raise ValueError("ScopeX Runtime API requires a local Unix Docker socket")
    return host


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
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18770)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-requests", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--finalizer-max-tokens", type=int, default=768)
    parser.add_argument("--api-key-env", default="SCOPEX_API_KEY")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=ROOT / ".local" / "runtime-api",
    )
    args = parser.parse_args(argv)

    if not sys.platform.startswith("linux") or os.geteuid() == 0:
        raise ValueError("run on Spark Linux as the ordinary user, not sudo")
    if not 1 <= args.port <= 65535:
        raise ValueError("port must be between 1 and 65535")

    workspace = args.workspace.expanduser().resolve()
    cli = args.openclaw_bin.expanduser().resolve()
    data_root = args.data_root.expanduser().resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    api_key = os.environ.get(args.api_key_env, "")
    if "\n" in api_key or "\r" in api_key:
        raise ValueError("invalid API key environment value")

    runtime_factory = OpenClawRuntimeFactory(
        LocalRuntimeConfig(
            cli_path=cli,
            model_id=args.model,
            base_url=args.base_url,
            api_key=api_key,
            workspace=workspace,
            work_root=data_root / "work",
            sandbox_image=args.sandbox_image,
            docker_host=docker_host(),
            timeout_s=args.timeout,
            max_requests=args.max_requests,
            max_tokens=args.max_tokens,
            finalizer_max_tokens=args.finalizer_max_tokens,
        )
    )
    service = TaskService(
        audit_root=data_root / "tasks",
        coordinator_factory=runtime_factory.coordinator,
        finalizer_factory=runtime_factory.finalizer,
    )
    server = RuntimeApiServer((args.host, args.port), service)
    server.timeout = 0.5
    stopping = threading.Event()

    def request_stop(_signum, _frame):
        stopping.set()

    old_term = signal.signal(signal.SIGTERM, request_stop)
    old_int = signal.signal(signal.SIGINT, request_stop)
    try:
        print(
            json.dumps(
                {
                    "status": "SCOPEX_RUNTIME_API_READY",
                    "host": args.host,
                    "port": server.server_port,
                    "model": args.model,
                    "workspace": str(workspace),
                    "data_root": str(data_root),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        while not stopping.is_set():
            server.handle_request()
    finally:
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
        service.shutdown(timeout_s=10)
        server.server_close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print("RUNTIME_API_SETUP_ERROR:", str(exc), file=sys.stderr)
        sys.exit(2)
