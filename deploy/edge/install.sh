#!/usr/bin/env bash
set -euo pipefail

ROOT="${SCOPEX_ROOT:-/opt/ScalingRobotics/scopex}"
IMAGES="${1:-$ROOT/images}"
"$ROOT/app/deploy/edge/import-images.sh" "$IMAGES"
"$ROOT/app/deploy/edge/start.sh"
