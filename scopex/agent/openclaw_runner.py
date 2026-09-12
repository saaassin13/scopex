from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Mapping

from scopex.agent.openclaw import OpenClawCommandBuilder


@dataclass(frozen=True, slots=True)
class OpenClawProcessResult:
    returncode: int | None
    stop_reason: str | None
    wall_s: float
    stdout_path: Path
    stderr_path: Path
    message_path: Path


class OpenClawTurnRunner:
    def __init__(
        self,
        builder: OpenClawCommandBuilder,
        *,
        env: Mapping[str, str],
        cwd: Path,
    ) -> None:
        self.builder = builder
        self.env = dict(env)
        self.cwd = Path(cwd)

    def run(
        self,
        *,
        session_key: str,
        message: str,
        timeout_s: int,
        audit_dir: Path,
    ) -> OpenClawProcessResult:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        audit = Path(audit_dir)
        audit.mkdir(parents=True, exist_ok=True)
        message_path = audit / "message.txt"
        stdout_path = audit / "agent.stdout.txt"
        stderr_path = audit / "agent.stderr.txt"
        message_path.write_text(message, encoding="utf-8")
        command = self.builder.build(
            session_key=session_key,
            message_file=message_path,
            timeout_s=timeout_s,
        )

        start = time.monotonic()
        returncode: int | None = None
        stop_reason: str | None = None
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            process = subprocess.Popen(
                command,
                env=self.env,
                cwd=self.cwd,
                stdout=stdout,
                stderr=stderr,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                returncode = process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                stop_reason = "timeout"
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    returncode = process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    returncode = process.wait()
            except KeyboardInterrupt:
                stop_reason = "interrupted"
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    returncode = process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    returncode = process.wait()
                raise

        return OpenClawProcessResult(
            returncode=returncode,
            stop_reason=stop_reason,
            wall_s=round(time.monotonic() - start, 4),
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            message_path=message_path,
        )
