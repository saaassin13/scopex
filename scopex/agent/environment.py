from __future__ import annotations

from pathlib import Path
import os
from typing import Mapping


def build_openclaw_env(
    *,
    runtime_root: Path,
    config_path: Path,
    docker_host: str,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Create the minimal private environment validated by POC02."""

    if not isinstance(docker_host, str) or not docker_host.startswith("unix://"):
        raise ValueError("ScopeX requires a local Unix Docker socket")
    runtime = Path(runtime_root)
    config = Path(config_path)
    home = runtime / "home"
    state = runtime / "state"
    cache = home / ".cache"
    xdg_config = home / ".config"
    for path in (runtime, home, state, cache, xdg_config):
        path.mkdir(parents=True, exist_ok=True)

    source = dict(os.environ if base_env is None else base_env)
    return {
        "PATH": source.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "NO_COLOR": "1",
        "DOCKER_HOST": docker_host,
        "OPENCLAW_HOME": str(home),
        "OPENCLAW_STATE_DIR": str(state),
        "OPENCLAW_CONFIG_PATH": str(config),
        "OPENCLAW_LOAD_SHELL_ENV": "0",
        "XDG_CONFIG_HOME": str(xdg_config),
        "XDG_CACHE_HOME": str(cache),
    }
