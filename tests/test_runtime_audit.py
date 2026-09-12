import json
from pathlib import Path
import tempfile
import unittest

from scopex.evidence.catalog import EvidenceCatalog
from scopex.events.progress import EventType, InMemoryEventSink
from scopex.runtime.session import Session
from scopex.runtime.task import Task, TaskState
from scopex.storage.audit import AuditStore
from scopex.storage.runtime_audit import AuditEventSink, RuntimeAudit


class RuntimeAuditTests(unittest.TestCase):
    def test_control_and_result_files_live_under_one_task_directory(self):
        with tempfile.TemporaryDirectory() as td:
            store = AuditStore(Path(td))
            audit = RuntimeAudit(store, "task-1")
            task = Task("task-1", "diagnose", "agent:sx:task-1")
            session = Session("task-1", "agent:sx:task-1")
            session.user("diagnose")
            task.transition(TaskState.RUNNING)
            catalog = EvidenceCatalog("task-1", "agent:sx:task-1")
            catalog.add(source="system.log", raw="worker exited status=137")

            audit.snapshot_control(task, session, catalog)
            audit.persist_claims({"claims": [], "summary_claim_ids": []})
            audit.persist_result({"status": "COMPLETED"}, rendered="final answer")

            root = Path(td) / "task-1"
            for name in (
                "task.json", "session.json", "evidence.json", "claims.json", "result.json", "final.txt"
            ):
                self.assertTrue((root / name).is_file(), name)
            self.assertEqual(json.loads((root / "task.json").read_text())["state"], "RUNNING")
            self.assertEqual((root / "final.txt").read_text(), "final answer")

    def test_audit_event_sink_preserves_downstream_event_identity(self):
        with tempfile.TemporaryDirectory() as td:
            store = AuditStore(Path(td))
            memory = InMemoryEventSink()
            sink = AuditEventSink(store, downstream=memory)
            event = sink.emit("task-1", EventType.TASK_STARTED, state="RUNNING")
            self.assertEqual(event.seq, 1)
            self.assertEqual(memory.events[0], event)
            row = json.loads((Path(td) / "task-1" / "events.jsonl").read_text())
            self.assertEqual(row["type"], "TASK_STARTED")
            self.assertEqual(row["data"]["state"], "RUNNING")


if __name__ == "__main__":
    unittest.main()
