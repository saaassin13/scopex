from pathlib import Path
R=Path('.')
def edit(path, old,new):
 p=R/path;s=p.read_text(); assert old in s,(path,old[:80]);p.write_text(s.replace(old,new))
edit('scopex/api/factory.py','from scopex.finalizer.structured import StructuredFinalizer', 'from scopex.finalizer.structured import StructuredFinalizer\nfrom scopex.finalizer.text_report import TextReportComposer')
edit('scopex/api/factory.py','    report_max_tokens: int = 1024','    report_max_tokens: int = 2048')
edit('scopex/api/factory.py','            report_composer=self.report_composer(),','            text_reporter=TextReportComposer(\n                StreamingFinalizerClient(self.config.base_url, api_key=self.config.api_key,\n                                         timeout_s=self.config.finalizer_timeout_s),\n                model=self.config.model_id, max_tokens=self.config.report_max_tokens,\n                media_loader=EvidenceMediaLoader(self.config.data_binds),\n            ),')
edit('scopex/runtime/investigation.py','from scopex.finalizer.report import ConstrainedReportComposer','from scopex.finalizer.report import ConstrainedReportComposer\nfrom scopex.finalizer.text_report import TextReportComposer, TextReportResult')
edit('scopex/runtime/investigation.py','        report_composer: ConstrainedReportComposer | None = None,','        report_composer: ConstrainedReportComposer | None = None,\n        text_reporter: TextReportComposer | None = None,')
edit('scopex/runtime/investigation.py','        self.report_composer = report_composer','        self.report_composer = report_composer\n        self.text_reporter = text_reporter')
edit('scopex/runtime/investigation.py','            report_composer=report_composer,\n            control_lock=control_lock,','            report_composer=report_composer,\n            text_reporter=text_reporter,\n            control_lock=control_lock,')
edit('scopex/runtime/investigation.py','        result = finalizer.run(user_request=self.task.user_request, catalog=self.catalog)','        if self.text_reporter is not None:\n            return self._finish_text_report()\n        result = finalizer.run(user_request=self.task.user_request, catalog=self.catalog)')
p=R/'scopex/runtime/investigation.py';s=p.read_text(); pos=s.index('    def finalize_fresh(')
s=s[:pos]+'''    def _finish_text_report(self) -> TextReportResult:
        assert self.text_reporter is not None
        controls = f"原请求/计划时刻：{self.task.scheduled_for or self.task.created_at}\\n" + "\\n".join(
            f"{turn.kind.value}: {turn.content}" for turn in self.session.turns
            if turn.kind.value in {"STEER", "RESUME"}
        )
        result = self.text_reporter.run(
            user_request=self.task.user_request, catalog=self.catalog,
            control_context=controls, completion_reasons=self._finalization_reasons,
        )
        published = TaskState.COMPLETED if result.valid else TaskState.FAILED
        if self.audit is not None:
            # Do not ask legacy Claims/fallback rendering to translate business fields.
            self.audit.store.write_json(self.task.id, "result.json", {
                "version": 2, "valid": result.valid, "task_state": published.value,
                "execution_status": "ended_with_evidence",
                "investigation_reasons": list(self._finalization_reasons),
                "report_text": result.text, "report_meta": result.meta,
                "errors": result.meta.get("errors", []),
            })
            self.audit.store.write_text(self.task.id, "report.md", result.text)
            self.audit.store.write_text(self.task.id, "final.txt", result.text)
            self.audit.store.write_json(self.task.id, "report-meta.json", result.meta)
        if result.valid:
            self.controller.finalization_completed()
            self.controller.complete()
        else:
            self.controller.fail("text_report_" + result.status)
        self._snapshot()
        return result

'''+s[pos:]
start=s.index('    def finish_fresh_finalization(')
s=s[:start]+s[start:].replace('        text_reporter: TextReportComposer | None = None,\n','').replace(') -> StructuredFinalizerResult:',') -> StructuredFinalizerResult | TextReportResult:')
p.write_text(s)
edit('scopex/agent/runtime.py','    concise_terminal_handoff: bool = True','    concise_terminal_handoff: bool = True\n    request_time_anchor: str = ""')
edit('scopex/agent/runtime.py','        notes: list[str] = []','        notes: list[str] = []\n        if self.spec.request_time_anchor:\n            notes.append(\n                "Historical relative windows such as past 30 minutes are anchored to the "\n                f"original request/scheduled time {self.spec.request_time_anchor}, not admission "\n                "or later queue completion time. Current host-resource questions instead use "\n                "the admitted run snapshot and must report its captured_at time."\n            )')
edit('scopex/api/factory.py','            compaction_enabled=self.config.enable_compaction,','            compaction_enabled=self.config.enable_compaction,\n            request_time_anchor=task.scheduled_for or task.created_at,')
