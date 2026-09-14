#!/usr/bin/env bash
set -euo pipefail

BUNDLE="${1:-}"
TARGET_ARG="${2:-}"

if [ -z "$BUNDLE" ]; then
  echo "usage: $0 <extracted-bundle-dir> [target-dir]" >&2
  exit 2
fi
BUNDLE="$(cd "$BUNDLE" && pwd)"

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "missing required command: $1" >&2; exit 1; }
}
for cmd in docker python3 tar gzip sha256sum awk find cp uname; do
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

COMMIT="$(manifest_value scopex_commit)"
BUNDLE_ARCH="$(manifest_value architecture)"
LOCAL_ARCH="$(uname -m)"
if [ -z "$COMMIT" ]; then
  echo "bundle manifest is missing scopex_commit" >&2
  exit 1
fi
if [ -z "$BUNDLE_ARCH" ] || [ "$BUNDLE_ARCH" != "$LOCAL_ARCH" ]; then
  echo "architecture mismatch: bundle=${BUNDLE_ARCH:-unknown} target=$LOCAL_ARCH" >&2
  exit 1
fi

TARGET="${TARGET_ARG:-$HOME/scopex-releases/$COMMIT}"
if [ -e "$TARGET" ] && [ "$(find "$TARGET" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
  echo "target directory is not empty: $TARGET" >&2
  echo "use a new version directory; do not overwrite the previous release" >&2
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

IMAGE="$(manifest_value sandbox_image)"
cat <<EOF
ScopeX offline install completed.
  target:        $TARGET
  commit:        $COMMIT
  sandbox image: $IMAGE
  architecture:  $LOCAL_ARCH

The installer intentionally did NOT switch the active release.
After health/smoke validation, activate this release with:
  mkdir -p "$HOME/scopex-releases"
  ln -sfn "$TARGET" "$HOME/scopex-releases/current"
  systemctl --user restart scopex-runtime.service

Before starting Runtime API, verify these external prerequisites:
  1. ~/.openclaw/bin/openclaw is the validated executable.
  2. local vLLM is running and /v1/models contains the configured model id.
  3. the business data directory exists and will be mounted read-only.

Manual smoke command:
  $TARGET/.venv/bin/python $TARGET/scripts/runtime_api.py ...
EOF
