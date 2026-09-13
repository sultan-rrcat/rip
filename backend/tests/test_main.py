"""Phase 4.2 — app/main.py: lifespan + /api/* + /v1/* mounts.

The torch chain (`app.rag.vector_rag` → pipeline → sentence_transformers)
cannot import on the current host venv (torchaudio rot — Phase 6), so this
file stubs `app.rag.vector_rag` (VectorRAG) and `app.rag.pipeline`
(RagPipeline) in sys.modules BEFORE importing app.main. The lifespan then
runs for real under TestClient: stub RAG in, real Ollama-backed runtime
around it. Needs Ollama up (`OLLAMA_BASE_URL=http://localhost:11434` from
the host) for the runtime assertions; mount/health assertions hold even
degraded.

Run with ``pytest backend/tests/test_main.py -q --noconftest``.
"""

from __future__ import annotations

import sys
import types

import pytest

try:
    import httpx

    _IMPORT_ERROR = None
except Exception as e:  # pragma: no cover - import-time guard
    httpx = None  # type: ignore[assignment]
    _IMPORT_ERROR = e


def _stub_heavy_rag():
    """Stub the torch-pulling RAG modules (single choke point)."""
    if "app.rag.vector_rag" not in sys.modules:
        mod = types.ModuleType("app.rag.vector_rag")

        class VectorRAG:  # test double: lifespan binds whatever this is
            def __init__(self, *a, **k):
                pass

            def retrieve_context(self, notebook_id, query, top_k=8):
                return []

        mod.VectorRAG = VectorRAG
        sys.modules["app.rag.vector_rag"] = mod
    if "app.rag.pipeline" not in sys.modules:
        pipe = types.ModuleType("app.rag.pipeline")

        class RagPipeline:  # imported by app.routes.files; never run here
            def __init__(self, *a, **k):
                pass

        pipe.RagPipeline = RagPipeline
        sys.modules["app.rag.pipeline"] = pipe


_stub_heavy_rag()

try:
    from fastapi.testclient import TestClient

    import app.main as main_module
    from app.api import deps
    from app.tools.rag_query import get_rag_singleton
except Exception as e:  # pragma: no cover - import-time guard
    TestClient = None  # type: ignore[assignment]
    main_module = None  # type: ignore[assignment]
    deps = None  # type: ignore[assignment]
    get_rag_singleton = None  # type: ignore[assignment]
    if _IMPORT_ERROR is None:
        _IMPORT_ERROR = e


def _ollama_up() -> bool:
    if _IMPORT_ERROR is not None or main_module is None:
        return False
    try:
        from app.core.config import settings

        r = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


needs_app = pytest.mark.skipif(
    _IMPORT_ERROR is not None or main_module is None,
    reason=f"app.main unimportable ({_IMPORT_ERROR})",
)
needs_ollama = pytest.mark.skipif(
    not _ollama_up(), reason="Ollama down — runtime assertions skipped"
)


@needs_app
class TestMounts:
    def test_both_health_probes_ok(self):
        with TestClient(main_module.app) as client:
            assert client.get("/api/health").json() == {"status": "ok"}
            assert client.get("/health").json() == {"status": "ok"}

    def test_v1_and_api_routes_mounted(self):
        paths = {r.path for r in main_module.app.routes}
        for expected in (
            "/v1/runs",
            "/v1/runs/{run_id}",
            "/v1/runs/{run_id}/events",
            "/v1/runs/{run_id}/cancel",
            "/v1/runs/{run_id}/artifacts/{artifact_id}",
            "/v1/admin/health",
            "/health",
            "/api/health",
            "/api/notebooks",
        ):
            assert expected in paths, f"missing route {expected}"

    def test_no_plugin_loader(self):
        import inspect

        src = inspect.getsource(main_module)
        # "No plugin loader" in prose is fine; what must not exist is the
        # machinery (manager import/attribute).
        assert "PluginManager" not in src
        assert "app.plugins" not in src and "plugins.manager" not in src


@needs_app
@needs_ollama
class TestLifespanRuntime:
    def test_rag_singleton_bound(self):
        from app.rag.vector_rag import VectorRAG

        with TestClient(main_module.app):
            rag = get_rag_singleton()
            assert isinstance(rag, VectorRAG)

    def test_runtime_configured_rag_bound(self):
        from app.rag.vector_rag import VectorRAG

        with TestClient(main_module.app) as client:
            tools = deps.get_tool_registry()
            bound = tools.get("rag.query")._rag
            assert isinstance(bound, VectorRAG)
            assert deps.get_run_manager() is not None
            assert deps.get_orchestrator() is not None
            body = client.get("/v1/admin/health").json()
            assert body["status"] == "ok"
            assert set(body["agents"]) == {"reasoning", "coding", "vision"}
            assert "rag.query" in body["tools"]

    def test_runtime_reset_on_shutdown(self):
        with TestClient(main_module.app):
            inside = deps.get_run_manager()
        # Teardown ran deps.reset(): the next access lazily builds a FRESH
        # manager instead of returning the lifespan one (no leaked state).
        assert deps.get_run_manager() is not inside
