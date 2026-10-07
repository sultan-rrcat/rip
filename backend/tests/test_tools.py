"""Phase 3.1 — tools: registry/executor + all 5 tools.

Live where possible (plot = stdlib, doc = installed libs); fakes where
live deps are unavailable (rag.query uses an injected fake — real
VectorRAG needs the torch env repair from Phase 6).
Run with ``pytest backend/tests/test_tools.py -q --noconftest`` until the
torch env is repaired — the shared conftest imports app.main, which needs
sentence_transformers.
"""

from __future__ import annotations

import pytest
from app.tools.base import Tool, ToolRequest, ToolResponse
from app.tools.executor import execute_tool
from app.tools.rag_query import RagQueryTool, bind_rag_singleton, rag_query
from app.tools.registry import ToolRegistry, get_default_tool_registry

EXPECTED_IDS = [
    "code.read",
    "doc.convert",
    "doc.generate",
    "notebook.inspect",
    "plot.chart",
    "rag.query",
]


class FakeRAG:
    """Stand-in for VectorRAG (same retrieve_context shape)."""

    def __init__(self, results: list[dict] | None = None):
        self.seen: list[tuple] = []
        self.results = results if results is not None else [
            {"content": "c1", "source": "f.pdf", "section": "H1", "rerank_score": 0.9},
            {"content": "c2", "source": "f.pdf", "section": "H1", "rerank_score": 0.8},
            {"content": "c3", "source": "g.pdf", "section": "H2", "rerank_score": 0.7},
        ]

    def retrieve_context(
        self, notebook_id, query, top_k=4, file_id=None, file_name=None, mode="specific"
    ):
        self.seen.append((notebook_id, query, top_k, file_id, mode))
        return {"query": query, "mode": mode, "results": list(self.results)}


class WholeFileRAG(FakeRAG):
    """FakeRAG that also implements the whole-file shortcut."""

    #: Sentinel: "no override given" so an explicit `whole=None` (shortcut
    #: declines) stays distinguishable from the default whole-file hit.
    _DEFAULT = object()

    def __init__(self, whole=_DEFAULT):
        super().__init__()
        self._whole = (
            [
                {"content": "whole a", "source": "f.pdf", "section": "H1", "rerank_score": None},
                {"content": "whole b", "source": "f.pdf", "section": "H1", "rerank_score": None},
                {"content": "whole c", "source": "f.pdf", "section": "H2", "rerank_score": None},
            ]
            if whole is self._DEFAULT
            else whole
        )
        self.ranked_calls = 0
        self.whole_file_calls: list[tuple] = []

    def retrieve_whole_file(self, notebook_id, *, file_id=None, file_name=None):
        self.whole_file_calls.append((notebook_id, file_id, file_name))
        return self._whole

    def retrieve_context(self, *args, **kwargs):
        self.ranked_calls += 1
        return super().retrieve_context(*args, **kwargs)


class SpyProvider:
    """Counts planner invocations; returns one sub-query."""

    def __init__(self):
        self.calls = 0

    def generate_structured(self, model, messages, schema, temperature=0, timeout_ms=None, cancel_event=None):
        self.calls += 1
        return {"queries": ["sub one"]}


# --- Registry / executor ---


