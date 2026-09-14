from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DeploymentAssetTests(unittest.TestCase):
    def test_analysis_sandbox_uses_tuna_and_common_toolbox(self):
        text = (ROOT / "docker" / "sandbox-analysis.Dockerfile").read_text(encoding="utf-8")
        self.assertIn("https://mirrors.tuna.tsinghua.edu.cn", text)
        self.assertIn("ubuntu-ports", text)
        for package in (
            "python3-numpy",
            "python3-scipy",
            "python3-pandas",
            "python3-opencv",
            "python3-skimage",
            "python3-pil",
            "python3-openpyxl",
            "python3-sklearn",
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
        checksum = text.index("sha256sum -c SHA256SUMS")
        docker_load = text.index("docker load")
        self.assertLess(checksum, docker_load)
        self.assertIn("architecture mismatch", text)
        self.assertIn("--no-index", text)
        self.assertIn("--find-links", text)
        self.assertIn("target directory is not empty", text)

    def test_systemd_service_restarts_on_failure_and_mounts_metrics(self):
        text = (ROOT / "deploy" / "systemd" / "scopex-runtime.service").read_text(encoding="utf-8")
        self.assertIn("Restart=on-failure", text)
        self.assertIn("NoNewPrivileges=true", text)
        self.assertIn("runtime.env", text)
        self.assertIn("--system-metrics-dir", text)

    def test_system_metrics_timer_is_lightweight_and_bounded(self):
        service = (ROOT / "deploy" / "systemd" / "scopex-system-metrics.service").read_text(encoding="utf-8")
        timer = (ROOT / "deploy" / "systemd" / "scopex-system-metrics.timer").read_text(encoding="utf-8")
        self.assertIn("collect_system_metrics.py", service)
        self.assertIn("--max-file-mb 64", service)
        self.assertIn("OnUnitActiveSec=30s", timer)
        self.assertIn("Persistent=true", timer)

    def test_deployment_doc_covers_device_base_vllm_and_model_revision(self):
        text = (ROOT / "docs" / "09-zero-to-one-build-and-offline-deployment.md").read_text(encoding="utf-8")
        self.assertIn("Device Base Package", text)
        self.assertIn("vllm/vllm-openai", text)
        self.assertIn("MODEL_REPO", text)
        self.assertIn("MODEL_REVISION", text)
        self.assertIn("--served-model-name", text)
        self.assertIn("/v1/models", text)
        self.assertIn("hf download", text)


if __name__ == "__main__":
    unittest.main()
