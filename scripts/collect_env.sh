#!/usr/bin/env bash
# Read-only inventory. Does not dump environment variables, process arguments,
# Docker environment/inspect, application configs, production logs, or credentials.
set -u
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/.local/poc01/env/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$OUT"
run() {
  local title="$1"; shift
  printf '\n## %s\n' "$title"
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'UNAVAILABLE: %s\n' "$1"; return
  fi
  if command -v timeout >/dev/null 2>&1; then
    timeout 8s "$@" 2>&1 || printf 'exit=%s (保留缺失/权限/超时，不自动安装或提权)\n' "$?"
  else
    printf 'SKIPPED: 缺少 timeout，避免采集命令无限等待\n'
  fi
}
{
  printf '# ScopeX 环境记录（仅本地，可能含主机/容器名称）\nUTC: %s\n' "$(date -u +%FT%TZ)"
  run kernel uname -srm
  run os cat /etc/os-release
  run memory free -h
  run disk df -h "$ROOT"
  run cpu lscpu
  run gpu nvidia-smi
  run listeners ss -ltn
  run containers docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}'
  run python python3 --version
  run packages python3 -c 'import importlib.metadata as m
for n in ["vllm", "torch", "transformers"]:
 try: print(n, m.version(n))
 except m.PackageNotFoundError: print(n, "NOT_INSTALLED_IN_HOST_PYTHON")'
  run commit git -C "$ROOT" rev-parse HEAD
  run workspace git -C "$ROOT" status --short
} > "$OUT/environment.txt"
printf '已保存: %s\n不会自动上传。启动参数、量化、解析器、上下文长度等请按结果模板手动补齐并脱敏。\n' "$OUT/environment.txt"
