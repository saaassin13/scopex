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
from scopex.agent.model_proxy import ModelProxy, RUNTIME_LIMIT_TURN_TIMEOUT
from scopex.agent.openclaw import OpenClawCommandBuilder
from scopex.agent.openclaw_config import (
    ModelRequestSettings,
    OpenClawConfigSpec,
    TASK_SCRATCH_PATH,
    build_openclaw_config,
)
from scopex.agent.openclaw_runner import OpenClawProcessResult, OpenClawTurnRunner
from scopex.agent.outcome import CliOutcome, classify_cli_runtime_guard, parse_cli_outcome
from scopex.agent.proxy_control import RuntimeRequestHook
from scopex.agent.request_policy import OpenClawRequestPolicy
from scopex.agent.sandbox import SandboxCleanupResult, SandboxManager
from scopex.events.observer import AgentProgressObserver
from scopex.events.progress import EventSink
from scopex.model_capabilities import render_image_capacity_context
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
    # Hard model budgets are per OpenClaw turn. A user resume/steer starts a new
    # turn with a fresh turn budget while task-level counters remain audit-only.
    timeout_s: int = 600
    max_requests: int = 16
    max_tokens: int = 2048
    skills: tuple[str, ...] = ()
    tools: tuple[str, ...] = ("read", "exec", "process")
    sandbox_binds: tuple[str, ...] = ()
    task_scratch_bind: str | None = None
    data_catalog_summary: str = ""
    exec_host: str = "sandbox"
    exec_mode: str = "full"
    docker_bin: str = "docker"
    compaction_enabled: bool = True
    concise_terminal_handoff: bool = True
    request_time_anchor: str = ""


@dataclass(frozen=True, slots=True)
class OpenClawTurnResult:
    turn_name: str
    process: OpenClawProcessResult
    cli_outcome: CliOutcome | None
    proxy_records: tuple[dict, ...]
    audit_dir: Path
    runtime_limit_reason: str | None = None
    runtime_guard_reason: str | None = None


