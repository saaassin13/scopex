from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from scopex.agent.openclaw_config import TASK_SCRATCH_PATH
from scopex.agent.runtime import OpenClawTaskSpec
from scopex.evidence.media import EvidenceMediaLoader
from scopex.evidence.projector import OpenClawEvidenceProjector
from scopex.events.progress import EventSink
from scopex.finalizer.answer import ConstrainedAnswerComposer
from scopex.finalizer.client import StreamingFinalizerClient
from scopex.finalizer.structured import StructuredFinalizer
from scopex.runtime.convergence import ConvergencePolicy
from scopex.runtime.investigation import InvestigationCoordinator
from scopex.runtime.session import Session
from scopex.runtime.task import Task
from scopex.storage.runtime_audit import RuntimeAudit


@dataclass(frozen=True, slots=True)
class LocalRuntimeConfig:
    cli_path: Path
    model_id: str
    base_url: str
    api_key: str
    workspace: Path
    work_root: Path
    sandbox_image: str
    docker_host: str
    # Per-OpenClaw-turn hard budgets. Step 6B's 48-image probe took ~439 s
    # and 11 forwarded model requests, so the previous 180 s / 8 request POC
    # defaults would reject a task that we have now proven useful and bounded.
    timeout_s: int = 600
    max_requests: int = 16
    max_tokens: int = 2048
    finalizer_max_tokens: int = 768
    finalizer_timeout_s: int = 180
    answer_composer_max_tokens: int = 256
    answer_composer_timeout_s: int = 60
    skills: tuple[str, ...] = ()
    data_binds: tuple[str, ...] = ()
    exec_host: str = "sandbox"
    exec_mode: str = "full"
    enable_view_image: bool = False
    enable_progress_card: bool = False
    enable_compaction: bool = True


class _UnavailableAnswerComposer:
    """Defer factory setup failure into the non-fatal composer audit boundary."""

    def __init__(self, error_type: str) -> None:
        self.error_type = error_type

    def run(self, **_kwargs):
        raise RuntimeError(f"answer_composer_factory_error:{self.error_type}")


class OpenClawRuntimeFactory:
    """Create per-task production coordinators and trusted output stages for the API."""

    def __init__(self, config: LocalRuntimeConfig) -> None:
        self.config = config
        if not config.model_id or not config.sandbox_image:
            raise ValueError("model_id and sandbox_image are required")
        if not config.cli_path.is_file() or not os.access(config.cli_path, os.X_OK):
            raise ValueError("OpenClaw CLI is not executable")
        if not config.workspace.is_dir():
            raise ValueError("workspace must be an existing directory")
        if config.workspace.is_symlink():
            raise ValueError("workspace symlink is not allowed")
        if not config.docker_host.startswith("unix://"):
            raise ValueError("local Runtime API requires a Unix Docker socket")
        if not 60 <= config.timeout_s <= 1200:
            raise ValueError("timeout_s must be between 60 and 1200")
        if not 2 <= config.max_requests <= 30:
            raise ValueError("max_requests must be between 2 and 30")
        if not 256 <= config.finalizer_max_tokens <= 1024:
            raise ValueError("finalizer_max_tokens must be between 256 and 1024")
        if not 30 <= config.finalizer_timeout_s <= 600:
            raise ValueError("finalizer_timeout_s must be between 30 and 600")
        if not 128 <= config.answer_composer_max_tokens <= 512:
            raise ValueError("answer_composer_max_tokens must be between 128 and 512")
        if not 10 <= config.answer_composer_timeout_s <= 180:
            raise ValueError("answer_composer_timeout_s must be between 10 and 180")
        config.work_root.mkdir(parents=True, exist_ok=True)

    def coordinator(
        self,
        task: Task,
        session: Session,
        events: EventSink,
        audit: RuntimeAudit,
    ) -> InvestigationCoordinator:
        agent_id = task.metadata.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("task metadata is missing agent_id")
        task_root = self.config.work_root / task.id
        scratch_root = task_root / "scratch"
        scratch_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        if scratch_root.is_symlink():
            raise ValueError("task scratch symlink is not allowed")
        resolved_work_root = self.config.work_root.resolve()
        resolved_scratch = scratch_root.resolve()
        try:
            resolved_scratch.relative_to(resolved_work_root)
        except ValueError as exc:
            raise ValueError("task scratch escaped work_root") from exc
        task_scratch_bind = f"{resolved_scratch}:{TASK_SCRATCH_PATH}:rw"

        tools = ["read", "exec", "process"]
        if self.config.enable_view_image:
            tools.append("view_image")
        if self.config.enable_progress_card:
            tools.append("progress_card")
        spec = OpenClawTaskSpec(
            cli_path=self.config.cli_path,
            model_id=self.config.model_id,
            upstream_base_url=self.config.base_url,
            upstream_api_key=self.config.api_key,
            workspace=self.config.workspace,
            runtime_root=task_root / "runtime",
            audit_root=task_root / "agent-turns",
            image=self.config.sandbox_image,
            docker_host=self.config.docker_host,
            agent_id=agent_id,
            uid=os.getuid(),
            gid=os.getgid(),
            timeout_s=self.config.timeout_s,
            max_requests=self.config.max_requests,
            max_tokens=self.config.max_tokens,
            skills=self.config.skills,
            tools=tuple(tools),
            sandbox_binds=self.config.data_binds,
            task_scratch_bind=task_scratch_bind,
            exec_host=self.config.exec_host,
            exec_mode=self.config.exec_mode,
            compaction_enabled=self.config.enable_compaction,
        )
        return InvestigationCoordinator.for_openclaw(
            task=task,
            session=session,
            spec=spec,
            events=events,
            # Hard request/time/context budgets belong to OpenClaw/ModelProxy.
            # ScopeX convergence remains product-level only.
            convergence_policy=ConvergencePolicy(),
            evidence_projector_factory=lambda collector: OpenClawEvidenceProjector(
                collector,
                exec_host=self.config.exec_host,
                sandbox_binds=self.config.data_binds,
            ),
            audit=audit,
        )

    def finalizer(self) -> StructuredFinalizer:
        return StructuredFinalizer(
            StreamingFinalizerClient(
                self.config.base_url,
                api_key=self.config.api_key,
                timeout_s=self.config.finalizer_timeout_s,
            ),
            model=self.config.model_id,
            max_tokens=self.config.finalizer_max_tokens,
            media_loader=EvidenceMediaLoader(self.config.data_binds),
        )

    def answer_composer(self) -> ConstrainedAnswerComposer | _UnavailableAnswerComposer:
        try:
            return ConstrainedAnswerComposer(
                StreamingFinalizerClient(
                    self.config.base_url,
                    api_key=self.config.api_key,
                    timeout_s=self.config.answer_composer_timeout_s,
                ),
                model=self.config.model_id,
                max_tokens=self.config.answer_composer_max_tokens,
            )
        except Exception as exc:
            # A valid Fresh Finalizer result must never be lost because the
            # optional product-presentation stage could not be constructed.
            return _UnavailableAnswerComposer(type(exc).__name__)
