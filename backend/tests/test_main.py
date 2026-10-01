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

import importlib
import sys
import types

import pytest

try:
    import httpx

    _IMPORT_ERROR = None
except Exception as e:  # noqa: BLE001 - import probe; pragma: no cover
    httpx = None  # type: ignore[assignment]
    _IMPORT_ERROR = e


def _try_import_real(name: str):
    """Import a real RAG module when the torch chain is healthy.

    Returns the module, or None on torch rot (the reason this file
    stubs at all). Never raises; scrubs half-imported remnants so the
    stub install below starts clean.
    """
    try:
        return importlib.import_module(name)
    except Exception:  # noqa: BLE001 - probe; any failure means "stub it"
        sys.modules.pop(name, None)
        return None


def _delegate_missing(stub, real):
    """Serve names the stub does not define from the real module (PEP 562).

    Other test modules import helpers straight from these keys
    (``_interleave_by_source`` from ``app.rag.vector_rag``,
    ``RagPipeline``/``settings`` from ``app.rag.pipeline``). Without
    delegation the stub shadows the real module session-wide and those
    imports fail — a collection error plus order-dependent failures.
    """

    def __getattr__(name: str):
        return getattr(real, name)

    stub.__getattr__ = __getattr__


def _stub_heavy_rag():
    """Stub the torch-pulling RAG modules (single choke point).

    Installed unconditionally (replacing any probe residue): the lifespan
    must bind the fast fake ``VectorRAG`` — constructing the real one
    costs ~50 s per ``TestClient`` entry. ``RagPipeline`` is the real
    class when importable (the lifespan never constructs it, and unit
    tests exercise its real methods); anything the stub does not define
    delegates to the real module so sibling test files keep working.
    """
    real_vector_rag = _try_import_real("app.rag.vector_rag")
    real_pipeline = _try_import_real("app.rag.pipeline")

    mod = types.ModuleType("app.rag.vector_rag")

    class VectorRAG:  # test double: lifespan binds whatever this is
        def __init__(self, *a, **k):
            pass

        def retrieve_context(
            self, notebook_id, query, top_k=4, file_id=None, file_name=None, mode="specific"
        ):
            return []

    mod.VectorRAG = VectorRAG
    if real_vector_rag is not None:
        _delegate_missing(mod, real_vector_rag)
    sys.modules["app.rag.vector_rag"] = mod

    pipe = types.ModuleType("app.rag.pipeline")
    if real_pipeline is not None and hasattr(real_pipeline, "RagPipeline"):
        pipe.RagPipeline = real_pipeline.RagPipeline
        _delegate_missing(pipe, real_pipeline)
    else:

        class RagPipeline:  # imported by app.routes.files; never run here
            def __init__(self, *a, **k):
                pass

        pipe.RagPipeline = RagPipeline
        if real_pipeline is not None:
            _delegate_missing(pipe, real_pipeline)
    sys.modules["app.rag.pipeline"] = pipe


_stub_heavy_rag()

try:
    import app.main as main_module
    from app.api import deps
    from app.tools.rag_query import get_rag_singleton
    from fastapi.testclient import TestClient
except Exception as e:  # noqa: BLE001 - import probe; pragma: no cover
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
    except Exception:  # noqa: BLE001 - any probe failure means "down"
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
