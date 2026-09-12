from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import secrets
import threading
from typing import Callable

from scopex.agent.environment import build_openclaw_env
from scopex.agent.model_proxy import ModelProxy
from scopex.agent.openclaw import OpenClawCommandBuilder
from scopex.agent.openclaw_config import (
    ModelRequestSettings,
    OpenClawConfigSpec,
    build_openclaw_config,
)
from scopex.agent.openclaw_runner import OpenClawProcessResult, OpenClawTurnRunner
from scopex.agent.outcome import CliOutcome, parse_cli_outcome
from scopex.agent.proxy_control import RuntimeRequestHook
from scopex.agent.request_policy import OpenClawRequestPolicy
from scopex.agent.sandbox import SandboxCleanupResult, SandboxManager
from scopex.events.observer import AgentProgressObserver
from scopex.events.progress import EventSink
from scopex.runtime.stop import SafeStopGate, StopBoundary


_SESSION_KEY = re.compile(r"^agent:([^:]+):(.+)$")


@dataclass(frozen=True, slots=True)
class OpenClawTaskSpec:
    cli_path: Path
    model_id: str
    upstream_base_url: str
    upstream_api_key: str
    workspace: Path
    runtime_root: Path
    audit_root: Path
    image: str
    docker_host: str
    agent_id: str
    uid: int
    gid: int
    timeout_s: int = 300
    max_requests: int = 12
    max_tokens: int = 2048
    skills: tuple[str, ...] = ()
    sandbox_binds: tuple[str, ...] = ()
    docker_bin: str = "docker"


@dataclass(frozen=True, slots=True)
class OpenClawTurnResult:
    turn_name: str
    process: OpenClawProcessResult
    cli_outcome: CliOutcome | None
    proxy_records: tuple[dict, ...]
    audit_dir: Path


def validate_session_key_agent(session_key: str, agent_id: str) -> None:
    """Require OpenClaw's session routing identity to match the configured agent.

    OpenClaw session keys use ``agent:<agent-id>:<session-suffix>``. POC04/05
    proved same-session steering/resume only when the key's agent segment matched
    the configured ``agents.entries`` ID. A mismatch exits before the first model
    request, so fail synchronously at ScopeX setup instead of surfacing a vague
    CLI return code.
    """

    if not isinstance(agent_id, str) or not agent_id:
        raise ValueError("agent_id is required")
    match = _SESSION_KEY.fullmatch(session_key or "")
    if match is None:
        raise ValueError("session_key must use agent:<agent_id>:<suffix> format")
    routed_agent, suffix = match.groups()
    if routed_agent != agent_id:
        raise ValueError(
            f"session_key agent mismatch: routed={routed_agent!r}, configured={agent_id!r}"
        )
    if not suffix.strip():
        raise ValueError("session_key suffix is required")


class OpenClawTaskRuntime:
    """One ScopeX task's persistent OpenClaw runtime/session environment."""

    def __init__(
        self,
        *,
        task_id: str,
        session_key: str,
        spec: OpenClawTaskSpec,
        events: EventSink,
        stop_gate: SafeStopGate,
        on_safe_stop: Callable[[StopBoundary], None] | None = None,
    ) -> None:
        validate_session_key_agent(session_key, spec.agent_id)
        self.task_id = task_id
        self.session_key = session_key
        self.spec = spec
        self.events = events
        self.stop_gate = stop_gate
        self.on_safe_stop = on_safe_stop
        self.observer = AgentProgressObserver(task_id, events)
        self.spec.runtime_root.mkdir(parents=True, exist_ok=True)
        self.spec.audit_root.mkdir(parents=True, exist_ok=True)
        self.config_path = self.spec.runtime_root / "openclaw.json"

    def run_turn(self, message: str, *, turn_name: str) -> OpenClawTurnResult:
        if not turn_name or "/" in turn_name or "\\" in turn_name:
            raise ValueError("turn_name must be a simple directory name")
        audit = self.spec.audit_root / turn_name
        audit.mkdir(parents=True, exist_ok=False)
        token = secrets.token_urlsafe(24)
        policy = OpenClawRequestPolicy(self.spec.model_id, self.spec.max_tokens)
        hook = RuntimeRequestHook(
            self.observer,
            self.stop_gate,
            on_safe_stop=self.on_safe_stop,
            request_validator=policy.validate,
        )
        proxy = ModelProxy(
            audit_dir=audit,
            upstream_base_url=self.spec.upstream_base_url,
            upstream_api_key=self.spec.upstream_api_key,
            local_token=token,
            max_requests=self.spec.max_requests,
            deadline_s=self.spec.timeout_s,
            on_request=hook,
        )
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()

        try:
            config = build_openclaw_config(
                OpenClawConfigSpec(
                    model_id=self.spec.model_id,
                    proxy_base_url=proxy.base_url,
                    proxy_api_key=token,
                    workspace=self.spec.workspace,
                    audit_log=audit / "openclaw.log",
                    sandbox_root=self.spec.runtime_root / "sandboxes",
                    image=self.spec.image,
                    agent_id=self.spec.agent_id,
                    uid=self.spec.uid,
                    gid=self.spec.gid,
                    timeout_s=self.spec.timeout_s,
                    request=ModelRequestSettings(
                        max_tokens=self.spec.max_tokens,
                        temperature=0.1,
                        enable_thinking=False,
                    ),
                    skills=self.spec.skills,
                    sandbox_binds=self.spec.sandbox_binds,
                    container_prefix="scopex-",
                )
            )
            self.config_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            env = build_openclaw_env(
                runtime_root=self.spec.runtime_root,
                config_path=self.config_path,
                docker_host=self.spec.docker_host,
                base_env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
            )
            runner = OpenClawTurnRunner(
                OpenClawCommandBuilder(self.spec.cli_path),
                env=env,
                cwd=self.spec.runtime_root,
            )
            process = runner.run(
                session_key=self.session_key,
                message=message,
                timeout_s=self.spec.timeout_s,
                audit_dir=audit,
            )

            cli_outcome = None
            if process.returncode == 0 and process.stop_reason is None:
                try:
                    cli_outcome = parse_cli_outcome(
                        process.stdout_path.read_text(encoding="utf-8")
                    )
                except (ValueError, UnicodeError):
                    cli_outcome = None

            return OpenClawTurnResult(
                turn_name=turn_name,
                process=process,
                cli_outcome=cli_outcome,
                proxy_records=tuple(dict(row) for row in proxy.records),
                audit_dir=audit,
            )
        finally:
            proxy.cancel()
            proxy.shutdown()
            proxy.server_close()
            thread.join(timeout=3)

    def close(self) -> SandboxCleanupResult:
        """Remove only Docker sandboxes owned by this task's configured agent.

        Session-scoped sandboxes must survive across turns for Stop/Resume and
        Steering. Cleanup therefore happens only when the ScopeX task closes.
        """

        manager = SandboxManager(
            docker_bin=self.spec.docker_bin,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "DOCKER_HOST": self.spec.docker_host,
            },
            container_prefix=f"scopex-{self.spec.agent_id}-",
        )
        return manager.cleanup()
