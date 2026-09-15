#!/usr/bin/env bash
set -euo pipefail

OUTPUT="${1:-scopex-edge-images}"
RUNTIME_IMAGE="${SCOPEX_RUNTIME_IMAGE:-scopex-runtime:2026.09.15}"
SANDBOX_IMAGE="${SCOPEX_SANDBOX_IMAGE:-scopex-sandbox-analysis:2026.09.15}"
mkdir -p "$OUTPUT"
docker save "$RUNTIME_IMAGE" | gzip -1 > "$OUTPUT/runtime.tar.gz"
docker save "$SANDBOX_IMAGE" | gzip -1 > "$OUTPUT/sandbox.tar.gz"
sha256sum "$OUTPUT/runtime.tar.gz" "$OUTPUT/sandbox.tar.gz" > "$OUTPUT/SHA256SUMS"
