# syntax=docker/dockerfile:1
# RIP code-sandbox image (ADR-049): opencode CLI + python3, baked once
# by the operator.
#
#   docker compose build sandbox
#   # equivalent: docker build -t rip-sandbox sandbox/
#
# The backend stages each run (code files + generated opencode.json pinning
# the Ollama model) into a temp dir mounted at /work; the tool never bakes
# credentials or model choice into the image. No ENTRYPOINT: the tool passes
# the full `opencode run …` command per execution.
FROM node:22-slim

# Build-time proxy (ARG only, not persisted): apt/npm read env during RUN.
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY

# Python runtime for code-execution asks: node:22-slim ships no interpreter,
# so `.py` tasks failed honest (`python3` missing). Debian bookworm python3
# + pip/venv + a small baked base (pytest for "run tests" asks, numpy for
# numeric scripts, pandas for CSV analysis asks) — the box has no PyPI
# network at runtime, so anything the agent may need must be baked here
# (see ADR-049 follow-up: wheelhouse).
# The `python` symlink covers agents that try bare `python`.
# (--break-system-packages: Debian 12 pip refuses system installs without it.)
# Host-wide wheel caching: the pip/apt cache mounts persist across builds
# (BuildKit builder cache, outside the image layers), so rebuilds reuse
# downloaded wheels instead of re-fetching from PyPI.
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    && ln -s /usr/bin/python3 /usr/local/bin/python \
    && pip install --break-system-packages \
        pytest numpy pandas \
    && python3 --version

RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    npm i -g @opencode/cli@2.0.24 \
    && opencode --version

WORKDIR /work
