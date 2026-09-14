from pathlib import Path
import tempfile
import unittest

from scopex.agent.runtime import OpenClawTaskRuntime, OpenClawTaskSpec
from scopex.events.progress import InMemoryEventSink
from scopex.runtime.stop import SafeStopGate


class RuntimeMessageContractTests(unittest.TestCase):
    def runtime(
        self,
        root: Path,
        *,
        exec_host="sandbox",
        concise=True,
        view_image=True,
        data_catalog_summary="",
    ):
        tools = ["read", "exec"]
        if view_image:
            tools.append("view_image")
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
            tools=tuple(tools),
            task_scratch_bind=f"{root}/scratch:/task-scratch:rw",
            data_catalog_summary=data_catalog_summary,
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

    def test_sandbox_message_exposes_scope_stop_toolbox_and_terminal_contract(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "workspace").mkdir()
            (root / "scratch").mkdir()
            message = self.runtime(root)._runtime_message("diagnose")

        self.assertIn("explicit target, source, and scope constraints as binding", message)
        self.assertIn("smallest sufficient evidence path", message)
        self.assertIn("stop using tools and hand off the result", message)
        self.assertIn("sandbox runs with network access disabled", message)
        self.assertIn("Do not attempt runtime package installation", message)
        self.assertIn("preinstalled toolbox", message)
        self.assertIn("ScopeX independently composes the user-facing product result", message)
        self.assertIn("keep the terminal assistant answer brief", message)
        self.assertIn("diagnose", message)

    def test_data_catalog_summary_is_global_semantic_context_not_business_evidence(self):
        summary = (
            "- cowdisinfect_logs: /agent-data/logs (hourly_rotated_log)\n"
            "- left_camera_multimodal: /agent-data/left-camera (hourly_multimodal)\n"
            "Do not use recursive find/grep/du over these mounted roots for ordinary time-window tasks."
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "workspace").mkdir()
            (root / "scratch").mkdir()
            message = self.runtime(root, data_catalog_summary=summary)._runtime_message("检查3点编码器")

        self.assertIn("ScopeX data catalog", message)
        self.assertIn("/agent-data/logs", message)
        self.assertIn("/agent-data/left-camera", message)
        self.assertIn("Do not use recursive find/grep/du", message)
        self.assertIn("do not treat this as business Evidence", message)

    def test_view_image_contract_prefers_named_originals_and_avoids_unrelated_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "workspace").mkdir()
            (root / "scratch").mkdir()
            message = self.runtime(root, view_image=True)._runtime_message("inspect one image")

        self.assertIn("one or a few explicitly named images", message)
        self.assertIn("inspect those read-only originals directly with view_image", message)
        self.assertIn("Do not inspect adjacent logs, JSON, or sibling images", message)
        self.assertIn("direct visual evidence is insufficient", message)

    def test_view_image_specific_guidance_is_absent_when_tool_is_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "workspace").mkdir()
            (root / "scratch").mkdir()
            message = self.runtime(root, view_image=False)._runtime_message("inspect")

        self.assertNotIn("read-only originals directly with view_image", message)
        self.assertIn("smallest sufficient evidence path", message)

    def test_gateway_exec_does_not_claim_network_is_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "workspace").mkdir()
            (root / "scratch").mkdir()
            message = self.runtime(root, exec_host="gateway")._runtime_message("inspect")

        self.assertNotIn("sandbox runs with network access disabled", message)
        self.assertIn("explicit target, source, and scope constraints as binding", message)
        self.assertIn("keep the terminal assistant answer brief", message)

    def test_terminal_handoff_can_be_disabled_for_regression_probes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "workspace").mkdir()
            (root / "scratch").mkdir()
            message = self.runtime(root, concise=False)._runtime_message("inspect")

        self.assertNotIn("ScopeX independently composes the user-facing product result", message)
        self.assertIn("smallest sufficient evidence path", message)


if __name__ == "__main__":
    unittest.main()
