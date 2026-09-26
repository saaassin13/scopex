#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_IMAGE="${SCOPEX_RUNTIME_IMAGE:-scopex-runtime:2026.09.15}"
SANDBOX_IMAGE="${SCOPEX_SANDBOX_IMAGE:-scopex-sandbox-analysis:2026.09.15}"
MONITOR_IMAGE="${SCOPEX_MONITOR_IMAGE:-scopex-monitor:2026.09.26}"
SANDBOX_SOURCE_IMAGE="${SCOPEX_SANDBOX_SOURCE_IMAGE:-scopex-sandbox-analysis:step7}"
SANDBOX_BASE_IMAGE="${SCOPEX_SANDBOX_BASE_IMAGE:-scopex-sandbox-base:step6f}"

docker build --pull=false -f "$ROOT/docker/runtime.Dockerfile" -t "$RUNTIME_IMAGE" "$ROOT"
if docker image inspect "$SANDBOX_SOURCE_IMAGE" >/dev/null 2>&1; then
  docker tag "$SANDBOX_SOURCE_IMAGE" "$SANDBOX_IMAGE"
else
  docker image inspect "$SANDBOX_BASE_IMAGE" >/dev/null
  docker build --pull=false -f "$ROOT/docker/sandbox-analysis.Dockerfile" \
    --build-arg "BASE_IMAGE=$SANDBOX_BASE_IMAGE" -t "$SANDBOX_IMAGE" "$ROOT"
fi
docker build --pull=false -f "$ROOT/docker/monitor.Dockerfile" \
  --build-arg "BASE_IMAGE=$SANDBOX_IMAGE" -t "$MONITOR_IMAGE" "$ROOT"
docker run --rm --network none --entrypoint python3 "$SANDBOX_IMAGE" \
  -c 'import cv2,PIL,numpy,pandas,scipy,skimage,matplotlib; matplotlib.use("Agg"); print("sandbox OK")'
# All three in one process: a pip numpy clobbering the apt one would break
# the apt-built cv2, and only a same-process import catches that.
docker run --rm --network none --entrypoint python3 "$MONITOR_IMAGE" \
  -c 'import cv2, numpy, onnxruntime; print("monitor imports OK")'
docker run --rm --network none --entrypoint ffmpeg "$MONITOR_IMAGE" -version | head -1
printf 'built %s\nbuilt %s\nbuilt %s\n' "$RUNTIME_IMAGE" "$SANDBOX_IMAGE" "$MONITOR_IMAGE"
