# Cow-fall monitoring image: the analysis sandbox plus the two pieces it
# lacks for 7x24 stream monitoring - ffmpeg for the RTSP decode pipe and
# onnxruntime for the YOLO detector. Built on the already validated
# sandbox layer so cv2/numpy stay the apt versions the sandbox tests cover.
#
# pip installs against the TUNA mirror like the other offline builds.
# onnxruntime is version-pinned for reproducibility; override with
# --build-arg ONNXRUNTIME_VERSION=... The system numpy (1.26 via apt)
# already satisfies onnxruntime's requirement, so pip keeps it and the
# apt-built cv2 stays ABI-compatible - build-images.sh re-verifies the
# three imports in one process to catch any regression here.
#
# Example:
#   docker build \
#     -f docker/monitor.Dockerfile \
#     --build-arg BASE_IMAGE=scopex-sandbox-analysis:2026.09.15 \
#     -t scopex-monitor:2026.09.26 .

ARG BASE_IMAGE=scopex-sandbox-analysis:2026.09.15
FROM ${BASE_IMAGE}

ARG TUNA_PYPI=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
ARG ONNXRUNTIME_VERSION=1.20.1
USER root

RUN set -eux; \
    command -v apt-get >/dev/null || { echo 'apt-get unavailable in base image' >&2; exit 1; }; \
    . /etc/os-release; \
    case "${ID:-}" in \
      ubuntu) echo "deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ ${VERSION_CODENAME:-noble} main universe" > /etc/apt/sources.list ;; \
      debian) echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian/ ${VERSION_CODENAME:-bookworm} main" > /etc/apt/sources.list ;; \
      *) echo "unsupported apt base distribution: ${ID:-unknown}" >&2; exit 1 ;; \
    esac; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ffmpeg; \
    rm -rf /var/lib/apt/lists/*; \
    python3 -m pip install --break-system-packages \
        --index-url "$TUNA_PYPI" \
        "onnxruntime==${ONNXRUNTIME_VERSION}"; \
    python3 -c 'import cv2, numpy, onnxruntime; print("monitor toolbox OK:", numpy.__version__, onnxruntime.__version__)'; \
    ffmpeg -version | head -1
