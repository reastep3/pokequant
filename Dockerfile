# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# PokéQuant — single multi-stage Dockerfile, two deployable targets:
#   --target api   FastAPI + worker (python:slim, non-root, tini)
#   --target web   Next.js standalone (node:alpine, non-root)
# fly.toml selects the target via [build] build-target.
# Layer order is dependency-manifest first so code changes never bust the
# dependency cache; BuildKit cache mounts keep pip/npm caches across builds.
# ---------------------------------------------------------------------------
ARG PYTHON_VERSION=3.13
ARG NODE_VERSION=24

# ============================== API: build =================================
FROM python:${PYTHON_VERSION}-slim-bookworm AS api-builder
ENV PIP_NO_CACHE_DIR=0 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
COPY backend/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt \
    && find /opt/venv -name "__pycache__" -type d -prune -exec rm -rf {} + \
    && find /opt/venv -name "*.pyc" -delete

# ============================== API: runtime ===============================
FROM python:${PYTHON_VERSION}-slim-bookworm AS api
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/opt/venv/bin:$PATH" PORT=8000 APP_ENV=production
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && apt-get update \
    && apt-get install -y --no-install-recommends tini curl \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data && chown app:app /data
COPY --from=api-builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=app:app backend/alembic.ini ./
COPY --chown=app:app backend/alembic ./alembic
COPY --chown=app:app backend/scripts ./scripts
COPY --chown=app:app backend/app ./app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips=*"]

# ============================== WEB: deps ==================================
FROM node:${NODE_VERSION}-alpine AS web-deps
RUN apk add --no-cache libc6-compat
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund

# ============================== WEB: build =================================
FROM node:${NODE_VERSION}-alpine AS web-builder
WORKDIR /web
COPY --from=web-deps /web/node_modules ./node_modules
COPY frontend/ .
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} NEXT_TELEMETRY_DISABLED=1 NODE_ENV=production
RUN npm run build

# ============================== WEB: runtime ===============================
FROM node:${NODE_VERSION}-alpine AS web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN addgroup -S -g 10001 app && adduser -S -u 10001 -G app app
WORKDIR /web
COPY --from=web-builder --chown=app:app /web/public ./public
COPY --from=web-builder --chown=app:app /web/.next/standalone ./
COPY --from=web-builder --chown=app:app /web/.next/static ./.next/static
USER app
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD wget -qO- "http://127.0.0.1:${PORT}/" > /dev/null || exit 1
CMD ["node", "server.js"]
