#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:?usage: deploy/edge/sync.sh user@device}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

[ -f "$ROOT/frontend/dist/index.html" ] || { echo "run: npm --prefix frontend run build" >&2; exit 1; }
ssh "$TARGET" '/opt/ScalingRobotics/scopex/app/deploy/edge/update.sh --check-only'
rsync -az --delete \
  --exclude '.git/' --exclude '.local/' --exclude '.venv/' --exclude '__pycache__/' \
  --exclude 'frontend/node_modules/' --exclude '/dist/' \
  "$ROOT/" "$TARGET:/opt/ScalingRobotics/scopex/app/"
ssh "$TARGET" '/opt/ScalingRobotics/scopex/app/deploy/edge/update.sh'
