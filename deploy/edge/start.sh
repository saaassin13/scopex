#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="${SCOPEX_ROOT:-/opt/ScalingRobotics/scopex}"
CONFIG="$ROOT/config/edge.env"
COMPOSE="$ROOT/app/deploy/edge/compose.yaml"

if [ ! -f "$CONFIG" ]; then
  mkdir -p "$ROOT/config"
  cp "$ROOT/app/deploy/edge/edge.env.example" "$CONFIG"
  echo "created $CONFIG"
fi
chmod 600 "$CONFIG"

set -a
. "$CONFIG"
set +a

for command in docker ip curl; do
  command -v "$command" >/dev/null || { echo "missing command: $command" >&2; exit 1; }
done
docker compose version >/dev/null
docker info >/dev/null

SCOPEX_VPN_IP="$(ip -4 -o addr show dev "$SCOPEX_VPN_INTERFACE" scope global | awk 'NR==1 {split($4,a,"/"); print a[1]}')"
[ -n "$SCOPEX_VPN_IP" ] || { echo "no IPv4 address on $SCOPEX_VPN_INTERFACE" >&2; exit 1; }
export SCOPEX_VPN_IP
SCOPEX_UID="$(id -u)"
SCOPEX_GID="$(id -g)"
SCOPEX_DOCKER_GID="$(stat -c %g /var/run/docker.sock)"
export SCOPEX_UID SCOPEX_GID SCOPEX_DOCKER_GID

for directory in "$SCOPEX_APP_DIR" "$SCOPEX_MODEL_DIR" "$COWDISINFECT_LOG_DIR" "$LEFT_CAMERA_DIR"; do
  [ -d "$directory" ] || { echo "missing directory: $directory" >&2; exit 1; }
done
[ -r "$SCOPEX_MODEL_DIR/config.json" ] || { echo "missing model config.json" >&2; exit 1; }
[ -r "$SCOPEX_APP_DIR/frontend/dist/index.html" ] || { echo "frontend/dist is not built" >&2; exit 1; }
docker image inspect "$VLLM_IMAGE" >/dev/null
docker image inspect "$SCOPEX_RUNTIME_IMAGE" >/dev/null
docker image inspect "$SCOPEX_SANDBOX_IMAGE" >/dev/null

mkdir -p "$SCOPEX_DATA_DIR/runtime-api" "$SCOPEX_DATA_DIR/workspace" "$SCOPEX_DATA_DIR/home"
docker compose --env-file "$CONFIG" -f "$COMPOSE" config --quiet
docker compose --env-file "$CONFIG" -f "$COMPOSE" up -d

for _ in $(seq 1 90); do
  curl -fsS --max-time 2 "http://$SCOPEX_VPN_IP:8787/health" >/dev/null && {
    echo "ScopeX: http://$SCOPEX_VPN_IP:8787"
    exit 0
  }
  sleep 2
done
echo "ScopeX health check failed" >&2
docker compose --env-file "$CONFIG" -f "$COMPOSE" ps >&2
exit 1
