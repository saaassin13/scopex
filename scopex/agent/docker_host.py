from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Mapping


def resolve_local_docker_host(env: Mapping[str, str] | None = None) -> str:
    """Resolve the existing local Docker daemon and require a Unix socket."""

    source = dict(os.environ if env is None else env)
    direct = source.get("DOCKER_HOST", "")
    context = source.get("DOCKER_CONTEXT", "")
    if direct and not context:
        host = direct
    else:
        docker = shutil.which("docker", path=source.get("PATH"))
        if not docker:
            raise ValueError("docker CLI is required")
        process = subprocess.run(
            [
                docker,
                "context",
                "inspect",
                "--format",
                "{{json .Endpoints.docker.Host}}",
            ],
            env=source,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if process.returncode != 0:
            raise ValueError("failed to inspect Docker context")
        try:
            host = json.loads(process.stdout.strip())
        except json.JSONDecodeError as exc:
            raise ValueError("Docker context returned invalid endpoint JSON") from exc
    if not isinstance(host, str) or not host.startswith("unix://"):
        raise ValueError("ScopeX requires an existing local Unix Docker socket")
    return host
