# Changelog

Notable user-visible changes. Merge-era history (2026-09-08 → 2026-09-13) is frozen in `docs/archive/SESSION_LOG.md`.

---

## Unreleased — compose lifecycle scripts

- New `scripts/rip.ps1` (+ `scripts/rip.sh` mirror): `up` (always `--build`, waits healthy), `down`, `fresh` (wipes `pgdata` + `uploads` with confirm), `restart`, `rebuild`, `logs`, `ps`/`status`, `migrate` (re-applies `schema.sql` to running postgres, no host `psql` needed), `health`. Scripts clear stale shell `DB_*`, probe `127.0.0.1`, and read host ports from `.env`. Documented in `docs/SETUP.md` §4–5; `docs/AGENT.md` §5 points at them as canonical.

## Unreleased — chart rendering fix

- Charts (`plot.chart`) now render inline in chat as images over the artifact download URL instead of raw `<svg>` code. The run summary carries a short placeholder; the SVG bytes travel via the SSE `artifacts` event only.
- Chart previews persist on assistant messages (`messages.artifacts`) so they survive page reload. DB: re-apply `backend/schema.sql` via `psql` on existing databases (compose init runs once).
- Docs: corrected artifact disk path to `{upload_dir}/{notebook_id}/artifacts/{run_id}/{step_id}/{filename}` + download route `GET /v1/runs/{id}/artifacts/{artifact_id}` (ADR-019, CONTEXT, ARCHITECTURE); documented the aggregator SVG-placeholder exception (ADR-023).

## Unreleased — container/deploy hardening

- Compose: backend + frontend healthchecks (`/api/health`, `/healthz`),
  frontend gates on backend healthy, `extra_hosts` gateway for portable
  `host.docker.internal` (Windows/Linux), optional `.env` file.
- Backend image honors `$PORT`, stdlib `HEALTHCHECK`, proxy hygiene
  (`NO_PROXY` defaults; build proxy no longer persisted).
- Frontend nginx: real `proxy_cache off`/timeouts for SSE, `/healthz`,
  immutable `/assets/` vs no-cache `index.html`; new `frontend/.dockerignore`.
- Docs: host env canonical `ml_env` (`docs/AGENT.md` §5.1), `HOST_PG_PORT`
  sync + `psql` port fix, compose-init-once + same-origin notes.
- Docker hardening round 2: frontend `context: ./frontend` fix, `VITE_API_URL`
  build-arg, `PORT: 8000` pinned with `HOST_BACKEND_PORT`/`HOST_FRONTEND_PORT`
  host remaps, backend healthcheck grace 180s, Postgres `pg_isready` via
  container `$POSTGRES_USER`/`$POSTGRES_DB`, `OLLAMA_BASE_URL` interpolated
  from `.env`, nginx dual `listen` + `127.0.0.1` probes, reranker mount path
  `models/reranker/bge_reranker_v2_m3`, stale shell `DB_*` shadow documented.

- BGE path resolution (prior unreleased):
  Relative BGE model paths auto-resolve to repo-root absolute and fail fast.
- `DB_HOST` defaults to `127.0.0.1` (`localhost` auto-normalized), 5s connect timeout, password masked on connect failure.
- Ollama init degrades on 5s probe instead of boot-crashing; requests fail honest per call.
- Frontend uses same-origin API when `VITE_API_URL` is empty; nginx allows 100M uploads with unbuffered SSE.

## 2026-09-13 — Trivial-plan fallback

- Greetings and other trivial messages no longer fail with `No steps were executed`. The planner is instructed to emit a single `reasoning` step, and the engine repairs any still-empty plan the same way (`e58e18b`, ADR-026).

## 2026-09-13 — Docs reset as new project

- Archived `MERGE_PLAN.md`, `IMPLEMENTATION_PLAN.md`, `SESSION_LOG.md` to `docs/archive/` (frozen, history only).
- New `docs/ARCHITECTURE.md`; full `README.md` rewrite; curated `docs/ADR.md` (+ ADR-026); refreshed `docs/SETUP.md`; new `docs/CAVEATS.md`; `docs/AGENT.md` rewritten as maintainer guide.
