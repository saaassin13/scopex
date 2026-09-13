from pathlib import Path
import tempfile
import unittest

from scopex.agent.runtime import OpenClawTaskRuntime, OpenClawTaskSpec
from scopex.events.progress import InMemoryEventSink
from scopex.runtime.stop import SafeStopGate


class RuntimeMessageContractTests(unittest.TestCase):
    def runtime(self, root: Path, *, exec_host="sandbox", concise=True):
        spec = OpenClawTaskSpec(
            cli_path=root / "openclaw",
            model_id="local-model",
            upstream_base_url="http://127.0.0.1:18000/v1",
            upstream_api_key="",
            workspace=root / "workspace",
            runtime_root=root / "runtime",
            audit_root=root / "audit",
            image="scopex-test:latest",
            docker_host="unix:///tmp/docker.sock",
            agent_id="sx1",
            uid=1000,
            gid=1000,
            tools=("read", "exec", "view_image"),
            task_scratch_bind=f"{root}/scratch:/task-scratch:rw",
            exec_host=exec_host,
            concise_terminal_handoff=concise,
        )
        return OpenClawTaskRuntime(
            task_id="task-1",
            session_key="agent:sx1:task-1",
            spec=spec,
            events=InMemoryEventSink(),
            stop_gate=SafeStopGate(),
        )

    def test_sandbox_message_exposes_no_network_and_concise_terminal_contract(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "workspace").mkdir()
            (root / "scratch").mkdir()
            message = self.runtime(root)._runtime_message("diagnose")

        self.assertIn("sandbox runs with network access disabled", message)
        self.assertIn("Do not attempt runtime package installation", message)
        self.assertIn("ScopeX independently composes the user-facing product result", message)
        self.assertIn("keep the terminal assistant answer brief", message)
        self.assertIn("diagnose", message)

    def test_gateway_exec_does_not_claim_network_is_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "workspace").mkdir()
            (root / "scratch").mkdir()
            message = self.runtime(root, exec_host="gateway")._runtime_message("inspect")

        self.assertNotIn("sandbox runs with network access disabled", message)
        self.assertIn("keep the terminal assistant answer brief", message)

    def test_terminal_handoff_can_be_disabled_for_regression_probes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "workspace").mkdir()
            (root / "scratch").mkdir()
            message = self.runtime(root, concise=False)._runtime_message("inspect")

        self.assertNotIn("ScopeX independently composes the user-facing product result", message)


if __name__ == "__main__":
    unittest.main()
