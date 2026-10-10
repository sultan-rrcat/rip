# RIP code-sandbox image (ADR-049): opencode CLI, baked once by the operator.
#
#   docker compose build sandbox
#   # equivalent: docker build -t rip-sandbox sandbox/
#
# The backend stages each run (code files + generated opencode.json pinning
# the Ollama model) into a temp dir mounted at /work; the tool never bakes
# credentials or model choice into the image. No ENTRYPOINT: the tool passes
# the full `opencode run …` command per execution.
FROM node:22-slim

RUN npm i -g @opencode/cli@2.0.24 \
    && npm cache clean --force \
    && opencode --version

WORKDIR /work
