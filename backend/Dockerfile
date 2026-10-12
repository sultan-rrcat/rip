# syntax=docker/dockerfile:1
FROM python:3.11.9-slim

# Build-time proxy only (ARG, not persisted): apt/pip read the env during
# RUN. Persisting empty HTTP_PROXY/HTTPS_PROXY breaks LAN traffic
# (postgres, host.docker.internal for Ollama/Langfuse); only NO_PROXY
# defaults are baked for runtime.
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NO_PROXY=postgres,backend,host.docker.internal,127.0.0.1,localhost \
    no_proxy=postgres,backend,host.docker.internal,127.0.0.1,localhost

# NOTE: no PIP_NO_CACHE_DIR here — pip caching is enabled deliberately and
# backed by BuildKit cache mounts on every pip RUN below, so wheels persist
# across builds host-wide without bloating the image layers.

WORKDIR /app

# Java runtime for OpenDataLoader/Tika PDF fallback (P0.1: in-container
# ingest stalled in `processing` without a JRE; Docling primary + fallback
# both need it). Minimal headless variant.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    default-jre-headless
ENV JAVA_HOME=/usr/lib/jvm/default-java
RUN java -version

# Docker CLI for code.sandbox (ADR-049/050): the tool shells out to
# `docker create/cp/start/rm` against the mounted socket, so the CLI must
# exist inside this image (socket alone is not enough). Client-only copy
# from the official image: no daemon, no apt repo setup.
COPY --from=docker:28-cli /usr/local/bin/docker /usr/local/bin/docker
RUN docker --version

# CPU-only torch first: default PyPI pulls ~2-3GB nvidia-* CUDA wheels even
# though inference is BGE-M3 CPU + Ollama (host ml_env proves torch 2.14+cpu
# needs no nvidia deps). CPU index avoids the hours-long pip layer.
# Cache mount: the torch wheel persists host-wide across rebuilds.
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install torch --index-url https://download.pytorch.org/whl/cpu

# Deps layer cached separately from code: pyproject changes rebuild deps,
# backend code changes reuse the cached deps layer.
COPY pyproject.toml README.md ./
# CPU index PRIMARY here (not extra): keeps the CPU torch from the layer
# above. With --extra-index-url pip still sees PyPI CUDA wheels and can
# reinstall torch as CUDA (~10GB image). Index order prefers CPU builds.
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple .

COPY backend ./backend

WORKDIR /app/backend
RUN mkdir -p /app/uploads
# Fallback when UPLOAD_DIR is unset: keeps uploads on the /app/uploads
# volume target (compose always overrides to /app/uploads explicitly).
ENV UPLOAD_DIR=/app/uploads

# Compose pins PORT=8000 (ports mapping + probes assume it); the default
# here only covers bare `docker run` without an env file.
EXPOSE 8000

# Stdlib-only liveness (no curl in slim image); compose gates frontend on it.
# Generous start-period: lifespan blocks serving until BGE weights load.
HEALTHCHECK --interval=10s --timeout=5s --retries=8 --start-period=180s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

# Shell form so $PORT is honored (default 8000, see core/config.py + .env).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
