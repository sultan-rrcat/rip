# Containerization / Deployment Fix Plan

Decisions (2026-09-14): docs/AGENT.md for host-env docs; Q3-A portable
`extra_hosts`; Q4-A same-origin frontend only.

## Host env (probed)

- `C:\Users\offic\venvs\ml_env` EXISTS — Python 3.12.0, torch 2.14.0+cpu.
- `agent_env` NOT FOUND (`..\ENV\agent_env`, `C:\Users\offic\ENV\agent_env`,
  `C:\Users\offic\projects\rip-athena\ENV` all absent; `C:\Users\offic\venvs`
  contains only `ml_env`). `agent_env` is legacy (see
  `docs/archive/SESSION_LOG.md:130`); canonical host env is `ml_env`.
- Docker 28.5.2, Compose v2.40.3-desktop.1. `docker compose config` passes.

## Issues

1. `docker-compose.yml:1-5` stale migration header (file already TARGET).
2. No `backend` healthcheck; `frontend depends_on backend` ungated.
3. `backend/Dockerfile:45` hardcodes `--port 8000`, ignores `PORT`.
4. Proxy `ARG -> ENV` leak (`Dockerfile:3-8`); no `NO_PROXY` for
   `postgres,backend,host.docker.internal`.
5. `host.docker.internal` without `extra_hosts` breaks Linux.
6. `env_file: ./.env` hard-required; missing `.env` breaks `config`.
7. `frontend/nginx.conf` ineffective `Cache-Control`/`X-Accel-Buffering`
   request headers; missing timeouts, `proxy_cache off`, `/healthz`,
   static-cache split.
8. No `frontend/.dockerignore`; `COPY . .` pulls `node_modules/dist/.env`.
9. `HOST_PG_PORT` vs `DB_PORT` unsynced; `SETUP.md:47` hardcodes `port=5432`;
   live `.env DB_HOST=localhost` stale; `.env.example` placeholder paths.
10. `backend/tests/test_app.py:3-5` stale `agent_env` header; `docs/AGENT.md`
    has no host-env section.

## Fix batches (one concern per commit)

- `chore(docker)`: compose health + `extra_hosts` + optional env_file.
- `fix(backend)`: Dockerfile `PORT`, `HEALTHCHECK`, proxy hygiene.
- `fix(frontend)`: nginx timeouts/cache/healthz + `.dockerignore`.
- `docs`: AGENT host-env (`ml_env`), SETUP ports/psql, CAVEATS notes,
  test header, CHANGELOG.

## Verification

- `docker compose config --quiet`
- `GET :8000/api/health`, `GET :5173/healthz` (after up)
- `pytest` (root) + `ruff`; `npm run lint/build` (frontend)
- `git status/diff --stat` per batch; never stage `.env`.
