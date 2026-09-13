# Build a lightweight ScopeX analysis sandbox on top of the already validated
# OpenClaw sandbox image. Runtime networking remains disabled by OpenClaw; this
# layer only moves a common image dependency into build time so the Agent does
# not waste model/tool rounds trying to install packages during a task.
#
# Example:
#   docker build \
#     -f docker/sandbox-analysis.Dockerfile \
#     --build-arg BASE_IMAGE=<current-sandbox-image> \
#     -t scopex-sandbox-analysis:step6f .

ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
ENV PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN python3 -m pip install "Pillow>=11,<12" \
    && python3 - <<'PY'
from PIL import Image
print("Pillow ready:", Image.__version__)
PY
