# app/main.py — RIP backend entrypoint (Phase 4.2 rewrite).
#
# Merges the RIP lifespan (load VectorRAG ONCE, bind it for rag.query) with
# the /v1 run-lifecycle mounts. No plugin loader: the runtime is composed
# directly and installed into app.api.deps (see lifespan). Served as
# `uvicorn app.main:app` from rip/backend/.

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, deps, health, runs
from app.core.logging import setup_logging
from app.routes import files, messages, notebooks

load_dotenv()
logger = setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Deferred: app.rag.vector_rag pulls torch (sentence_transformers). It
    # also enters via app.routes.files at import time (production installs
    # torch properly); the singleton itself is still constructed here, once,
    # so rag.query reuses it instead of reloading models per query.
    from app.rag.vector_rag import VectorRAG
    from app.tools.rag_query import bind_rag_singleton

    logger.info("Initiating ML models...")
    app.state.rag = VectorRAG()
    bind_rag_singleton(app.state.rag)
    logger.info("ML models loaded successfully.")

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
        from app.runs.manager import RunManager
        from app.tools.registry import get_default_tool_registry

        provider = OllamaProvider()
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

# Enable CORS
# middleware - code that runs before and after every request
# CORS - Cross Origin Resource Sharing - Is a mechanism that allows to specify which other origins(domains, ports, protocols) are permitted to access their resources.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # meaning only http://localhost:5173", "http://10.31.2.94:5173 can make request here.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
