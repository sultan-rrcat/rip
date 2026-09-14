# Changelog

Notable user-visible changes. Merge-era history (2026-09-08 → 2026-09-13) is frozen in `docs/archive/SESSION_LOG.md`.

---

## Unreleased — BGE path resolution

- Relative BGE model paths auto-resolve to repo-root absolute and fail fast with the missing path instead of a cryptic boot crash.
- `DB_HOST` defaults to `127.0.0.1` (`localhost` auto-normalized), 5s connect timeout, password masked on connect failure.

## 2026-09-13 — Trivial-plan fallback

- Greetings and other trivial messages no longer fail with `No steps were executed`. The planner is instructed to emit a single `reasoning` step, and the engine repairs any still-empty plan the same way (`e58e18b`, ADR-026).

## 2026-09-13 — Docs reset as new project

- Archived `MERGE_PLAN.md`, `IMPLEMENTATION_PLAN.md`, `SESSION_LOG.md` to `docs/archive/` (frozen, history only).
- New `docs/ARCHITECTURE.md`; full `README.md` rewrite; curated `docs/ADR.md` (+ ADR-026); refreshed `docs/SETUP.md`; new `docs/CAVEATS.md`; `docs/AGENT.md` rewritten as maintainer guide.
