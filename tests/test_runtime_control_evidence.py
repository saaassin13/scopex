from __future__ import annotations

import unittest

from scopex.agent.trace import AgentTrace, ToolCall, ToolResult
from scopex.evidence.catalog import EvidenceCatalog
from scopex.evidence.collector import EvidenceCollector
from scopex.evidence.projector import OpenClawEvidenceProjector
from scopex.events.progress import InMemoryEventSink


class RuntimeControlEvidenceTests(unittest.TestCase):
    def projector(self):
        catalog = EvidenceCatalog("task-1", "agent:sx:task-1")
        projector = OpenClawEvidenceProjector(
            EvidenceCollector(catalog, InMemoryEventSink()),
        )
        return catalog, projector

    def test_read_loop_warning_is_control_metadata_not_evidence(self):
        catalog, projector = self.projector()
        trace = AgentTrace(
            calls=(ToolCall("r1", "read", {"path": "/agent-data/marker.txt"}),),
            results=(ToolResult(
                "r1",
                "STATIC-123\n\n"
                "[System note: Tool-loop warning after 10 repeated calls. "
                "Change your approach or stop if you are not making progress.]",
            ),),
        )

        projector.process_trace(trace)

        self.assertEqual([item.raw for item in catalog.items], ["STATIC-123"])

    def test_blocked_read_loop_result_adds_no_claim_grade_evidence(self):
        catalog, projector = self.projector()
        trace = AgentTrace(
            calls=(ToolCall("r2", "read", {"path": "/agent-data/marker.txt"}),),
            results=(ToolResult(
                "r2",
                "CRITICAL: Called read with identical outcomes 20 times. "
                "Session execution blocked to prevent runaway loops.\n\n"
                "Do not repeat this exact tool action. Reassess the task. "
                "You may answer the user, ask for clarification, or continue with a different tool or different arguments.",
            ),),
        )

        projector.process_trace(trace)

        self.assertEqual(catalog.items, ())

    def test_exec_loop_warning_is_not_misrepresented_as_command_output(self):
        catalog, projector = self.projector()
        trace = AgentTrace(
            calls=(ToolCall("e1", "exec", {"command": "printf ok"}),),
            results=(ToolResult(
                "e1",
                "ok\n"
                "[System note: Tool-loop warning after 10 repeated calls. "
                "Change your approach or stop if you are not making progress.]\n",
            ),),
        )

        projector.process_trace(trace)

        self.assertEqual([item.raw for item in catalog.items], ["ok"])


if __name__ == "__main__":
    unittest.main()