def validate_session_key_agent(session_key: str, agent_id: str) -> None:
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
        policy = OpenClawRequestPolicy(
            self.spec.model_id,
            self.spec.max_tokens,
            frozenset(self.spec.tools),
        )
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
                    tools=self.spec.tools,
                    sandbox_binds=self.spec.sandbox_binds,
                    task_scratch_bind=self.spec.task_scratch_bind,
                    exec_host=self.spec.exec_host,
                    exec_mode=self.spec.exec_mode,
                    container_prefix="scopex-",
                    compaction_enabled=self.spec.compaction_enabled,
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
                message=self._runtime_message(message),
                timeout_s=self.spec.timeout_s,
                audit_dir=audit,
            )

            cli_outcome = None
            runtime_guard_reason = None
            stdout_text = ""
            if process.stop_reason is None:
                try:
                    stdout_text = process.stdout_path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    stdout_text = ""
                if stdout_text:
                    runtime_guard_reason = classify_cli_runtime_guard(stdout_text)
                    try:
                        cli_outcome = parse_cli_outcome(stdout_text)
                    except ValueError:
                        cli_outcome = None

            runtime_limit_reason = proxy.runtime_limit_reason
            # The runner and proxy use the same turn timeout. If the outer runner
            # wins the race, preserve the same semantic stop reason.
            if runtime_limit_reason is None and process.stop_reason == "timeout":
                runtime_limit_reason = RUNTIME_LIMIT_TURN_TIMEOUT

            return OpenClawTurnResult(
                turn_name=turn_name,
                process=process,
                cli_outcome=cli_outcome,
                proxy_records=tuple(dict(row) for row in proxy.records),
                audit_dir=audit,
                runtime_limit_reason=runtime_limit_reason,
                runtime_guard_reason=runtime_guard_reason,
            )
        finally:
            proxy.cancel()
            proxy.shutdown()
            proxy.server_close()
            thread.join(timeout=3)

    def _runtime_message(self, message: str) -> str:
        """Attach product capability and scope context without owning the Agent loop."""
        notes: list[str] = []
        if self.spec.request_time_anchor:
            notes.append(
                "Historical relative windows such as past 30 minutes are anchored to the "
                f"original request/scheduled time {self.spec.request_time_anchor}, not admission "
                "or later queue completion time. Current host-resource questions instead use "
                "the admitted run snapshot and must report its captured_at time."
            )
        notes.append(
            "Treat the user's explicit target, source, and scope constraints as binding. "
            "Use the smallest sufficient evidence path for the requested outcome; do not inspect "
            "sibling files, unrelated datasets, or other subsystems merely because they are "
            "available. Expand beyond an explicitly named target only when it is necessary to "
            "answer the request or verify an allowed action, and keep that expansion minimal. "
            "Once the requested question is supported at the requested confidence, stop using "
            "tools and hand off the result instead of continuing exploratory investigation."
        )
        catalog_summary = self.spec.data_catalog_summary.strip()
        if catalog_summary:
            notes.append(
                "ScopeX data catalog (semantic locations and bounded-access rules; do not treat "
                "this as business Evidence):\n" + catalog_summary
            )
        if self.spec.task_scratch_bind is not None and self.spec.exec_host == "sandbox":
            notes.append(
                f"{TASK_SCRATCH_PATH} is writable, task-local scratch space for intermediate "
                "scripts and reduced analysis artifacts. External input mounts such as "
                "/agent-data remain read-only. For large inputs, prefer using tools to "
                "process bounded slices or summaries into task scratch instead of emitting "
                "the full raw dataset into model context."
            )
        if self.spec.exec_host == "sandbox":
            notes.append(
                "The sandbox runs with network access disabled. Do not attempt runtime package "
                "installation with pip, apt, npm, or similar network installers; use the "
                "preinstalled toolbox or standard-library fallbacks instead."
            )
        if "view_image" in self.spec.tools:
            notes.append(
                "For one or a few explicitly named images, inspect those read-only originals "
                "directly with view_image before broad directory exploration or derived metrics. "
                "Do not inspect adjacent logs, JSON, or sibling images unless the request needs "
                "cross-source correlation or the direct visual evidence is insufficient. For "
                "large image sets, keep the visual working set bounded: metadata, multi-image "
                "view_image calls, or scratch-derived previews may be used for screening. When "
                "the final conclusion depends on images, narrow to the smallest useful read-only "
                "original set. Do not re-open originals solely for evidence bookkeeping."
            )
            notes.append(render_image_capacity_context())
        if self.spec.concise_terminal_handoff:
            notes.append(
                "ScopeX independently composes the user-facing product result from observed "
                "Evidence after this OpenClaw turn. Once investigation, any allowed action, "
                "and required verification are complete, keep the terminal assistant answer "
                "brief and do not restate the full evidence trail. This affects presentation "
                "only; it does not change what you investigate, execute, verify, or when you stop."
            )
        else:
            notes.append(
                "Your final answer is delivered directly to the user. Answer the requested "
                "question in readable Chinese, with the important observations, source paths "
                "or timestamps, and material coverage limits. Distinguish measured facts, "
                "interpretation and unknowns. Give next steps only when they help answer the "
                "request; do not restate tool schemas or unrelated missing evidence. "
                "Do not invent internal Evidence IDs; use the source identifiers you observed."
            )
        if not notes:
            return message
        note = (
            "[ScopeX runtime capability]\n"
            + "\n".join(notes)
            + "\nThis is capability/evidence context, not a required investigation sequence.\n"
            "[/ScopeX runtime capability]"
        )
        return note + "\n\n" + message

    def close(self) -> SandboxCleanupResult:
        manager = SandboxManager(
            docker_bin=self.spec.docker_bin,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "DOCKER_HOST": self.spec.docker_host,
            },
            container_prefix=f"scopex-{self.spec.agent_id}-",
        )
        return manager.cleanup()
