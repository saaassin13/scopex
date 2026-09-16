from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path

from scopex.api.assessment_classifier import TextAssessmentClassifier
from scopex.agent.openclaw_config import TASK_SCRATCH_PATH
from scopex.agent.runtime import OpenClawTaskSpec
from scopex.evidence.media import EvidenceMediaLoader
from scopex.evidence.projector import OpenClawEvidenceProjector
from scopex.events.progress import EventSink
from scopex.finalizer.client import StreamingFinalizerClient
from scopex.finalizer.report import ConstrainedReportComposer
from scopex.finalizer.structured import StructuredFinalizer
from scopex.host_snapshot import write_current_host_snapshot
from scopex.runtime.convergence import ConvergencePolicy
from scopex.runtime.investigation import InvestigationCoordinator
from scopex.runtime.session import Session
from scopex.runtime.task import Task
from scopex.storage.runtime_audit import RuntimeAudit


HOST_SNAPSHOT_AGENT_DIR = "/scopex-host"


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
    timeout_s: int = 600
    max_requests: int = 16
    max_tokens: int = 2048
    finalizer_max_tokens: int = 768
    finalizer_timeout_s: int = 180
    report_max_tokens: int = 2048
    skills: tuple[str, ...] = ()
    data_binds: tuple[str, ...] = ()
    data_catalog_summary: str = ""
    exec_host: str = "sandbox"
    exec_mode: str = "full"
    enable_view_image: bool = False
    enable_progress_card: bool = False
    # Independent tasks do not need post-turn session maintenance. OpenClaw
    # still owns preflight/overflow recovery (verified with 2026.9.2).
    enable_compaction: bool = False


class OpenClawRuntimeFactory:
    """Create independent OpenClaw tasks; legacy report factories serve replays."""

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
        if not 256 <= config.report_max_tokens <= 2048:
            raise ValueError("report_max_tokens must be between 256 and 2048")
        if not 30 <= config.finalizer_timeout_s <= 600:
            raise ValueError("finalizer_timeout_s must be between 30 and 600")
        if len(config.data_catalog_summary) > 8192:
            raise ValueError("data_catalog_summary exceeds 8192 characters")
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

        host_root = task_root / "host"
        host_snapshot = host_root / "current.json"
        try:
            write_current_host_snapshot(host_snapshot)
        except Exception as exc:
            host_root.mkdir(parents=True, mode=0o700, exist_ok=True)
            host_snapshot.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "source": "scopex_host_snapshot",
                        "captured_at": None,
                        "errors": {"collector": type(exc).__name__ + ": " + str(exc)[:400]},
                    },
                    ensure_ascii=False,
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
        host_bind = f"{host_root.resolve()}:/scopex-host:ro"
        task_binds = tuple(self.config.data_binds) + (host_bind,)

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
            sandbox_binds=task_binds,
            task_scratch_bind=task_scratch_bind,
            data_catalog_summary=self.config.data_catalog_summary,
            exec_host=self.config.exec_host,
            exec_mode=self.config.exec_mode,
            compaction_enabled=self.config.enable_compaction,
            concise_terminal_handoff=False,
            request_time_anchor=task.scheduled_for or task.created_at,
        )
        return InvestigationCoordinator.for_openclaw(
            task=task,
            session=session,
            spec=spec,
            events=events,
            convergence_policy=ConvergencePolicy(),
            evidence_projector_factory=lambda collector: OpenClawEvidenceProjector(
                collector,
                exec_host=self.config.exec_host,
                sandbox_binds=task_binds,
                max_claim_images=2,
            ),
            audit=audit,
            native_answers=True,
        )

    def manual_assessment_classifier(self) -> TextAssessmentClassifier:
        # No request until an explicit manual API action. This never joins the
        # native completion chain and never loads Evidence/images/tools.
        return TextAssessmentClassifier(
            StreamingFinalizerClient(self.config.base_url, api_key=self.config.api_key,
                                     timeout_s=60, max_response_bytes=65536),
            model=self.config.model_id, api_key=self.config.api_key,
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

    def report_composer(self) -> ConstrainedReportComposer:
        return ConstrainedReportComposer(
            StreamingFinalizerClient(
                self.config.base_url,
                api_key=self.config.api_key,
                timeout_s=self.config.finalizer_timeout_s,
            ),
            model=self.config.model_id,
            max_tokens=self.config.report_max_tokens,
        )