class TestRegistry:
    def test_all_five_registered(self):
        reg = get_default_tool_registry(rag=FakeRAG())
        assert sorted(t["tool_id"] for t in reg.manifest()) == EXPECTED_IDS
        assert len(reg) == 6

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
        assert fake.seen and fake.seen[0][0] == "nb-9" and fake.seen[0][2] == 4
        assert fake.seen[0][4] == "specific"

    def test_file_id_and_mode_forwarded(self):
        fake = FakeRAG()
        reg = get_default_tool_registry(rag=fake)
        resp = execute_tool(
            reg,
            "rag.query",
            {"notebook_id": "nb-1", "query": "q", "file_id": "fid-1", "mode": "overview"},
        )
        assert resp.ok
        assert fake.seen[0][3] == "fid-1" and fake.seen[0][4] == "overview"
        assert resp.data["file_id"] == "fid-1" and resp.data["mode"] == "overview"

    def test_bad_mode_rejected(self):
        reg = get_default_tool_registry(rag=FakeRAG())
        resp = execute_tool(
            reg, "rag.query", {"notebook_id": "nb-1", "query": "q", "mode": "bogus"}
        )
        assert not resp.ok and "mode" in (resp.error or "")

    def test_placeholder_file_id_rejected(self):
        reg = get_default_tool_registry(rag=FakeRAG())
        resp = execute_tool(
            reg, "rag.query", {"notebook_id": "nb-1", "query": "q", "file_id": "{{1}}"}
        )
        assert not resp.ok and "placeholder" in (resp.error or "")

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

    def test_whole_file_shortcut_skips_ranked_retrieval(self):
        """Whole-file hit returns every chunk and never calls the planner."""
        rag = WholeFileRAG()
        provider = SpyProvider()
        tool = RagQueryTool(rag=rag, provider=provider)
        resp = tool.execute(
            ToolRequest(tool_id="rag.query", input={"notebook_id": "nb-1", "query": "hi"})
        )
        assert resp.ok
        assert len(resp.data["results"]) == 3
        assert resp.data["whole_file"] is True
        assert [r["content"] for r in resp.data["results"]] == [
            "whole a",
            "whole b",
            "whole c",
        ]
        # The whole point: no embed/vector/FTS/RRF/rerank, no planner LLM call.
        assert rag.ranked_calls == 0
        assert provider.calls == 0
        assert resp.data["generated_queries"] == ["hi"]
        # Sources still carry every chunk's section (SSE `sources` stays
        # intact). `extract_sources` dedupes via a set, so order is not
        # guaranteed — compare as a set.
        assert {tuple(sorted(s.items())) for s in resp.data["sources"]} == {
            (("section", "H1"), ("source", "f.pdf")),
            (("section", "H2"), ("source", "f.pdf")),
        }
        assert "whole a" in resp.output

    def test_whole_file_shortcut_scopes_to_file_id(self):
        rag = WholeFileRAG()
        tool = RagQueryTool(rag=rag)
        tool.execute(
            ToolRequest(
                tool_id="rag.query",
                input={"notebook_id": "nb-1", "query": "hi", "file_id": "f1"},
            )
        )
        assert rag.whole_file_calls == [("nb-1", "f1", None)]

    def test_shortcut_disabled_falls_back_to_ranked(self):
        """None (over budget) keeps today's path exactly."""
        rag = WholeFileRAG(whole=None)
        provider = SpyProvider()
        tool = RagQueryTool(rag=rag, provider=provider)
        resp = tool.execute(
            ToolRequest(tool_id="rag.query", input={"notebook_id": "nb-1", "query": "hi"})
        )
        assert resp.ok
        assert resp.data["whole_file"] is False
        assert rag.ranked_calls == 1
        assert provider.calls == 1

    def test_shortcut_error_does_not_fail_retrieval(self):
        """A raising shortcut must not fail the tool."""

        class BoomRAG(WholeFileRAG):
            def retrieve_whole_file(self, notebook_id, *, file_id=None, file_name=None):
                raise RuntimeError("db down")

        tool = RagQueryTool(rag=BoomRAG())
        resp = tool.execute(
            ToolRequest(tool_id="rag.query", input={"notebook_id": "nb-1", "query": "hi"})
        )
        assert resp.ok
        assert resp.data["whole_file"] is False

    def test_legacy_double_without_shortcut_still_runs(self):
        """Plain FakeRAG exposes no retrieve_whole_file (back-compat)."""
        rag = FakeRAG()
        tool = RagQueryTool(rag=rag)
        resp = tool.execute(
            ToolRequest(tool_id="rag.query", input={"notebook_id": "nb-1", "query": "hi"})
        )
        assert resp.ok
        assert resp.data["whole_file"] is False
        assert rag.seen == [("nb-1", "hi", 4, None, "specific")]

    def test_module_singleton_binding(self):
        fake = FakeRAG()
        bind_rag_singleton(fake)
        try:
            assert len(rag_query("nb-1", "hello")) == 3
        finally:
            bind_rag_singleton(None)  # type: ignore[arg-type]


