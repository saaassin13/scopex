from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DeploymentAssetTests(unittest.TestCase):
    def test_analysis_sandbox_uses_tuna_and_common_toolbox(self):
        text = (ROOT / "docker" / "sandbox-analysis.Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ARG BASE_IMAGE=scopex-sandbox-base:step6f", text)
        self.assertIn("FROM ${BASE_IMAGE}", text)
        self.assertIn("https://mirrors.tuna.tsinghua.edu.cn", text)
        self.assertIn("ubuntu-ports", text)
        for package in (
            "python3-numpy", "python3-scipy", "python3-pandas", "python3-opencv",
            "python3-skimage", "python3-pil", "python3-openpyxl", "python3-sklearn",
        ):
            self.assertIn(package, text)
        self.assertIn("/opt/scopex/toolbox.json", text)

    def test_offline_export_has_traceability_and_no_runtime_package_install(self):
        text = (ROOT / "scripts" / "export_offline_bundle.sh").read_text(encoding="utf-8")
        self.assertIn("git archive", text)
        self.assertIn("--only-binary=:all:", text)
        self.assertIn("mirrors.tuna.tsinghua.edu.cn/pypi/web/simple", text)
        self.assertIn("docker save", text)
        self.assertIn("SHA256SUMS", text)
        self.assertIn("architecture=$ARCH", text)
        self.assertIn("scopex_commit=$COMMIT", text)

    def test_offline_install_verifies_before_loading(self):
        text = (ROOT / "scripts" / "install_offline_bundle.sh").read_text(encoding="utf-8")
        self.assertLess(text.index("sha256sum -c SHA256SUMS"), text.index("docker load"))
        self.assertIn("architecture mismatch", text)
        self.assertIn("--no-index", text)
        self.assertIn("--find-links", text)
        self.assertIn("target directory is not empty", text)

    def test_systemd_service_restarts_on_failure_without_history_collector(self):
        text = (ROOT / "deploy" / "systemd" / "scopex-runtime.service").read_text(encoding="utf-8")
        self.assertIn("Restart=on-failure", text)
        self.assertIn("NoNewPrivileges=true", text)
        self.assertIn("runtime.env", text)
        self.assertIn("--workspace %h/.local/share/scopex/workspace", text)
        self.assertIn("--data-root %h/.local/share/scopex/runtime-api", text)
        self.assertNotIn("current/.local", text)
        self.assertNotIn("--data-dir", text)
        self.assertNotIn("--system-metrics-dir", text)
        env = (ROOT / "deploy" / "systemd" / "runtime.env.example").read_text(encoding="utf-8")
        self.assertIn("SCOPEX_MAX_IMAGES_PER_PROMPT=4", env)
        self.assertNotIn("SCOPEX_DATA_DIR=", env)
        self.assertFalse((ROOT / "deploy" / "systemd" / "scopex-system-metrics.timer").exists())
        self.assertFalse((ROOT / "deploy" / "systemd" / "scopex-system-metrics.service").exists())

    def test_runtime_uses_per_task_current_host_snapshot(self):
        factory = (ROOT / "scopex" / "api" / "factory.py").read_text(encoding="utf-8")
        skill = (ROOT / "skills" / "system-health" / "SKILL.md").read_text(encoding="utf-8")
        collector = (ROOT / "scripts" / "collect_system_metrics.py").read_text(encoding="utf-8")
        self.assertIn("write_current_host_snapshot", factory)
        self.assertIn(":/scopex-host:ro", factory)
        self.assertIn("/scopex-host/current.json", skill)
        self.assertIn("Do not fall back to sandbox-local measurements", skill)
        self.assertNotIn("--output", collector)
        self.assertNotIn("system_metrics.jsonl", collector)

    def test_deployment_doc_covers_device_base_vllm_and_model_revision(self):
        text = (ROOT / "docs" / "09-zero-to-one-build-and-offline-deployment.md").read_text(encoding="utf-8")
        for token in (
            "Device Base Package", "nvcr.io/nvidia/vllm:26.08-py3", "MODEL_REPO",
            "MODEL_REVISION", "f0b7c9e722f5565102fff8481c99e4d86ae099c7",
            "--served-model-name", "/v1/models", "不部署系统资源 timer",
            "/scopex-host/current.json", "SCOPEX_MAX_IMAGES_PER_PROMPT", "--data-root",
        ):
            self.assertIn(token, text)
        # The documented venv executable is quoted; require the actual subcommand,
        # not one spelling that would reject a safe absolute executable path.
        self.assertRegex(text, r"\bhf[\"']?\s+download\b")


if __name__ == "__main__":
    unittest.main()
