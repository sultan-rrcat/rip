# app/main.py — RIP backend entrypoint (Phase 4.2 rewrite).
#
# Merges the RIP lifespan (load VectorRAG ONCE, bind it for rag.query) with
# the /v1 run-lifecycle mounts. No plugin loader: the runtime is composed
# directly and installed into app.api.deps (see lifespan). Served as
# `uvicorn app.main:app` from rip/backend/.

from contextlib import asynccontextmanager

from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load FIRST, before any app.* import: the settings singleton is built at
# first config import, so a late load_dotenv() silently leaves defaults in
# force (found live 6.2: BGE paths fell back to relative defaults and boot
# crashed). find_dotenv searches CWD upward, so rip/.env loads whether the
# server starts from rip/ or rip/backend/.
load_dotenv(find_dotenv(usecwd=True))

from app.api import admin, deps, health, runs  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.observability.langfuse import flush as langfuse_flush  # noqa: E402
from app.observability.langfuse import init_langfuse  # noqa: E402
from app.routes import files, messages, notebooks  # noqa: E402

logger = setup_logging()
init_langfuse()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Deferred: app.rag.vector_rag pulls torch (sentence_transformers).
    # routes/services use lazy/Any typing so import app.main never pulls
    # torch; the singleton itself is still constructed here, once,
    # so rag.query reuses it instead of reloading models per query.
    from app.tools.rag_query import bind_rag_singleton

    # Self-healing DDL for pre-existing volumes: compose Postgres init runs
    # schema.sql only on an empty pgdata volume, so redeploys with new
    # columns would 500 until hand-migrated. Fail-soft (warns only).
    try:
        from app.routes.messages import ensure_artifacts_column

        ensure_artifacts_column()
    except Exception:
        logger.warning("startup DDL ensure failed, continuing", exc_info=True)

    logger.info("Initiating ML models...")
    from app.core.config import settings as _settings

    logger.info(
        "BGE m3=%s reranker=%s",
        _settings.bge_m3_model_path,
        _settings.bge_reranker_v2_m3,
    )
    try:
        from app.rag.vector_rag import VectorRAG

        app.state.rag = VectorRAG()
        bind_rag_singleton(app.state.rag)
        logger.info("ML models loaded successfully.")
    except Exception:
        # Degraded boot, not a crash: /api/* still serves, /v1/* fails
        # honest per request (rag.query unbound → ok=False).
        app.state.rag = None
        logger.warning(
            "ML models NOT loaded (BGE weights missing?) — "
            "/api/* serves, rag.query fails honest",
            exc_info=True,
        )

    # Compose the /v1 runtime around the lifespan RAG singleton so
    # rag.query reuses it (never reloads models per query). Ollama-down is
    # a degraded boot, not a crash: /api/* still serves and /v1/* fails
    # honest per request (deps lazy getters raise).
    try:
        from app.agents.registry import get_default_agent_registry
        from app.orchestration.aggregator import Aggregator
        from app.orchestration.orchestrator import Orchestrator
        from app.orchestration.planner import Planner
        from app.orchestration.validator import PlanValidator
        from app.providers.ollama import OllamaProvider
        from app.providers.tracing import wrap_provider
        from app.runs.manager import RunManager
        from app.tools.registry import get_default_tool_registry

        provider = wrap_provider(OllamaProvider())
        agent_registry = get_default_agent_registry(provider)
        tool_registry = get_default_tool_registry(
            rag=app.state.rag, provider=provider
        )
        orchestrator = Orchestrator(
            Planner(provider, agent_registry, tool_registry),
            PlanValidator(agent_registry, tool_registry),
            Aggregator(),
            agent_registry,
            tool_registry=tool_registry,
        )
        run_manager = RunManager(provider=provider, orchestrator=orchestrator)
        deps.configure(
            provider=provider,
            agent_registry=agent_registry,
            tool_registry=tool_registry,
            orchestrator=orchestrator,
            run_manager=run_manager,
        )
        logger.info("/v1 runtime configured (rag-bound tool registry)")
    except Exception:
        logger.warning(
            "/v1 runtime NOT configured (Ollama down?) — "
            "/api/* serves, /v1/runs fails honest",
            exc_info=True,
        )

    yield  # transferring control back to fastapi

    logger.info("Shutting down and clearing models...")
    langfuse_flush()
    app.state.rag = None
    deps.reset()


logger.info("Logging has been successfully set up.")

app = FastAPI(lifespan=lifespan)

# RIP document/notebook API (existing, kept).
app.include_router(notebooks.router)
app.include_router(files.router)
app.include_router(messages.router)
# NOTE (Phase 4.3): app/routes/llm.py deleted — the old prompt endpoints are
# gone with no shim. Replacement: POST /v1/runs + GET /v1/runs/{id}/events.

# Run lifecycle + admin stub (/v1/*, bare JSON — no envelope).
app.include_router(runs.router)
app.include_router(admin.router)

# Liveness probes: /api/health (RIP alias, kept) + /health (canonical).
app.include_router(health.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}

# Enable CORS — origins from settings (plain str, split on ",").
# Browsers reject wildcard + credentials, so credentials are only
# enabled for explicit origin lists.
from app.core.config import settings as _cors_settings  # noqa: E402

_cors_origins = [o.strip() for o in (_cors_settings.cors_origins or "*").split(",") if o.strip()]
_cors_allow_credentials = "*" not in _cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)
