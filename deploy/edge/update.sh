#!/usr/bin/env bash
set -euo pipefail
ROOT="${SCOPEX_ROOT:-/opt/ScalingRobotics/scopex}"
MODE="${1:-}"
CONFIG="$ROOT/config/edge.env"
COMPOSE="$ROOT/app/deploy/edge/compose.yaml"

set -a
. "$CONFIG"
set +a
VPN_IP="$(ip -4 -o addr show dev "$SCOPEX_VPN_INTERFACE" scope global | awk 'NR==1 {split($4,a,"/"); print a[1]}')"
[ -n "$VPN_IP" ] || { echo "no IPv4 address on $SCOPEX_VPN_INTERFACE" >&2; exit 1; }
[ -r "$SCOPEX_APP_DIR/frontend/dist/index.html" ] || { echo "frontend/dist is not built" >&2; exit 1; }

ACTIVITY="$(curl -fsS --max-time 5 "http://$VPN_IP:8787/activity" || true)"
if [ -n "$ACTIVITY" ] && ! python3 -c 'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if not d.get("tasks") else 1)' <<<"$ACTIVITY"; then
  echo "ScopeX has active or queued tasks; update stopped" >&2
  exit 1
fi
[ "$MODE" = "--check-only" ] && exit 0

export SCOPEX_VPN_IP="$VPN_IP" SCOPEX_UID="$(id -u)" SCOPEX_GID="$(id -g)" SCOPEX_DOCKER_GID="$(stat -c %g /var/run/docker.sock)"
docker compose --env-file "$CONFIG" -f "$COMPOSE" up -d --no-deps scopex
for _ in $(seq 1 30); do
  curl -fsS --max-time 2 "http://$VPN_IP:8787/health" >/dev/null && { echo "ScopeX updated: http://$VPN_IP:8787"; exit 0; }
  sleep 2
done
echo "ScopeX update health check failed" >&2
exit 1
