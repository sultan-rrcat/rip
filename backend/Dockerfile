FROM python:3.11-slim

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ENV HTTP_PROXY=$HTTP_PROXY
ENV HTTPS_PROXY=$HTTPS_PROXY
ENV NO_PROXY=$NO_PROXY

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Java runtime for OpenDataLoader/Tika PDF fallback (P0.1: in-container
# ingest stalled in `processing` without a JRE; Docling primary + fallback
# both need it — see docs/CAVEATS.md). Minimal headless variant.
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-jre-headless \
    && rm -rf /var/lib/apt/lists/*
ENV JAVA_HOME=/usr/lib/jvm/default-java
RUN java -version

# CPU-only torch first: default PyPI pulls ~2-3GB nvidia-* CUDA wheels even
# though inference is BGE-M3 CPU + Ollama (host ml_env proves torch 2.14+cpu
# needs no nvidia deps). CPU index avoids the hours-long pip layer.
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

# Deps layer cached separately from code: pyproject changes rebuild deps,
# backend code changes reuse the cached deps layer.
COPY pyproject.toml README.md ./
# CPU index PRIMARY here (not extra): keeps the CPU torch from the layer
# above. With --extra-index-url pip still sees PyPI CUDA wheels and can
# reinstall torch as CUDA (~10GB image). Index order prefers CPU builds.
RUN pip install --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple .

COPY backend ./backend

WORKDIR /app/backend
RUN mkdir -p /app/uploads

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
