FROM node:24-bookworm-slim

ARG OPENCLAW_VERSION=2026.9.2
ARG APT_MIRROR=http://mirrors.tuna.tsinghua.edu.cn
ARG NPM_REGISTRY=https://registry.npmmirror.com
ARG PIP_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple

RUN set -eux; \
    for file in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
      [ -f "$file" ] || continue; \
      sed -i -E \
        -e "s#https?://(deb\.debian\.org|security\.debian\.org)/debian-security#${APT_MIRROR}/debian-security#g" \
        -e "s#https?://deb\.debian\.org/debian#${APT_MIRROR}/debian#g" \
        "$file"; \
    done; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates curl python3 python3-pip; \
    rm -rf /var/lib/apt/lists/*; \
    npm install --global --omit=dev --registry="$NPM_REGISTRY" "openclaw@${OPENCLAW_VERSION}"; \
    openclaw --version

COPY requirements-api.txt /tmp/requirements-api.txt
RUN python3 -m pip install --break-system-packages --no-cache-dir \
      --index-url "$PIP_INDEX_URL" -r /tmp/requirements-api.txt \
    && rm /tmp/requirements-api.txt

WORKDIR /opt/ScalingRobotics/scopex/app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "scripts/runtime_api.py"]
