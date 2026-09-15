#!/usr/bin/env bash
set -euo pipefail

INPUT="${1:-.}"
(cd "$INPUT" && sha256sum -c SHA256SUMS)
gzip -dc "$INPUT/runtime.tar.gz" | docker load
gzip -dc "$INPUT/sandbox.tar.gz" | docker load
