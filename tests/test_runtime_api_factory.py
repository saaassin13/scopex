from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scopex.api.factory import LocalRuntimeConfig, OpenClawRuntimeFactory
from scopex.evidence.projector import OpenClawEvidenceProjector
from scopex.events.progress import InMemoryEventSink
from scopex.runtime.session import Session
from scopex.runtime.task import Task
from scopex.storage.audit import AuditStore
from scopex.storage.runtime_audit import RuntimeAudit


class RuntimeApiFactoryTests(unittest.TestCase):
    def test_runtime_options_propagate_to_openclaw_spec(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cli = root / "openclaw"
            cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            cli.chmod(0o755)
            workspace = root / "workspace"
            workspace.mkdir()

            config = LocalRuntimeConfig(
                cli_path=cli,
                model_id="local-model",
                base_url="http://127.0.0.1:18002/v1",
                api_key="",
                workspace=workspace,
                work_root=root / "work",
                sandbox_image="scopex-test:latest",
                docker_host="unix:///var/run/docker.sock",
                timeout_s=181,
                max_requests=6,
                data_binds=("/srv/logs:/agent-data/logs:ro",),
                exec_host="gateway",
                exec_mode="full",
                enable_view_image=True,
                enable_progress_card=True,
                enable_compaction=True,
            )
            factory = OpenClawRuntimeFactory(config)

            task = Task(
                "task-1",
                "diagnose",
                "agent:sxapi1:task-1",
                metadata={"agent_id": "sxapi1"},
            )
            session = Session(task.id, task.session_key)
            store = AuditStore(root / "audit")
            coordinator = factory.coordinator(
                task,
                session,
                InMemoryEventSink(),
                RuntimeAudit(store, task.id),
            )

            self.assertEqual(coordinator.agent.spec.timeout_s, 181)
            self.assertEqual(coordinator.agent.spec.max_requests, 6)
            self.assertTrue(coordinator.agent.spec.compaction_enabled)

            # Configured business-data binds are preserved and ScopeX adds one
            # task-local, read-only current-host snapshot capability. The latter
            # is intentional product plumbing for system-health, not an
            # unexpected expansion of external business-data access.
            binds = coordinator.agent.spec.sandbox_binds
            self.assertEqual(binds[0], "/srv/logs:/agent-data/logs:ro")
            self.assertEqual(len(binds), 2)
            expected_host = (root / "work" / "task-1" / "host").resolve()
            self.assertEqual(binds[1], f"{expected_host}:/scopex-host:ro")
            host_snapshot = expected_host / "current.json"
            self.assertTrue(host_snapshot.is_file())
            snapshot = json.loads(host_snapshot.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["source"], "scopex_host_snapshot")

            expected_scratch = (root / "work" / "task-1" / "scratch").resolve()
            self.assertTrue(expected_scratch.is_dir())
            self.assertEqual(
                coordinator.agent.spec.task_scratch_bind,
                f"{expected_scratch}:/task-scratch:rw",
            )
            self.assertEqual(coordinator.agent.spec.exec_host, "gateway")
            self.assertEqual(coordinator.agent.spec.exec_mode, "full")
            self.assertEqual(
                coordinator.agent.spec.tools,
                ("read", "exec", "process", "view_image", "progress_card"),
            )
            self.assertIsInstance(
                coordinator.evidence_pipeline,
                OpenClawEvidenceProjector,
            )
            # Hard request/time budgets are propagated to OpenClaw/ModelProxy;
            # ScopeX convergence no longer duplicates them.
            self.assertEqual(coordinator.convergence_policy.max_stale_rounds, 3)


if __name__ == "__main__":
    unittest.main()
