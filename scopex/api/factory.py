from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from scopex.agent.runtime import OpenClawTaskSpec
from scopex.evidence.extractor import ReadLineExtractor
from scopex.events.progress import EventSink
from scopex.finalizer.client import StreamingFinalizerClient
from scopex.finalizer.structured import StructuredFinalizer
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
    timeout_s: int = 180
    max_requests: int = 8
    max_tokens: int = 2048
    finalizer_max_tokens: int = 768
    skills: tuple[str, ...] = ()


class OpenClawRuntimeFactory:
    """Create per-task production coordinators and fresh finalizers for the API."""

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
        if not 60 <= config.timeout_s <= 600:
            raise ValueError("timeout_s must be between 60 and 600")
        if not 2 <= config.max_requests <= 12:
            raise ValueError("max_requests must be between 2 and 12")
        if not 256 <= config.finalizer_max_tokens <= 1024:
            raise ValueError("finalizer_max_tokens must be between 256 and 1024")
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
        )
        return InvestigationCoordinator.for_openclaw(
            task=task,
            session=session,
            spec=spec,
            events=events,
            extractors=(ReadLineExtractor(),),
            audit=audit,
        )

    def finalizer(self) -> StructuredFinalizer:
        return StructuredFinalizer(
            StreamingFinalizerClient(
                self.config.base_url,
                api_key=self.config.api_key,
                timeout_s=120,
            ),
            model=self.config.model_id,
            max_tokens=self.config.finalizer_max_tokens,
        )
