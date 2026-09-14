# Build the ScopeX analysis sandbox on top of the already validated OpenClaw
# sandbox image. Runtime networking remains disabled by OpenClaw; dependencies
# are installed only at image build time so the Agent does not waste model/tool
# rounds probing or trying to install common analysis packages during a task.
#
# Example:
#   docker build \
#     -f docker/sandbox-analysis.Dockerfile \
#     --build-arg BASE_IMAGE=<current-sandbox-image> \
#     -t scopex-sandbox-analysis:step7 .

ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root

RUN set -eux; \
    command -v apt-get >/dev/null 2>&1 || { echo 'apt-get unavailable in base image' >&2; exit 1; }; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3-numpy \
        python3-scipy \
        python3-pandas \
        python3-opencv \
        python3-skimage \
        python3-pil \
        python3-matplotlib \
        python3-openpyxl \
        python3-yaml \
        python3-psutil; \
    if apt-cache show python3-open3d >/dev/null 2>&1; then \
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3-open3d; \
    else \
        echo 'python3-open3d unavailable in this base distribution; point-cloud work still has numpy/scipy primitives'; \
    fi; \
    rm -rf /var/lib/apt/lists/*; \
    mkdir -p /opt/scopex; \
    python3 - <<'PY'
from importlib import import_module
import json
from pathlib import Path

required = {
    "numpy": "numpy",
    "scipy": "scipy",
    "pandas": "pandas",
    "cv2": "cv2",
    "Pillow": "PIL",
    "scikit-image": "skimage",
    "matplotlib": "matplotlib",
    "openpyxl": "openpyxl",
    "PyYAML": "yaml",
    "psutil": "psutil",
}
modules = {}
for label, module_name in required.items():
    module = import_module(module_name)
    modules[label] = {
        "module": module_name,
        "available": True,
        "version": getattr(module, "__version__", None),
    }

try:
    open3d = import_module("open3d")
except ImportError:
    modules["Open3D"] = {"module": "open3d", "available": False, "version": None}
else:
    modules["Open3D"] = {
        "module": "open3d",
        "available": True,
        "version": getattr(open3d, "__version__", None),
    }

manifest = {
    "schema": 1,
    "purpose": "ScopeX preinstalled offline analysis toolbox",
    "modules": modules,
}
Path("/opt/scopex/toolbox.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(json.dumps(manifest, ensure_ascii=False))
PY
