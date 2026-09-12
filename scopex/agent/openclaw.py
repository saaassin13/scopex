from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class OpenClawCommandBuilder:
    """Build the native same-session OpenClaw CLI command used by ScopeX.

    Execution/recorder integration stays outside this small object so that the
    POC02 wire/sandbox lessons can be migrated without coupling command syntax
    to the task controller.
    """

    cli_path: Path
    thinking: str = "off"

    def build(
        self,
        *,
        session_key: str,
        message_file: Path,
        timeout_s: int,
    ) -> list[str]:
        if not session_key:
            raise ValueError("session_key is required")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        return [
            str(self.cli_path),
            "agent",
            "--local",
            "--session-key",
            session_key,
            "--thinking",
            self.thinking,
            "--timeout",
            str(timeout_s),
            "--json",
            "--message-file",
            str(message_file),
        ]
