from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time

from scopex.agent.runtime import OpenClawTaskRuntime, OpenClawTaskSpec, OpenClawTurnResult
from scopex.evidence.catalog import EvidenceCatalog, EvidenceItem
from scopex.evidence.collector import EvidenceCollector
from scopex.events.progress import EventSink
from scopex.finalizer.service import FinalizationResult, FinalizationService
from scopex.finalizer.structured import StructuredFinalizer, StructuredFinalizerResult
from scopex.runtime.controller import TaskController
from scopex.runtime.convergence import (
    ConvergenceDecision,
    ConvergencePolicy,
    ConvergenceSnapshot,
    evaluate,
)
from scopex.runtime.session import Session
from scopex.runtime.stop import SafeStopGate, StopBoundary
from scopex.runtime.task import Task, TaskState


@dataclass(frozen=True, slots=True)
class InvestigationMetrics:
    turns: int
    forwarded_model_requests: int
    tool_calls: int
    max_context_chars: int
    stale_rounds: int
    elapsed_s: float


class InvestigationCoordinator:
    """Product coordinator around OpenClaw; contains no business investigation path."""

    def __init__(
        self,
        *,
        task: Task,
        session: Session,
        controller: TaskController,
        agent: OpenClawTaskRuntime,
        stop_gate: SafeStopGate,
        catalog: EvidenceCatalog,
        collector: EvidenceCollector,
        convergence_policy: ConvergencePolicy,
    ) -> None:
        self.task = task
        self.session = session
        self.controller = controller
        self.agent = agent
        self.stop_gate = stop_gate
        self.catalog = catalog
        self.collector = collector
        self.convergence_policy = convergence_policy
        self._started_at: float | None = None
        self._turns = 0
        self._forwarded_model_requests = 0
        self._max_context_chars = 0
        self._stale_rounds = 0
        self._last_evidence_count = 0

    @classmethod
    def for_openclaw(
        cls,
        *,
        task: Task,
        session: Session,
        spec: OpenClawTaskSpec,
        events: EventSink,
        convergence_policy: ConvergencePolicy | None = None,
    ) -> "InvestigationCoordinator":
        controller = TaskController(task, session, events)
        stop_gate = SafeStopGate()
        catalog = EvidenceCatalog(task.id, task.session_key)
        collector = EvidenceCollector(catalog, events)

        def on_safe_stop(boundary: StopBoundary) -> None:
            controller.safe_stop_if_pausing(
                before_model_request=boundary.request_index,
                running_tool_cancelled=False,
            )

        agent = OpenClawTaskRuntime(
            task_id=task.id,
            session_key=task.session_key,
            spec=spec,
            events=events,
            stop_gate=stop_gate,
            on_safe_stop=on_safe_stop,
        )
        return cls(
            task=task,
            session=session,
            controller=controller,
            agent=agent,
            stop_gate=stop_gate,
            catalog=catalog,
            collector=collector,
            convergence_policy=convergence_policy or ConvergencePolicy(),
        )

    def start(self, message: str, *, turn_name: str = "turn-001") -> OpenClawTurnResult:
        if self.controller.state is not TaskState.CREATED:
            raise ValueError("start requires CREATED task")
        self.session.user(message)
        self.controller.created()
        self.controller.start()
        self._started_at = time.monotonic()
        return self._run_turn(message, turn_name=turn_name)

    def request_stop(self, message: str = "") -> None:
        self.controller.request_stop(message)
        self.stop_gate.request("user_stop")

    def resume(self, message: str, *, turn_name: str) -> OpenClawTurnResult:
        if self.controller.state is not TaskState.PAUSED:
            raise ValueError("resume requires PAUSED task")
        self.stop_gate.reset_for_resume()
        self.controller.resume(message)
        return self._run_turn(message, turn_name=turn_name)

    def add_evidence(
        self,
        *,
        source: str,
        raw: str,
        tool_call_id: str | None = None,
        metadata: dict | None = None,
    ) -> EvidenceItem:
        return self.collector.add(
            source=source,
            raw=raw,
            tool_call_id=tool_call_id,
            metadata=metadata,
        )

    def checkpoint_evidence_progress(self) -> int:
        """Update stale-round state after the caller finishes evidence extraction.

        Investigation turns and evidence extraction are intentionally separate:
        generic runtime cannot decide which arbitrary tool-result bytes are
        meaningful domain evidence. Call this once after all extractors for the
        completed round have had a chance to add evidence.
        """

        current = len(self.catalog.items)
        if current > self._last_evidence_count:
            self._stale_rounds = 0
        else:
            self._stale_rounds += 1
        self._last_evidence_count = current
        return self._stale_rounds

    def convergence(self, *, goal_satisfied: bool = False) -> ConvergenceDecision:
        return evaluate(
            self.convergence_policy,
            ConvergenceSnapshot(
                model_requests=self._forwarded_model_requests,
                tool_calls=len(self.agent.observer.seen_call_ids),
                elapsed_s=self.metrics.elapsed_s,
                context_chars=self._max_context_chars,
                stale_rounds=self._stale_rounds,
                goal_satisfied=goal_satisfied,
            ),
        )

    def begin_finalization(self, *, goal_satisfied: bool = False) -> ConvergenceDecision:
        decision = self.convergence(goal_satisfied=goal_satisfied)
        if not decision.should_finalize:
            raise ValueError("convergence policy does not allow finalization yet")
        self.controller.begin_finalization(reasons=decision.reasons)
        return decision

    def finish_finalization(
        self,
        payload: dict,
        *,
        service: FinalizationService | None = None,
    ) -> FinalizationResult:
        if self.controller.state is not TaskState.FINALIZING:
            raise ValueError("task must be FINALIZING")
        result = (service or FinalizationService()).finalize(payload, self.catalog)
        if not result.valid:
            self.controller.fail("structured_finalizer_validation_failed")
            return result
        self.controller.finalization_completed()
        self.controller.complete()
        return result

    def finalize_fresh(
        self,
        finalizer: StructuredFinalizer,
        *,
        goal_satisfied: bool = False,
    ) -> StructuredFinalizerResult:
        """End Investigation by policy, then execute one fresh no-tool finalizer call."""

        self.begin_finalization(goal_satisfied=goal_satisfied)
        result = finalizer.run(user_request=self.task.user_request, catalog=self.catalog)
        if not result.valid:
            self.controller.fail("fresh_structured_finalizer_failed")
            return result
        self.controller.finalization_completed()
        self.controller.complete()
        return result

    @property
    def metrics(self) -> InvestigationMetrics:
        elapsed = 0.0 if self._started_at is None else time.monotonic() - self._started_at
        return InvestigationMetrics(
            turns=self._turns,
            forwarded_model_requests=self._forwarded_model_requests,
            tool_calls=len(self.agent.observer.seen_call_ids),
            max_context_chars=self._max_context_chars,
            stale_rounds=self._stale_rounds,
            elapsed_s=round(elapsed, 4),
        )

    def close(self):
        return self.agent.close()

    def _run_turn(self, message: str, *, turn_name: str) -> OpenClawTurnResult:
        if self.controller.state is not TaskState.RUNNING:
            raise ValueError("agent turn requires RUNNING task")
        result = self.agent.run_turn(message, turn_name=turn_name)
        self._turns += 1
        self._forwarded_model_requests += sum(
            1 for record in result.proxy_records if record.get("forwarded") is True
        )
        self._max_context_chars = max(
            self._max_context_chars,
            self._context_chars(result.audit_dir),
        )
        return result

    @staticmethod
    def _context_chars(audit_dir: Path) -> int:
        maximum = 0
        for path in Path(audit_dir).glob("wire-*-request.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError, UnicodeError):
                continue
            messages = payload.get("messages")
            if isinstance(messages, list):
                maximum = max(
                    maximum,
                    len(json.dumps(messages, ensure_ascii=False)),
                )
        return maximum
