#!/usr/bin/env bash
set -euo pipefail
ROOT="${SCOPEX_ROOT:-/opt/ScalingRobotics/scopex}"
set -a
. "$ROOT/config/edge.env"
set +a
export SCOPEX_VPN_IP="$(ip -4 -o addr show dev "$SCOPEX_VPN_INTERFACE" scope global | awk 'NR==1 {split($4,a,"/"); print a[1]}')"
export SCOPEX_UID="$(id -u)" SCOPEX_GID="$(id -g)" SCOPEX_DOCKER_GID="$(stat -c %g /var/run/docker.sock)"
docker compose --env-file "$ROOT/config/edge.env" -f "$ROOT/app/deploy/edge/compose.yaml" stop