class FilterProvider:
    """Fake provider serving both the sub-query planner and the filter.

    Dispatches on the schema: `quotes` properties get the canned verbatim
    quotes, anything else gets one sub-query. Counts only filter calls.
    """

    def __init__(self, quotes: list[str] | None = None, fail: bool = False):
        self.quotes = quotes if quotes is not None else []
        self.fail = fail
        self.filter_calls = 0
        self.seen_timeout_ms: list = []

    def generate_structured(self, model, messages, schema, temperature=0, timeout_ms=None, cancel_event=None):
        if "quotes" in (schema.get("properties") or {}):
            self.filter_calls += 1
            self.seen_timeout_ms.append(timeout_ms)
            if self.fail:
                raise RuntimeError("filter llm down")
            return {"quotes": list(self.quotes)}
        return {"queries": ["sub one"]}


def _big_results(n: int = 3, size: int = 5000, tag: str = "chunk") -> list[dict]:
    """Results whose formatted output exceeds the filter threshold."""
    return [
        {
            "content": f"{tag}-{i} " + ("x" * size),
            "source": "f.pdf",
            "section": "H1",
            "rerank_score": 0.9,
        }
        for i in range(n)
    ]


class TestRelevanceFilter:
    def test_small_results_skip_filter_without_provider_call(self):
        provider = FilterProvider(quotes=["c1"])
        tool = RagQueryTool(rag=FakeRAG(), provider=provider)
        resp = tool.execute(
            ToolRequest(tool_id="rag.query", input={"notebook_id": "nb-1", "query": "q"})
        )
        assert resp.ok
        assert provider.filter_calls == 0
        assert resp.data["filtered"] is False
        assert resp.data["filter_reason"] == "under_threshold"
        assert len(resp.data["results"]) == 3

    def test_large_results_filtered_by_default(self):
        big = _big_results()
        rag = WholeFileRAG(whole=big)
        provider = FilterProvider(quotes=[big[1]["content"]])
        tool = RagQueryTool(rag=rag, provider=provider)
        resp = tool.execute(
            ToolRequest(tool_id="rag.query", input={"notebook_id": "nb-1", "query": "q"})
        )
        assert resp.ok
        assert provider.filter_calls == 1
        assert resp.data["filtered"] is True
        # Subset only, original order preserved, sources consistent.
        assert [r["content"] for r in resp.data["results"]] == [big[1]["content"]]
        assert "chunk-1" in (resp.output or "")
        assert "chunk-0" not in (resp.output or "")

    def test_verbatim_true_skips_filter(self):
        big = _big_results()
        rag = WholeFileRAG(whole=big)
        provider = FilterProvider(quotes=[big[1]["content"]])
        tool = RagQueryTool(rag=rag, provider=provider)
        resp = tool.execute(
            ToolRequest(
                tool_id="rag.query",
                input={"notebook_id": "nb-1", "query": "q", "verbatim": True},
            )
        )
        assert resp.ok
        assert provider.filter_calls == 0
        assert resp.data["filtered"] is False
        assert resp.data["filter_reason"] == "not_requested"
        assert len(resp.data["results"]) == 3

    def test_filter_uses_tight_deadline(self):
        from app.core.config import settings

        big = _big_results()
        provider = FilterProvider(quotes=[big[0]["content"]])
        tool = RagQueryTool(rag=WholeFileRAG(whole=big), provider=provider)
        tool.execute(
            ToolRequest(tool_id="rag.query", input={"notebook_id": "nb-1", "query": "q"})
        )
        assert provider.seen_timeout_ms == [settings.planner_timeout_ms]

    def test_filter_failure_falls_back_unfiltered(self):
        big = _big_results()
        provider = FilterProvider(fail=True)
        tool = RagQueryTool(rag=WholeFileRAG(whole=big), provider=provider)
        resp = tool.execute(
            ToolRequest(tool_id="rag.query", input={"notebook_id": "nb-1", "query": "q"})
        )
        assert resp.ok
        assert resp.data["filtered"] is False
        assert resp.data["filter_reason"] == "filter_failed"
        assert len(resp.data["results"]) == 3

    def test_filter_no_provider_falls_back_unfiltered(self):
        big = _big_results()
        tool = RagQueryTool(rag=WholeFileRAG(whole=big))
        resp = tool.execute(
            ToolRequest(tool_id="rag.query", input={"notebook_id": "nb-1", "query": "q"})
        )
        assert resp.ok
        assert resp.data["filtered"] is False
        assert resp.data["filter_reason"] == "no_provider"
        assert len(resp.data["results"]) == 3

    def test_filter_empty_quotes_falls_back_unfiltered(self):
        big = _big_results()
        provider = FilterProvider(quotes=[])
        tool = RagQueryTool(rag=WholeFileRAG(whole=big), provider=provider)
        resp = tool.execute(
            ToolRequest(tool_id="rag.query", input={"notebook_id": "nb-1", "query": "q"})
        )
        assert resp.ok
        assert resp.data["filtered"] is False
        assert resp.data["filter_reason"] == "nothing_mapped"
        assert len(resp.data["results"]) == 3

    def test_filter_paraphrase_matches_nothing(self):
        big = _big_results()
        provider = FilterProvider(quotes=["a completely unrelated sentence"])
        tool = RagQueryTool(rag=WholeFileRAG(whole=big), provider=provider)
        resp = tool.execute(
            ToolRequest(tool_id="rag.query", input={"notebook_id": "nb-1", "query": "q"})
        )
        assert resp.ok and resp.data["filtered"] is False
        assert len(resp.data["results"]) == 3

    def test_filter_partial_quote_matches_source(self):
        from app.tools.rag_query import _map_quotes_to_results

        results = [
            {"content": "alpha beta gamma delta", "source": "f.pdf"},
            {"content": "one two three four", "source": "f.pdf"},
        ]
        # Partial quote still matches its source chunk, in doc order.
        assert _map_quotes_to_results(["beta gamma"], results) == [results[0]]
        # Paraphrase matches nothing.
        assert _map_quotes_to_results(["something else entirely"], results) == []
        # Non-list / empty input matches nothing (never raises).
        assert _map_quotes_to_results("alpha", results) == []
        assert _map_quotes_to_results([], results) == []
        assert _map_quotes_to_results(None, results) == []

    def test_ranked_path_filters_large_merge(self):
        """The filter also applies past the whole-file shortcut."""

        class _BigRanked(FakeRAG):
            def __init__(self):
                super().__init__(results=_big_results(n=3))

        big = _BigRanked().results
        provider = FilterProvider(quotes=[big[0]["content"]])
        tool = RagQueryTool(rag=_BigRanked(), provider=provider)
        resp = tool.execute(
            ToolRequest(tool_id="rag.query", input={"notebook_id": "nb-1", "query": "q"})
        )
        assert resp.ok and resp.data["whole_file"] is False
        assert resp.data["filtered"] is True
        assert [r["content"] for r in resp.data["results"]] == [big[0]["content"]]


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

    def test_csv_string_values_split(self):
        # Placeholder-resolved whole-text output arrives as ONE string.
        reg = get_default_tool_registry()
        resp = execute_tool(
            reg, "plot.chart",
            {"chart_type": "bar", "labels": ["a", "b"], "values": ["0.82, 0.88"]},
        )
        assert resp.ok and resp.data["point_count"] == 2

    def test_stray_commas_tolerated(self):
        reg = get_default_tool_registry()
        resp = execute_tool(
            reg, "plot.chart",
            {"chart_type": "bar", "labels": ["a", "b"], "values": [",0.82,", "0.88,"]},
        )
        assert resp.ok and resp.data["point_count"] == 2

    def test_garbage_values_still_rejected(self):
        reg = get_default_tool_registry()
        resp = execute_tool(
            reg, "plot.chart",
            {"chart_type": "bar", "labels": ["a"], "values": ["not a number"]},
        )
        assert not resp.ok and "must all be numbers" in (resp.error or "")

    def test_length_checked_after_split(self):
        reg = get_default_tool_registry()
        resp = execute_tool(
            reg, "plot.chart",
            {"chart_type": "bar", "labels": ["a", "b"], "values": ["1, 2, 3"]},
        )
        assert not resp.ok and "same length" in (resp.error or "")

    def test_multiseries_line_renders_with_legend(self):
        reg = get_default_tool_registry()
        resp = execute_tool(
            reg, "plot.chart",
            {"chart_type": "line", "labels": ["2000", "2010", "2020"],
             "series": [{"label": "USA", "values": [10.0, 15.0, 21.0]},
                        {"label": "China", "values": ["1.2, 6.0, 14.7"]}],
             "title": "GDP (approximate)"},
        )
        assert resp.ok
        assert (resp.output or "").count("<polyline") == 2
        assert ">USA<" in (resp.output or "") and ">China<" in (resp.output or "")
        assert resp.data["series_count"] == 2 and resp.data["point_count"] == 3

    def test_multiseries_bar_groups(self):
        reg = get_default_tool_registry()
        resp = execute_tool(
            reg, "plot.chart",
            {"chart_type": "bar", "labels": ["a", "b"],
             "series": [{"label": "x", "values": [1, 2]},
                        {"label": "y", "values": [3, 4]}]},
        )
        assert resp.ok and (resp.output or "").startswith("<svg")

    def test_values_and_series_conflict_rejected(self):
        reg = get_default_tool_registry()
        resp = execute_tool(
            reg, "plot.chart",
            {"chart_type": "bar", "labels": ["a"], "values": [1],
             "series": [{"label": "x", "values": [1]}]},
        )
        assert not resp.ok and "never both" in (resp.error or "")

    def test_missing_values_and_series_rejected(self):
        reg = get_default_tool_registry()
        resp = execute_tool(
            reg, "plot.chart",
            {"chart_type": "bar", "labels": ["a"]},
        )
        assert not resp.ok and "non-empty array" in (resp.error or "")

    def test_series_length_mismatch_rejected(self):
        reg = get_default_tool_registry()
        resp = execute_tool(
            reg, "plot.chart",
            {"chart_type": "line", "labels": ["a", "b"],
             "series": [{"label": "x", "values": [1]}]},
        )
        assert not resp.ok and "but 2 labels" in (resp.error or "")

    def test_too_many_series_rejected(self):
        reg = get_default_tool_registry()
        resp = execute_tool(
            reg, "plot.chart",
            {"chart_type": "line", "labels": ["a"],
             "series": [{"label": f"s{i}", "values": [1]} for i in range(6)]},
        )
        assert not resp.ok and "at most 5 series" in (resp.error or "")

    def test_series_entry_without_label_rejected(self):
        reg = get_default_tool_registry()
        resp = execute_tool(
            reg, "plot.chart",
            {"chart_type": "line", "labels": ["a"],
             "series": [{"label": "", "values": [1]}]},
        )
        assert not resp.ok and "label" in (resp.error or "")

    def test_missing_title_defaults_from_labels(self):
        # Untitled calls still render a heading derived from the data
        # (trace affdbbd4: ReAct omitted title on 3 of 4 charts).
        reg = get_default_tool_registry()
        resp = execute_tool(
            reg, "plot.chart",
            {"chart_type": "bar", "labels": ["FASDD_CV", "AgniNetra"],
             "values": [0.5, 0.7]},
        )
        assert resp.ok
        assert "FASDD_CV vs AgniNetra" in (resp.output or "")

    def test_missing_title_defaults_from_series(self):
        reg = get_default_tool_registry()
        resp = execute_tool(
            reg, "plot.chart",
            {"chart_type": "bar", "labels": ["FASDD_CV", "AgniNetra"],
             "series": [{"label": "s", "values": [0.5, 0.7]},
                        {"label": "n", "values": [0.4, 0.6]}]},
        )
        assert resp.ok
        assert "s, n by FASDD_CV vs AgniNetra" in (resp.output or "")

    def test_explicit_title_kept(self):
        reg = get_default_tool_registry()
        resp = execute_tool(
            reg, "plot.chart",
            {"chart_type": "bar", "labels": ["a", "b"], "values": [1, 2],
             "title": "Custom"},
        )
        assert resp.ok and ">Custom<" in (resp.output or "")


