from __future__ import annotations

from dataclasses import dataclass
import re
import subprocess
from typing import Mapping


_CONTAINER_ID = re.compile(r"^[0-9a-fA-F]{6,64}$")


@dataclass(frozen=True, slots=True)
class SandboxCleanupResult:
    container_ids: tuple[str, ...]
    warnings: tuple[str, ...]


class SandboxManager:
    """Own cleanup only for containers carrying one ScopeX task prefix."""

    def __init__(
        self,
        *,
        docker_bin: str,
        env: Mapping[str, str],
        container_prefix: str,
    ) -> None:
        if not container_prefix or len(container_prefix) < 6:
            raise ValueError("container prefix is too broad")
        self.docker_bin = docker_bin
        self.env = dict(env)
        self.container_prefix = container_prefix

    def list_ids(self) -> tuple[str, ...]:
        process = subprocess.run(
            [
                self.docker_bin,
                "ps",
                "-aq",
                "--filter",
                f"name={self.container_prefix}",
            ],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if process.returncode != 0:
            raise RuntimeError("docker ps failed: " + process.stderr.strip()[:300])
        ids = tuple(line.strip() for line in process.stdout.splitlines() if line.strip())
        if any(not _CONTAINER_ID.fullmatch(value) for value in ids):
            raise RuntimeError("docker returned an invalid container id")
        return ids

    def cleanup(self) -> SandboxCleanupResult:
        warnings: list[str] = []
        try:
            ids = self.list_ids()
        except Exception as exc:
            return SandboxCleanupResult((), (type(exc).__name__ + ": " + str(exc)[:300],))

        for cid in ids:
            stop = subprocess.run(
                [self.docker_bin, "stop", "--time", "2", cid],
                env=self.env,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if stop.returncode != 0:
                warnings.append(f"stop {cid}: {stop.stderr.strip()[:200]}")
            remove = subprocess.run(
                [self.docker_bin, "rm", "-f", cid],
                env=self.env,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if remove.returncode != 0:
                warnings.append(f"rm {cid}: {remove.stderr.strip()[:200]}")
        return SandboxCleanupResult(ids, tuple(warnings))
