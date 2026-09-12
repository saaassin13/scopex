from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import threading
import time
from typing import Iterable

from scopex.agent.runtime import OpenClawTaskRuntime, OpenClawTaskSpec, OpenClawTurnResult
from scopex.agent.trace import load_audit_trace
from scopex.evidence.catalog import EvidenceCatalog, EvidenceItem
from scopex.evidence.collector import EvidenceCollector
from scopex.evidence.extractor import EvidenceExtractionPipeline, EvidenceExtractor
from scopex.events.progress import EventSink, EventType
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
from scopex.runtime.steering import PendingSteeringQueue
from scopex.runtime.stop import SafeStopGate, StopBoundary
from scopex.runtime.task import Task, TaskState
from scopex.storage.runtime_audit import RuntimeAudit


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
        events: EventSink,
        steering: PendingSteeringQueue | None = None,
        evidence_pipeline: EvidenceExtractionPipeline | None = None,
        audit: RuntimeAudit | None = None,
        control_lock=None,
    ) -> None:
        self.task = task
        self.session = session
        self.controller = controller
        self.agent = agent
        self.stop_gate = stop_gate
        self.catalog = catalog
        self.collector = collector
        self.convergence_policy = convergence_policy
        self.events = events
        self.steering = steering or PendingSteeringQueue()
        self.evidence_pipeline = evidence_pipeline
        self.audit = audit
        self._control_lock = control_lock or threading.RLock()
        self._turn_lock = threading.RLock()
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
        extractors: Iterable[EvidenceExtractor] = (),
        audit: RuntimeAudit | None = None,
    ) -> "InvestigationCoordinator":
        controller = TaskController(task, session, events)
        stop_gate = SafeStopGate()
        steering = PendingSteeringQueue()
        catalog = EvidenceCatalog(task.id, task.session_key)
        collector = EvidenceCollector(catalog, events)
        configured_extractors = tuple(extractors)
        evidence_pipeline = (
            EvidenceExtractionPipeline(collector, configured_extractors)
            if configured_extractors else None
        )
        control_lock = threading.RLock()

        def on_safe_stop(boundary: StopBoundary) -> None:
            with control_lock:
                if boundary.reason == "user_steer":
                    events.emit(
                        task.id,
                        EventType.STEER_BOUNDARY,
                        before_model_request=boundary.request_index,
                    )
                    return
                controller.safe_stop_if_pausing(
                    before_model_request=boundary.request_index,
                    running_tool_cancelled=False,
                )
                if audit is not None:
                    audit.snapshot_control(task, session, catalog)

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
            events=events,
            steering=steering,
            evidence_pipeline=evidence_pipeline,
            audit=audit,
            control_lock=control_lock,
        )

    def start(self, message: str, *, turn_name: str = "turn-001") -> OpenClawTurnResult:
        if self.controller.state is not TaskState.CREATED:
            raise ValueError("start requires CREATED task")
        self.session.user(message)
        self.controller.created()
        self.controller.start()
        self._started_at = time.monotonic()
        self._snapshot()
        return self._run_turn(message, turn_name=turn_name)

    def request_stop(self, message: str = "") -> None:
        """Arm the proxy gate before exposing PAUSING, without a forwarding race."""
        with self._control_lock:
            if self.controller.state is not TaskState.RUNNING:
                raise ValueError("stop requires RUNNING task")
            self.steering.clear()
            self.stop_gate.request("user_stop")
            self.controller.request_stop(message)
            self._snapshot()

    def resume(self, message: str, *, turn_name: str) -> OpenClawTurnResult:
        """Resume only after the stopped OpenClaw turn has fully unwound."""
        with self._turn_lock:
            with self._control_lock:
                if self.controller.state is not TaskState.PAUSED:
                    raise ValueError("resume requires PAUSED task")
                self.stop_gate.reset_for_next_turn()
                self.controller.resume(message)
                self._snapshot()
            return self._run_turn(message, turn_name=turn_name)

    def request_steer(self, message: str) -> None:
        """Interrupt the current Agent turn at the next safe model boundary."""
        with self._control_lock:
            if self.controller.state is not TaskState.RUNNING:
                raise ValueError("mid-turn steering requires RUNNING task")
            self.steering.push(message)
            self.stop_gate.request("user_steer")
            self.controller.steer(message)
            self._snapshot()

    def continue_pending_steering(self, *, turn_name: str) -> OpenClawTurnResult:
        """Run accumulated steering in the same session after the prior turn unwinds."""
        with self._turn_lock:
            with self._control_lock:
                if self.controller.state is not TaskState.RUNNING:
                    raise ValueError("steering continuation requires RUNNING task")
                message = self.steering.drain_message()
                if message is None:
                    raise ValueError("no pending steering instruction")
                self.stop_gate.reset_for_next_turn()
            return self._run_turn(message, turn_name=turn_name)

    def add_evidence(
        self,
        *,
        source: str,
        raw: str,
        tool_call_id: str | None = None,
        metadata: dict | None = None,
    ) -> EvidenceItem:
        item = self.collector.add(
            source=source,
            raw=raw,
            tool_call_id=tool_call_id,
            metadata=metadata,
        )
        if self.audit is not None:
            self.audit.persist_evidence(self.catalog)
        return item

    def checkpoint_evidence_progress(self) -> int:
        """Update stale-round state after configured extractors have run."""

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
        self._snapshot()
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
        if self.audit is not None:
            self.audit.persist_claims(payload)
        if not result.valid:
            self._persist_result(result, published_state=TaskState.FAILED)
            self.controller.fail("structured_finalizer_validation_failed")
            self._snapshot()
            return result

        # Publish the result before exposing FINALIZATION_COMPLETED/COMPLETED.
        # API/UI readers may treat terminal state as a guarantee that result.json
        # and final.txt are already available.
        self._persist_result(result, published_state=TaskState.COMPLETED)
        self.controller.finalization_completed()
        self.controller.complete()
        self._snapshot()
        return result

    def finish_fresh_finalization(
        self,
        finalizer: StructuredFinalizer,
    ) -> StructuredFinalizerResult:
        """Execute a fresh no-tool finalizer after FINALIZING was claimed atomically."""

        if self.controller.state is not TaskState.FINALIZING:
            raise ValueError("task must be FINALIZING")
        result = finalizer.run(user_request=self.task.user_request, catalog=self.catalog)
        if self.audit is not None and result.payload is not None:
            self.audit.persist_claims(result.payload)
        if not result.valid:
            self._persist_structured_result(result, published_state=TaskState.FAILED)
            self.controller.fail("fresh_structured_finalizer_failed")
            self._snapshot()
            return result

        # Same publication rule as the non-fresh path: result first, terminal
        # lifecycle events/state second.
        self._persist_structured_result(result, published_state=TaskState.COMPLETED)
        self.controller.finalization_completed()
        self.controller.complete()
        self._snapshot()
        return result

    def finalize_fresh(
        self,
        finalizer: StructuredFinalizer,
        *,
        goal_satisfied: bool = False,
    ) -> StructuredFinalizerResult:
        """Atomically end Investigation, then execute one fresh no-tool finalizer call."""

        self.begin_finalization(goal_satisfied=goal_satisfied)
        return self.finish_fresh_finalization(finalizer)

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
        result = self.agent.close()
        self._snapshot()
        return result

    def _run_turn(self, message: str, *, turn_name: str) -> OpenClawTurnResult:
        with self._turn_lock:
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
            if self.evidence_pipeline is not None:
                trace = load_audit_trace(result.audit_dir)
                self.evidence_pipeline.process_trace(trace)
                self.checkpoint_evidence_progress()
                if self.audit is not None:
                    self.audit.persist_evidence(self.catalog)
            self._snapshot()
            return result

    def _snapshot(self) -> None:
        if self.audit is not None:
            self.audit.snapshot_control(self.task, self.session, self.catalog)

    def _persist_result(
        self,
        result: FinalizationResult,
        *,
        published_state: TaskState,
    ) -> None:
        if self.audit is None:
            return
        self.audit.persist_result(
            {
                "valid": result.valid,
                "errors": list(result.errors),
                "task_state": published_state.value,
            },
            rendered=result.rendered,
        )

    def _persist_structured_result(
        self,
        result: StructuredFinalizerResult,
        *,
        published_state: TaskState,
    ) -> None:
        if self.audit is None:
            return
        rendered = result.finalization.rendered if result.finalization is not None else None
        errors = (
            list(result.finalization.errors)
            if result.finalization is not None else ([result.parse_error] if result.parse_error else [])
        )
        self.audit.persist_result(
            {
                "valid": result.valid,
                "errors": errors,
                "parse_error": result.parse_error,
                "finish_reasons": list(result.transport.finish_reasons),
                "task_state": published_state.value,
            },
            rendered=rendered,
        )

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