# --- doc.generate (live libs) ---


class TestDocGenerate:
    def test_renders_all_formats(self):
        reg = get_default_tool_registry()
        resp = execute_tool(
            reg, "doc.generate",
            {"title": "T", "sections": [{"heading": "H", "body": "B"}],
             "target_format": "docx"},
        )
        assert resp.ok
        assert (resp.output or "").startswith("# T")
        assert resp.data["docx_b64"]
        resp = execute_tool(
            reg, "doc.generate",
            {"title": "T", "sections": [{"heading": "H", "body": "B"}],
             "target_format": "pdf"},
        )
        assert resp.ok
        assert resp.data["pdf_b64"]

    def test_missing_title_rejected(self):
        reg = get_default_tool_registry()
        resp = execute_tool(reg, "doc.generate", {"sections": []})
        assert not resp.ok


# --- code.read (ReAct fallback file reader, trace 987e6ceb) ---


class TestCodeRead:
    def test_registered_with_five(self):
        reg = get_default_tool_registry(rag=FakeRAG())
        assert "code.read" in reg
        assert reg.get("code.read").effect_class == "read-only"

    def test_missing_notebook_id_fails_honest(self):
        reg = get_default_tool_registry(rag=FakeRAG())
        resp = execute_tool(
            reg, "code.read", {"file_id": "b38684db-3ff3-4f05-8b8d-bab7c5545b9d"}
        )
        assert not resp.ok and "notebook_id" in (resp.error or "")

    def test_missing_identifier_fails_honest(self):
        reg = get_default_tool_registry(rag=FakeRAG())
        resp = execute_tool(reg, "code.read", {"notebook_id": "nb-1"})
        assert not resp.ok and "file_id" in (resp.error or "")

    def test_placeholder_file_id_rejected(self):
        reg = get_default_tool_registry(rag=FakeRAG())
        resp = execute_tool(
            reg, "code.read", {"notebook_id": "nb-1", "file_id": "{{1}}"}
        )
        assert not resp.ok and "placeholder" in (resp.error or "")

    def test_malformed_file_id_rejected_without_db(self):
        reg = get_default_tool_registry(rag=FakeRAG())
        resp = execute_tool(
            reg, "code.read", {"notebook_id": "nb-1", "file_id": "not-a-uuid"}
        )
        assert not resp.ok and "unknown file_id" in (resp.error or "")

    def test_invalid_notebook_id_rejected_without_db(self):
        reg = get_default_tool_registry(rag=FakeRAG())
        resp = execute_tool(
            reg,
            "code.read",
            {"notebook_id": "../evil", "file_name": "plan_graph.py"},
        )
        assert not resp.ok


# --- notebook.inspect (fallback ids must ride the text, trace 987e6ceb) ---


class TestNotebookInspectFormat:
    def test_listing_carries_literal_ids(self):
        from app.tools.notebook_inspect import _format_files

        out = _format_files([
            {"file_name": "plan_graph.py", "file_status": "ready:code",
             "file_id": "fid-1"},
        ])
        assert "plan_graph.py" in out
        assert "id=fid-1" in out
