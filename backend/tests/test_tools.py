"""Phase 3.1 — tools: registry/executor + all 7 tools.

Live where possible (plot = stdlib, doc = installed libs, sandbox = docker
daemon, guarded by skip); fakes where live deps are unavailable (rag.query
uses an injected fake — real VectorRAG needs the torch env repair from
Phase 6; image.generate uses a mock provider — Ollama has no image model).
Run with ``pytest backend/tests/test_tools.py -q --noconftest`` until the
torch env is repaired — the shared conftest imports app.main, which needs
sentence_transformers.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
from app.tools.base import Tool, ToolRequest, ToolResponse
from app.tools.executor import execute_tool
from app.tools.rag_query import RagQueryTool, bind_rag_singleton, rag_query
from app.tools.registry import ToolRegistry, get_default_tool_registry

EXPECTED_IDS = [
    "code.sandbox",
    "doc.convert",
    "doc.generate",
    "image.generate",
    "notebook.inspect",
    "plot.chart",
    "rag.query",
]


def _docker_up() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        proc = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=10.0,
            check=False,  # returncode is the probe result
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


needs_docker = pytest.mark.skipif(not _docker_up(), reason="docker daemon down")


class FakeRAG:
    """Stand-in for VectorRAG (same retrieve_context shape)."""

    def __init__(self, results: list[dict] | None = None):
        self.seen: list[tuple] = []
        self.results = results if results is not None else [
            {"content": "c1", "source": "f.pdf", "section": "H1", "rerank_score": 0.9},
            {"content": "c2", "source": "f.pdf", "section": "H1", "rerank_score": 0.8},
            {"content": "c3", "source": "g.pdf", "section": "H2", "rerank_score": 0.7},
        ]

    def retrieve_context(self, notebook_id, query, top_k=8):
        self.seen.append((notebook_id, query, top_k))
        return {"query": query, "results": list(self.results)}


# --- Registry / executor ---


class TestRegistry:
    def test_all_seven_registered(self):
        reg = get_default_tool_registry(rag=FakeRAG())
        assert sorted(t["tool_id"] for t in reg.manifest()) == EXPECTED_IDS
        assert len(reg) == 7

    def test_all_inherit_tool(self):
        reg = get_default_tool_registry(rag=FakeRAG())
        for tool_id in EXPECTED_IDS:
            assert isinstance(reg.get(tool_id), Tool)

    def test_unknown_tool_raises(self):
        with pytest.raises(KeyError):
            get_default_tool_registry().get("nope.tool")

    def test_no_approval_gate_in_executor(self):
        # Signature must not contain an approval parameter (merge decision).
        import inspect

        assert "approved" not in inspect.signature(execute_tool).parameters

    def test_sandboxed_tool_runs_without_approval(self):
        reg = get_default_tool_registry(rag=FakeRAG())
        resp = execute_tool(
            reg, "plot.chart",
            {"chart_type": "bar", "labels": ["a"], "values": [1]},
        )
        assert resp.ok

    def test_raising_tool_becomes_ok_false(self):
        class Boom(Tool):
            tool_id = "boom.tool"
            name = "Boom"
            description = "raises"
            effect_class = "read-only"  # type: ignore[assignment]

            def execute(self, request: ToolRequest) -> ToolResponse:
                raise RuntimeError("kaboom")

        reg = ToolRegistry()
        reg.register(Boom())
        resp = execute_tool(reg, "boom.tool", {})
        assert not resp.ok and "kaboom" in (resp.error or "")

    def test_identity_mismatch_rejected(self):
        class Liar(Tool):
            tool_id = "liar.tool"
            name = "Liar"
            description = "wrong id"
            effect_class = "read-only"  # type: ignore[assignment]

            def execute(self, request: ToolRequest) -> ToolResponse:
                return ToolResponse(tool_id="other.tool", ok=True)

        reg = ToolRegistry()
        reg.register(Liar())
        resp = execute_tool(reg, "liar.tool", {})
        assert not resp.ok and "mismatch" in (resp.error or "")


# --- rag.query ---


class TestRagQuery:
    def test_returns_chunks_with_sources(self):
        reg = get_default_tool_registry(rag=FakeRAG())
        resp = execute_tool(reg, "rag.query", {"notebook_id": "nb-1", "query": "hello"})
        assert resp.ok
        assert len(resp.data["results"]) == 3
        # extract_sources shape for the Q32 SSE event, deduped.
        assert sorted(resp.data["sources"], key=lambda s: s["source"]) == [
            {"source": "f.pdf", "section": "H1"},
            {"source": "g.pdf", "section": "H2"},
        ]
        assert "[1 (f.pdf)]" in (resp.output or "")

    def test_notebook_id_scoped_and_top_k_default(self):
        fake = FakeRAG()
        reg = get_default_tool_registry(rag=fake)
        execute_tool(reg, "rag.query", {"notebook_id": "nb-9", "query": "q"})
        assert fake.seen and fake.seen[0][0] == "nb-9" and fake.seen[0][2] == 8

    def test_missing_notebook_id_fails_honest(self):
        reg = get_default_tool_registry(rag=FakeRAG())
        resp = execute_tool(reg, "rag.query", {"query": "hello"})
        assert not resp.ok and "notebook_id" in (resp.error or "")

    def test_missing_query_fails_honest(self):
        reg = get_default_tool_registry(rag=FakeRAG())
        resp = execute_tool(reg, "rag.query", {"notebook_id": "nb-1"})
        assert not resp.ok and "query" in (resp.error or "")

    def test_unbound_singleton_fails_honest(self):
        from app.tools import rag_query as rq_mod

        old = rq_mod._rag_singleton
        rq_mod._rag_singleton = None
        try:
            tool = RagQueryTool()
            req = ToolRequest(
                tool_id="rag.query", input={"notebook_id": "nb", "query": "q"}
            )
            resp = tool.execute(req)
            assert not resp.ok
        finally:
            rq_mod._rag_singleton = old

    def test_module_function_uses_injected_rag(self):
        fake = FakeRAG()
        out = rag_query("nb-1", "hello", rag=fake)
        assert len(out) == 3 and out[0]["source"] == "f.pdf"

    def test_module_singleton_binding(self):
        fake = FakeRAG()
        bind_rag_singleton(fake)
        try:
            assert len(rag_query("nb-1", "hello")) == 3
        finally:
            bind_rag_singleton(None)  # type: ignore[arg-type]


# --- plot.chart ---


class TestPlotChart:
    def test_bar_renders_svg(self):
        reg = get_default_tool_registry()
        resp = execute_tool(
            reg, "plot.chart",
            {"chart_type": "bar", "labels": ["a", "b"], "values": [1, 2], "title": "T"},
        )
        assert resp.ok
        assert resp.output and resp.output.startswith("<svg")
        assert resp.data["point_count"] == 2

    def test_line_renders_svg(self):
        reg = get_default_tool_registry()
        resp = execute_tool(
            reg, "plot.chart",
            {"chart_type": "line", "labels": ["a", "b"], "values": [1, 2]},
        )
        assert resp.ok and "<polyline" in (resp.output or "")

    def test_bad_chart_type_rejected(self):
        reg = get_default_tool_registry()
        resp = execute_tool(
            reg, "plot.chart",
            {"chart_type": "pie", "labels": ["a"], "values": [1]},
        )
        assert not resp.ok


# --- doc.generate (live libs) ---


class TestDocGenerate:
    def test_renders_all_formats(self):
        reg = get_default_tool_registry()
        resp = execute_tool(
            reg, "doc.generate",
            {"title": "T", "sections": [{"heading": "H", "body": "B"}]},
        )
        assert resp.ok
        assert (resp.output or "").startswith("# T")
        assert resp.data["docx_b64"] and resp.data["pdf_b64"]

    def test_missing_title_rejected(self):
        reg = get_default_tool_registry()
        resp = execute_tool(reg, "doc.generate", {"sections": []})
        assert not resp.ok


# --- code.sandbox (live docker) ---


@needs_docker
class TestCodeSandbox:
    def test_print_round_trip(self):
        reg = get_default_tool_registry()
        resp = execute_tool(reg, "code.sandbox", {"code": "print(6*7)"})
        assert resp.ok and (resp.output or "").strip() == "42"

    def test_nonzero_exit_is_failure(self):
        reg = get_default_tool_registry()
        resp = execute_tool(reg, "code.sandbox", {"code": "raise SystemExit(3)"})
        assert not resp.ok and "exit code 3" in (resp.error or "")

    def test_missing_code_rejected(self):
        reg = get_default_tool_registry()
        resp = execute_tool(reg, "code.sandbox", {})
        assert not resp.ok


# --- image.generate ---


class TestImageGenerate:
    def test_unbound_fails_honest(self):
        reg = get_default_tool_registry()
        resp = execute_tool(reg, "image.generate", {"message": "a cat"})
        assert not resp.ok and "no provider bound" in (resp.error or "")

    def test_provider_without_capability_fails_honest(self):
        from unittest.mock import MagicMock

        from app.tools.image_generate import ImageGenerateTool

        provider = MagicMock()
        provider.generate_image.side_effect = NotImplementedError("no image model")
        tool = ImageGenerateTool(provider=provider)
        resp = tool.execute(
            ToolRequest(tool_id="image.generate", input={"message": "a cat"})
        )
        assert not resp.ok and "no image model" in (resp.error or "")

    def test_success_path_returns_b64(self):
        from app.tools.image_generate import ImageGenerateTool

        class FakeProvider:
            def generate_image(self, prompt: str):
                return "image/png", b"\x89PNG" + prompt.encode()

        tool = ImageGenerateTool(provider=FakeProvider())  # type: ignore[arg-type]
        resp = tool.execute(
            ToolRequest(tool_id="image.generate", input={"message": "a cat"})
        )
        assert resp.ok and resp.data["byte_count"] == 9
