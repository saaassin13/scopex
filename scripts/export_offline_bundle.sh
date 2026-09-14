#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

IMAGE="${SCOPEX_SANDBOX_IMAGE:-scopex-sandbox-analysis:step7}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple}"
ARCH="$(uname -m)"
COMMIT="$(git rev-parse HEAD)"
SHORT_COMMIT="${COMMIT:0:12}"
OUT="${1:-$ROOT/dist/offline/scopex-offline-${SHORT_COMMIT}-${ARCH}}"
ARCHIVE="${OUT}.tar.gz"

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "missing required command: $1" >&2; exit 1; }
}
for cmd in git docker python3 tar gzip sha256sum find sort xargs; do
  need "$cmd"
done

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "refusing to export a bundle from a dirty git worktree" >&2
  exit 1
fi
if ! python3 -m pip --version >/dev/null 2>&1; then
  echo "python3 pip is required on the online bundle-build host" >&2
  exit 1
fi
if [ ! -f frontend/dist/index.html ]; then
  echo "frontend/dist is missing; run 'cd frontend && npm install && npm run build' first" >&2
  exit 1
fi
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "sandbox image not found: $IMAGE" >&2
  exit 1
fi

rm -rf "$OUT" "$ARCHIVE" "${ARCHIVE}.sha256"
mkdir -p "$OUT/source" "$OUT/frontend-dist" "$OUT/wheelhouse" "$OUT/images" "$OUT/scripts"

# Bundle committed source only. This makes the source tree match the manifest
# commit exactly and avoids accidentally shipping local audit/data files.
git archive --format=tar HEAD | gzip -9 > "$OUT/source/scopex-source.tar.gz"
cp -a frontend/dist/. "$OUT/frontend-dist/"
cp scripts/install_offline_bundle.sh "$OUT/scripts/install_offline_bundle.sh"

# Host-side FastAPI dependencies are downloaded ahead of time. Build bundles on
# the same architecture/Python family as the target Spark host.
python3 -m pip download \
  --only-binary=:all: \
  --dest "$OUT/wheelhouse" \
  --index-url "$PIP_INDEX_URL" \
  -r requirements-api.txt

# docker save includes all layers required by the selected final image.
docker save "$IMAGE" | gzip -1 > "$OUT/images/scopex-sandbox-analysis.tar.gz"

IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE")"
CREATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
PYTHON_VERSION="$(python3 --version 2>&1)"
BRANCH="$(git branch --show-current || true)"

cat > "$OUT/manifest.txt" <<EOF
bundle_schema=1
created_at_utc=$CREATED_AT
scopex_commit=$COMMIT
scopex_branch=$BRANCH
architecture=$ARCH
python=$PYTHON_VERSION
sandbox_image=$IMAGE
sandbox_image_id=$IMAGE_ID
pypi_index=$PIP_INDEX_URL
frontend_prebuilt=true
openclaw_included=false
vllm_model_included=false
EOF

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 \
    | sort -z \
    | xargs -0 sha256sum > SHA256SUMS
)

tar -C "$(dirname "$OUT")" -czf "$ARCHIVE" "$(basename "$OUT")"
sha256sum "$ARCHIVE" > "${ARCHIVE}.sha256"

cat <<EOF
Offline bundle created:
  directory: $OUT
  archive:   $ARCHIVE
  checksum:  ${ARCHIVE}.sha256

Important external prerequisites not bundled:
  - Docker Engine / compatible daemon
  - Python 3 + venv support
  - validated OpenClaw CLI installation
  - local vLLM service and model weights
EOF
