#!/usr/bin/env bash
set -euo pipefail
ROOT="${SCOPEX_ROOT:-/opt/ScalingRobotics/scopex}"
docker compose --env-file "$ROOT/config/edge.env" -f "$ROOT/app/deploy/edge/compose.yaml" stop
