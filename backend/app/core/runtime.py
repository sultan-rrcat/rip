"""Runtime-config store (Option A): DB source of truth + in-memory write-through.

Contract:
- Precedence: DB override > env/.env > code default (in `core/config.py`).
- `settings` singleton is the in-memory read cache: hot paths keep reading
  `settings.X` with zero DB round-trips. PUT writes DB first, then `setattr`
  on the singleton (strong consistency in this process — not eventual).
- `load_runtime_overrides()` runs once in lifespan BEFORE the /v1 runtime is
  composed, so VectorRAG/provider pick up overridden values at boot.
- Apply modes: `live` (next request) vs `restart` (needs
  `docker compose restart backend` for singletons built once — BGE models,
  httpx client, DB pool). Restart keys still update `settings` immediately;
  the snapshot reports them in `pending_restart` while they differ from boot.
- Secrets are never editable here (see READ_ONLY_MASKED).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger("core.runtime")

ApplyMode = Literal["live", "restart"]
Source = Literal["default", "env", "db"]

PENDING_RESTART_COMMAND = "docker compose restart backend"


@dataclass(frozen=True)
class FieldSpec:
    key: str
    group: str
    label: str
    description: str
    type: Literal["int", "float", "bool", "str", "enum"]
    apply: ApplyMode
    options: tuple[str, ...] = ()
    min: float | None = None
    max: float | None = None
    editable: bool = True
    secret: bool = False


@dataclass(frozen=True)
class GroupSpec:
    id: str
    title: str
    description: str
    apply: ApplyMode | Literal["mixed", "system"]


GROUPS: tuple[GroupSpec, ...] = (
    GroupSpec("model", "Model & Generation",
              "Default model and per-call generation defaults.", "live"),
    GroupSpec("budgets", "Task Budgets & Deadlines",
              "Output caps and fail-fast deadlines for control-plane calls.", "live"),
    GroupSpec("orchestration", "Orchestration",
              "Planner depth and the L3 ReAct force switch.", "live"),
    GroupSpec("memory", "Memory & Context",
              "Context-window budget, verbatim window and summarizer caps.", "live"),
    GroupSpec("retrieval", "Retrieval / RAG",
              "Whole-file shortcut share and the PDF loader order.", "live"),
    GroupSpec("ingestion", "Uploads & Ingestion",
              "Upload size guard. Extensions stay code-fixed.", "live"),
    GroupSpec("observability", "Observability",
              "Tracing toggle and log verbosity.", "live"),
    GroupSpec("connection", "Model Connection",
              "Ollama endpoint and client budget. Restart to rebind the HTTP client.", "mixed"),
    GroupSpec("embeddings", "Embedding Models",
              "Local BGE weights. Reloads only on backend restart (heavy).", "mixed"),
    GroupSpec("sandbox", "Code Sandbox",
              "Ephemeral Docker containers running the opencode CLI. All live.", "live"),
    GroupSpec("system", "System (read-only)",
              "Operator-owned boot values. Change via .env / compose, not here.", "system"),
)

FIELDS: dict[str, FieldSpec] = {}


def _reg(spec: FieldSpec) -> None:
    FIELDS[spec.key] = spec


# — Model & Generation (live) —
_reg(FieldSpec("ollama_default_model", "model", "Default model",
               "Used when the requested model is not pulled locally.", "str", "live"))
_reg(FieldSpec("default_temperature", "model", "Temperature",
               "Sampling temperature for generation calls.", "float", "live", min=0.0, max=2.0))
_reg(FieldSpec("default_max_tokens", "model", "Default max tokens",
               "Shared fallback cap when a step sets no task budget. NOTE: plain chat uses "
               "Chat budget and code generation uses Coding budget — lowering only this "
               "key will not cap those paths.", "int", "live", min=128, max=16384))
_reg(FieldSpec("default_timeout_ms", "model", "Default step timeout",
               "Fallback deadline for a single model call.", "int", "live", min=5000, max=600000))
# — Budgets (live) —
_reg(FieldSpec("coding_max_tokens", "budgets", "Coding budget",
               "Output cap for code generation steps (overrides the shared default).", "int", "live", min=256, max=16384))
_reg(FieldSpec("chat_max_tokens", "budgets", "Chat budget",
               "Output cap for plain chat answers (overrides the shared default).", "int", "live", min=128, max=8192))
_reg(FieldSpec("router_timeout_ms", "budgets", "Router deadline",
               "L1 intent classification fails open fast under saturation.", "int", "live", min=1000, max=300000))
_reg(FieldSpec("planner_timeout_ms", "budgets", "Planner deadline",
               "L3 ReAct planner / summarizer call budget.", "int", "live", min=5000, max=300000))
# — Orchestration (live) —
_reg(FieldSpec("default_max_plan_steps", "orchestration", "Max plan steps",
               "Validator cap on plan length.", "int", "live", min=1, max=50))
_reg(FieldSpec("force_react", "orchestration", "Force ReAct",
               "Skip L1 router + L2 builders; every request runs the ReAct loop.", "bool", "live"))
# — Memory (live) —
_reg(FieldSpec("ollama_context_window", "memory", "Context window",
               "Native window of the serving model; scales the memory budget.", "int", "live", min=4096, max=131072))
_reg(FieldSpec("memory_window_size", "memory", "Verbatim window",
               "Advisory recent-turn window; the token budget is the real constraint.", "int", "live", min=1, max=50))
_reg(FieldSpec("summary_threshold_pct", "memory", "Summary threshold",
               "Share of the context window that triggers folding.", "float", "live", min=0.1, max=0.95))
_reg(FieldSpec("summary_max_tokens", "memory", "Summary cap",
               "Token cap for the rolling summary LLM call.", "int", "live", min=64, max=4096))
# — Retrieval (live) —
_reg(FieldSpec("rag_whole_file_pct", "retrieval", "Whole-file share",
               "Per-shard share of the window under which ranking is skipped.", "float", "live", min=0.01, max=0.5))
_reg(FieldSpec("rag_pdf_loader", "retrieval", "PDF loader order",
               "Selected loader runs first; the other is fallback.", "enum", "live",
               options=("docling", "opendataloader")))
# — Ingestion (live) —
_reg(FieldSpec("max_upload_size_mb", "ingestion", "Max upload size",
               "Per-file upload guard in megabytes.", "int", "live", min=1, max=500))
# — Observability (live) —
_reg(FieldSpec("langfuse_enabled", "observability", "Langfuse tracing",
               "Off = tracing wrapper is a no-op passthrough.", "bool", "live"))
_reg(FieldSpec("log_level", "observability", "Log level",
               "Root logger verbosity, applied immediately.", "enum", "live",
               options=("DEBUG", "INFO", "WARNING", "ERROR")))
_reg(FieldSpec("langfuse_host", "observability", "Langfuse host",
               "Tracing endpoint. Client re-inits on restart.", "str", "restart"))
# — Connection (restart to rebind httpx client) —
_reg(FieldSpec("ollama_base_url", "connection", "Ollama base URL",
               "Model server endpoint. Restart to rebind the HTTP client.", "str", "restart"))
_reg(FieldSpec("ollama_timeout_ms", "connection", "Ollama client timeout",
               "HTTP client budget baked at provider construction.", "int", "restart",
               min=5000, max=900000))
# — Embeddings (restart: torch singletons) —
_reg(FieldSpec("bge_m3_model_path", "embeddings", "BGE-M3 path",
               "Embedding weights. Missing path = degraded boot.", "str", "restart"))
_reg(FieldSpec("bge_reranker_v2_m3", "embeddings", "Reranker path",
               "Reranker weights. Missing path = degraded boot.", "str", "restart"))
_reg(FieldSpec("upload_dir", "embeddings", "Upload directory",
               "File storage root. Changing it strands existing files.", "str", "restart"))
# — Sandbox (live: read per execution) —
_reg(FieldSpec("sandbox_image", "sandbox", "Sandbox image",
               "Docker image with the opencode CLI. Build once: docker build -t rip-sandbox sandbox/.",
               "str", "live"))
_reg(FieldSpec("sandbox_timeout_ms", "sandbox", "Sandbox timeout",
               "Hard deadline per sandbox run, under the step wall-clock.", "int", "live",
               min=10000, max=480000))
_reg(FieldSpec("sandbox_cpus", "sandbox", "Sandbox CPUs",
               "CPU quota per sandbox container.", "float", "live", min=0.5, max=16.0))
_reg(FieldSpec("sandbox_memory", "sandbox", "Sandbox memory",
               "Memory cap per sandbox container (docker format, e.g. 2g).", "str", "live"))
# — System (read-only display) —
for _k, _label in (
    ("db_host", "DB host"), ("db_port", "DB port"), ("db_name", "DB name"),
    ("db_user", "DB user"), ("port", "Backend port"),
    ("cors_origins", "CORS origins"), ("env", "Environment"),
):
    _reg(FieldSpec(_k, "system", _label, "Operator-owned; change via .env / compose.",
                   "str" if _k not in ("db_port", "port") else "int", "restart", editable=False))
_reg(FieldSpec("db_password", "system", "DB password", "Secret — never displayed.",
               "str", "restart", editable=False, secret=True))
_reg(FieldSpec("langfuse_public_key", "system", "Langfuse public key",
               "Secret — set via .env.", "str", "restart", editable=False, secret=True))
_reg(FieldSpec("langfuse_secret_key", "system", "Langfuse secret key",
               "Secret — set via .env.", "str", "restart", editable=False, secret=True))

EDITABLE_KEYS = tuple(k for k, s in FIELDS.items() if s.editable)
RESTART_KEYS = tuple(k for k, s in FIELDS.items() if s.apply == "restart" and s.editable)

# Boot snapshot: value of every known key at the end of load_runtime_overrides.
_boot_snapshot: dict[str, Any] = {}
_loaded = False


def _fresh_env_value(key: str) -> Any:
    """Value from env/.env without DB overrides (fresh Settings)."""
    from app.core.config import Settings

    return getattr(Settings(), key)


def _code_default(key: str) -> Any:
    from app.core.config import Settings

    f = Settings.model_fields[key]
    return f.default


def coerce_value(spec: FieldSpec, raw: Any) -> Any:
    """Validate + coerce a raw admin input. Raises ValueError(key: reason)."""
    if spec.type == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("true", "1", "yes", "on"):
            return True
        if text in ("false", "0", "no", "off"):
            return False
        raise ValueError(f"{spec.key}: expected boolean (true/false)")
    if spec.type == "int":
        try:
            val = int(str(raw).strip())
        except (ValueError, TypeError):
            raise ValueError(f"{spec.key}: expected integer")
        if spec.min is not None and val < spec.min:
            raise ValueError(f"{spec.key}: minimum is {int(spec.min)}")
        if spec.max is not None and val > spec.max:
            raise ValueError(f"{spec.key}: maximum is {int(spec.max)}")
        return val
    if spec.type == "float":
        try:
            val = float(str(raw).strip())
        except (ValueError, TypeError):
            raise ValueError(f"{spec.key}: expected number")
        if spec.min is not None and val < spec.min:
            raise ValueError(f"{spec.key}: minimum is {spec.min}")
        if spec.max is not None and val > spec.max:
            raise ValueError(f"{spec.key}: maximum is {spec.max}")
        return val
    if spec.type == "enum":
        text = str(raw).strip()
        lowered = {o.lower(): o for o in spec.options}
        if spec.key == "log_level":
            hit = lowered.get(text.lower())
        else:
            hit = lowered.get(text.strip().lower())
        if hit is None:
            raise ValueError(f"{spec.key}: expected one of {', '.join(spec.options)}")
        return hit
    # str
    text = str(raw).strip() if raw is not None else ""
    if not text:
        raise ValueError(f"{spec.key}: must not be empty")
    if spec.key == "ollama_base_url" and not text.startswith(("http://", "https://")):
        raise ValueError(f"{spec.key}: must start with http:// or https://")
    if spec.key == "rag_pdf_loader":
        text = text.lower()
    return text


def _to_db_string(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _db_rows() -> dict[str, dict[str, Any]]:
    from app.core.db import pg_connection

    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT key, value, updated_by, updated_at FROM runtime_config")
        rows = cur.fetchall()
    out: dict[str, dict[str, Any]] = {}
    for k, v, by, at in rows:
        out[str(k)] = {"value": v, "updated_by": by,
                       "updated_at": at.isoformat() if at is not None else None}
    return out


def _source_for(key: str, in_db: bool) -> Source:
    if in_db:
        return "db"
    try:
        if _fresh_env_value(key) != _code_default(key):
            return "env"
    except Exception:  # noqa: BLE001, S110 - source detection must never break GET
        pass
    return "default"


def _pending_keys() -> list[str]:
    if not _boot_snapshot:
        return []
    from app.core.config import settings

    pending: list[str] = []
    for key, boot_val in _boot_snapshot.items():
        spec = FIELDS.get(key)
        if spec is None or spec.apply != "restart":
            continue
        try:
            if getattr(settings, key) != boot_val:
                pending.append(key)
        except AttributeError:
            continue
    return sorted(pending)


def snapshot() -> dict[str, Any]:
    """Full admin snapshot: groups + per-key entries + pending_restart."""
    from app.core.config import settings

    try:
        db = _db_rows()
    except Exception as e:  # noqa: BLE001 - GET degrades honest when DB is down
        logger.warning("runtime snapshot: DB unreadable (%s)", e)
        db = {}
    entries: dict[str, Any] = {}
    for key, spec in FIELDS.items():
        try:
            current = getattr(settings, key)
        except AttributeError:
            continue
        in_db = key in db
        if spec.secret:
            entries[key] = {
                "key": key, "group": spec.group, "label": spec.label,
                "description": spec.description, "type": spec.type,
                "apply": spec.apply, "editable": spec.editable,
                "secret": True, "value": "••••••" if current else "",
                "configured": bool(current),
                "default": "", "source": "db" if in_db else "default",
                "updated_by": (db.get(key) or {}).get("updated_by"),
                "updated_at": (db.get(key) or {}).get("updated_at"),
            }
            continue
        try:
            default = _code_default(key)
        except Exception:  # noqa: BLE001
            default = current
        entries[key] = {
            "key": key, "group": spec.group, "label": spec.label,
            "description": spec.description, "type": spec.type,
            "apply": spec.apply, "editable": spec.editable,
            "value": current, "default": default,
            "source": _source_for(key, in_db),
            "options": list(spec.options) or None,
            "min": spec.min, "max": spec.max,
            "updated_by": (db.get(key) or {}).get("updated_by"),
            "updated_at": (db.get(key) or {}).get("updated_at"),
        }
    pending = _pending_keys()
    return {
        "groups": [g.__dict__ | {"fields": [k for k, s in FIELDS.items() if s.group == g.id]}
                   for g in GROUPS],
        "entries": entries,
        "pending_restart": pending,
        "pending_restart_command": PENDING_RESTART_COMMAND,
    }


def load_runtime_overrides() -> int:
    """Apply DB overrides onto the settings singleton (lifespan, once).

    Returns the number of keys applied. Never raises — a DB failure leaves
    code/env values in force and is logged.
    """
    global _boot_snapshot, _loaded
    from app.core.config import settings

    applied = 0
    try:
        db = _db_rows()
    except Exception as e:  # noqa: BLE001 - degraded boot, not a crash
        logger.warning("runtime overrides skipped (DB down: %s)", e)
        _boot_snapshot = {k: getattr(settings, k, None) for k in FIELDS if hasattr(settings, k)}
        _loaded = True
        return 0
    for key, row in db.items():
        spec = FIELDS.get(key)
        if spec is None or not spec.editable or spec.secret:
            continue
        try:
            setattr(settings, key, coerce_value(spec, row["value"]))
            applied += 1
        except Exception as e:  # noqa: BLE001 - one bad row must not block the rest
            logger.warning("runtime override %r ignored: %s", key, e)
    _apply_side_effects(set(db.keys()))
    _boot_snapshot = {k: getattr(settings, k, None) for k in FIELDS if hasattr(settings, k)}
    _loaded = True
    if applied:
        logger.info("applied %d runtime override(s) from DB", applied)
    return applied


def _apply_side_effects(keys: set[str]) -> None:
    """Best-effort in-process follow-ups. Never raises."""
    try:
        if "log_level" in keys:
            import logging as _logging

            from app.core.config import settings as _s

            level = getattr(_logging, str(_s.log_level).upper(), _logging.INFO)
            _logging.getLogger().setLevel(level)
        if "langfuse_enabled" in keys:
            from app.core.config import settings as _s2

            if _s2.langfuse_enabled:
                try:
                    from app.observability.langfuse import init_langfuse

                    init_langfuse()
                except Exception as e:  # noqa: BLE001
                    logger.warning("langfuse re-init failed: %s", e)
            else:
                try:
                    import app.observability.langfuse as _lf

                    _lf._client = None
                except Exception:  # noqa: BLE001, S110 - tracing off is best-effort
                    pass
        if "ollama_default_model" in keys:
            # Stale-capture repair: Planner/Router/ReAct pin the default at
            # construction; push the new value into live singletons so the
            # next run picks it up without a restart.
            try:
                from app.api import deps as _deps
                from app.core.config import settings as _s3

                for attr in ("_orchestrator", "_agent_registry"):
                    obj = getattr(_deps, attr, None)
                    for sub in ("_planner", "_model"):
                        try:
                            if attr == "_orchestrator" and obj is not None and hasattr(obj, "_planner"):
                                if hasattr(obj._planner, "_model"):
                                    obj._planner._model = _s3.ollama_default_model
                            elif attr == "_agent_registry" and sub == "_model":
                                pass
                        except Exception:  # noqa: BLE001, S110
                            pass
            except Exception:  # noqa: BLE001, S110
                pass
    except Exception as e:  # noqa: BLE001 - side effects never fail a PUT
        logger.warning("runtime side effects failed: %s", e)


def apply_updates(updates: dict[str, Any], *, actor: str | None = None) -> list[str]:
    """Validate ALL, then write DB + memory. Returns applied keys.

    Raises ValueError (single message, 422) or LookupError (unknown key, 404).
    DB failure raises RuntimeError (503) with memory untouched.
    """
    from app.core.config import settings
    from app.core.db import pg_connection

    if not updates:
        raise ValueError("updates must not be empty")
    coerced: dict[str, Any] = {}
    for key, raw in updates.items():
        spec = FIELDS.get(key)
        if spec is None:
            raise LookupError(f"unknown config key: {key}")
        if not spec.editable or spec.secret:
            raise ValueError(f"{key}: read-only (managed via .env)")
        coerced[key] = coerce_value(spec, raw)
    # DB first (source of truth); memory untouched on failure.
    try:
        with pg_connection() as conn, conn.cursor() as cur:
            for key, val in coerced.items():
                cur.execute(
                    """INSERT INTO runtime_config (key, value, updated_by, updated_at)
                       VALUES (%s, %s, %s, NOW())
                       ON CONFLICT (key) DO UPDATE
                       SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by,
                           updated_at = NOW()""",
                    (key, _to_db_string(val), actor),
                )
    except Exception as e:  # noqa: BLE001 - DB outage must surface as 503
        logger.warning("runtime PUT failed (DB): %s", e)
        raise RuntimeError(f"config store unavailable: {e}")
    for key, val in coerced.items():
        setattr(settings, key, val)
    _apply_side_effects(set(coerced.keys()))
    logger.info("runtime config updated by %s: %s", actor or "admin", sorted(coerced))
    return sorted(coerced)


def reset_keys(keys: list[str] | None = None) -> list[str]:
    """Delete DB overrides (all or subset) and restore env/default values."""
    from app.core.config import settings
    from app.core.db import pg_connection

    targets = list(keys) if keys else [k for k in FIELDS if FIELDS[k].editable]
    for key in targets:
        if key not in FIELDS:
            raise LookupError(f"unknown config key: {key}")
        if not FIELDS[key].editable:
            raise ValueError(f"{key}: read-only")
    try:
        with pg_connection() as conn, conn.cursor() as cur:
            if keys:
                cur.execute("DELETE FROM runtime_config WHERE key = ANY(%s)", (targets,))
            else:
                cur.execute("DELETE FROM runtime_config")
    except Exception as e:  # noqa: BLE001 - DB outage must surface as 503
        logger.warning("runtime reset failed (DB): %s", e)
        raise RuntimeError(f"config store unavailable: {e}")
    restored: list[str] = []
    for key in targets:
        try:
            setattr(settings, key, _fresh_env_value(key))
            restored.append(key)
        except Exception as e:  # noqa: BLE001
            logger.warning("runtime reset %r failed: %s", key, e)
    _apply_side_effects(set(restored))
    logger.info("runtime config reset: %s", sorted(restored))
    return sorted(restored)
