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

# The validated base sandbox has Python but may intentionally omit pip. Prefer
# the distro package for Pillow so the runtime image does not need pip at all.
# This keeps the added toolbox small and makes the build fail clearly on a
# non-Debian/Ubuntu base instead of silently changing the runtime environment.
RUN set -eux; \
    if python3 -c 'from PIL import Image' >/dev/null 2>&1; then \
        python3 -c 'from PIL import Image; print("Pillow already available:", Image.__version__)'; \
    else \
        command -v apt-get >/dev/null 2>&1 || { echo 'Pillow missing and apt-get unavailable in base image' >&2; exit 1; }; \
        apt-get update; \
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3-pil; \
        rm -rf /var/lib/apt/lists/*; \
        python3 -c 'from PIL import Image; print("Pillow ready:", Image.__version__)'; \
    fi
