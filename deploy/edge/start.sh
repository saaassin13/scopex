#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="${SCOPEX_ROOT:-/opt/ScalingRobotics/scopex}"
CONFIG="$ROOT/config/edge.env"
RUNTIME_CONFIG="$ROOT/config/edge.runtime.env"
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
printf 'SCOPEX_VPN_IP=%s\nSCOPEX_UID=%s\nSCOPEX_GID=%s\nSCOPEX_DOCKER_GID=%s\n' \
  "$SCOPEX_VPN_IP" "$SCOPEX_UID" "$SCOPEX_GID" "$SCOPEX_DOCKER_GID" > "$RUNTIME_CONFIG"
chmod 600 "$RUNTIME_CONFIG"

for directory in "$SCOPEX_APP_DIR" "$SCOPEX_MODEL_DIR" "$COWDISINFECT_LOG_DIR" "$LEFT_CAMERA_DIR"; do
  [ -d "$directory" ] || { echo "missing directory: $directory" >&2; exit 1; }
done

# Optional cow-fall monitor (compose profile). Off by default so existing
# deployments start unchanged; see docs/deployment.md 监控服务 section.
PROFILE_ARGS=()
if [ "${SCOPEX_MONITOR_ENABLED:-0}" = "1" ]; then
  [ -n "${COW_FALL_RTSP_URL:-}" ] || { echo "SCOPEX_MONITOR_ENABLED=1 requires COW_FALL_RTSP_URL" >&2; exit 1; }
  docker image inspect "${SCOPEX_MONITOR_IMAGE:-scopex-monitor:2026.09.26}" >/dev/null
  [ -d "${SCOPEX_MONITOR_MODEL_DIR:-/opt/ScalingRobotics/scopex/models-monitor}" ] || { echo "missing directory: ${SCOPEX_MONITOR_MODEL_DIR}" >&2; exit 1; }
  # Event root must exist before compose up: the data-catalog bind that
  # lets confirmation tasks read event frames is existing_only.
  mkdir -p "$SCOPEX_DATA_DIR/monitor/cow-fall" "$SCOPEX_DATA_DIR/calibrations"
  if [ ! -f "$ROOT/config/monitor.json" ]; then
    cp "$SCOPEX_APP_DIR/deploy/edge/monitor.json.example" "$ROOT/config/monitor.json"
    chmod 600 "$ROOT/config/monitor.json"
    echo "created $ROOT/config/monitor.json (edit camera/scopex_api_url, then start again)"
  fi
  PROFILE_ARGS=(--profile monitor)
fi
[ -r "$SCOPEX_MODEL_DIR/config.json" ] || { echo "missing model config.json" >&2; exit 1; }
[ -r "$SCOPEX_APP_DIR/frontend/dist/index.html" ] || { echo "frontend/dist is not built" >&2; exit 1; }
docker image inspect "$VLLM_IMAGE" >/dev/null
docker image inspect "$SCOPEX_RUNTIME_IMAGE" >/dev/null
docker image inspect "$SCOPEX_SANDBOX_IMAGE" >/dev/null
if [ "${SCOPEX_MONITOR_ENABLED:-0}" = "1" ]; then
  docker image inspect "${SCOPEX_MONITOR_IMAGE:-scopex-monitor:2026.09.26}" >/dev/null
fi

mkdir -p "$SCOPEX_DATA_DIR/runtime-api" "$SCOPEX_DATA_DIR/workspace" "$SCOPEX_DATA_DIR/home"
docker compose --env-file "$CONFIG" -f "$COMPOSE" config --quiet
# shellcheck disable=SC2086 # PROFILE_ARGS is intentionally word-split-free
docker compose --env-file "$CONFIG" -f "$COMPOSE" ${PROFILE_ARGS[@]+"${PROFILE_ARGS[@]}"} up -d

for _ in $(seq 1 90); do
  curl -fsS --max-time 2 "http://$SCOPEX_VPN_IP:8787/health" >/dev/null 2>&1 && {
    echo "ScopeX: http://$SCOPEX_VPN_IP:8787"
    exit 0
  }
  sleep 2
done
echo "ScopeX health check failed" >&2
docker compose --env-file "$CONFIG" -f "$COMPOSE" ps >&2
exit 1
