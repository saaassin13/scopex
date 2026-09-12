from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from scopex.api.factory import LocalRuntimeConfig, OpenClawRuntimeFactory
from scopex.events.progress import InMemoryEventSink
from scopex.runtime.session import Session
from scopex.runtime.task import Task
from scopex.storage.audit import AuditStore
from scopex.storage.runtime_audit import RuntimeAudit


class RuntimeApiFactoryTests(unittest.TestCase):
    def test_convergence_budget_matches_openclaw_hard_limits(self):
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
            self.assertEqual(
                coordinator.agent.spec.sandbox_binds,
                ("/srv/logs:/agent-data/logs:ro",),
            )
            self.assertEqual(coordinator.agent.spec.exec_host, "gateway")
            self.assertEqual(coordinator.agent.spec.exec_mode, "full")
            self.assertEqual(coordinator.convergence_policy.max_elapsed_s, 181.0)
            self.assertEqual(coordinator.convergence_policy.max_model_requests, 6)


if __name__ == "__main__":
    unittest.main()
