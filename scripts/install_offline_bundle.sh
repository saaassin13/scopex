#!/usr/bin/env bash
set -euo pipefail

BUNDLE="${1:-}"
TARGET="${2:-$HOME/scopex}"

if [ -z "$BUNDLE" ]; then
  echo "usage: $0 <extracted-bundle-dir> [target-dir]" >&2
  exit 2
fi
BUNDLE="$(cd "$BUNDLE" && pwd)"

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "missing required command: $1" >&2; exit 1; }
}
for cmd in docker python3 tar gzip sha256sum awk; do
  need "$cmd"
done

for path in manifest.txt SHA256SUMS source/scopex-source.tar.gz images/scopex-sandbox-analysis.tar.gz; do
  [ -f "$BUNDLE/$path" ] || { echo "bundle file missing: $path" >&2; exit 1; }
done
[ -f "$BUNDLE/frontend-dist/index.html" ] || { echo "bundle frontend-dist is incomplete" >&2; exit 1; }

(
  cd "$BUNDLE"
  sha256sum -c SHA256SUMS
)

manifest_value() {
  local key="$1"
  awk -F= -v key="$key" '$1 == key {sub($1 "=", ""); print; exit}' "$BUNDLE/manifest.txt"
}

BUNDLE_ARCH="$(manifest_value architecture)"
LOCAL_ARCH="$(uname -m)"
if [ -z "$BUNDLE_ARCH" ] || [ "$BUNDLE_ARCH" != "$LOCAL_ARCH" ]; then
  echo "architecture mismatch: bundle=${BUNDLE_ARCH:-unknown} target=$LOCAL_ARCH" >&2
  exit 1
fi

if [ -e "$TARGET" ] && [ "$(find "$TARGET" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
  echo "target directory is not empty: $TARGET" >&2
  echo "use a new directory or move the previous deployment aside for rollback" >&2
  exit 1
fi
mkdir -p "$TARGET"

tar -xzf "$BUNDLE/source/scopex-source.tar.gz" -C "$TARGET"
mkdir -p "$TARGET/frontend/dist"
cp -a "$BUNDLE/frontend-dist/." "$TARGET/frontend/dist/"

if ! python3 -m venv "$TARGET/.venv"; then
  echo "failed to create venv; install the host OS python3-venv package before going offline" >&2
  exit 1
fi
"$TARGET/.venv/bin/python" -m pip install \
  --no-index \
  --find-links "$BUNDLE/wheelhouse" \
  -r "$TARGET/requirements-api.txt"

gzip -dc "$BUNDLE/images/scopex-sandbox-analysis.tar.gz" | docker load

COMMIT="$(manifest_value scopex_commit)"
IMAGE="$(manifest_value sandbox_image)"
cat <<EOF
ScopeX offline install completed.
  target:       $TARGET
  commit:       $COMMIT
  sandbox image:$IMAGE
  architecture: $LOCAL_ARCH

Before starting Runtime API, verify these external prerequisites:
  1. ~/.openclaw/bin/openclaw is the validated executable.
  2. local vLLM is running and /v1/models contains the configured model id.
  3. the business data directory exists and will be mounted read-only.

Use:
  $TARGET/.venv/bin/python $TARGET/scripts/runtime_api.py ...
EOF
