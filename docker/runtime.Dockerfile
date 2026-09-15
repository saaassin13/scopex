FROM node:24-bookworm-slim

ARG OPENCLAW_VERSION=2026.9.2

RUN set -eux; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates curl docker.io python3 python3-pip; \
    rm -rf /var/lib/apt/lists/*; \
    npm install --global --omit=dev "openclaw@${OPENCLAW_VERSION}"; \
    openclaw --version

COPY requirements-api.txt /tmp/requirements-api.txt
RUN python3 -m pip install --break-system-packages --no-cache-dir -r /tmp/requirements-api.txt \
    && rm /tmp/requirements-api.txt

WORKDIR /opt/ScalingRobotics/scopex/app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "scripts/runtime_api.py"]
