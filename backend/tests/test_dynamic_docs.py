"""V2(B) — notebook.inspect + doc.convert + planner doc-awareness.

Fake-heavy (no DB, no Docling, no Ollama): pg_connection and the
loader/render boundaries are monkeypatched.
"""

from __future__ import annotations

from contextlib import contextmanager

from app.agents.base import StepStatus
from app.artifacts import collect_artifacts
from app.orchestration.results import StepResult
from app.tools.base import ToolRequest
from app.tools.doc_convert import DocConvertTool
from app.tools.notebook_inspect import NotebookInspectTool
from app.tools.registry import get_default_tool_registry


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        return None

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self, *a, **k):
        return _FakeCursor(self._rows)


class TestNotebookInspect:
    def test_lists_files(self):
        import app.core.db as db_mod

        rows = [
            ("fid-1", "a.pdf", 10, "ready"),
            ("fid-2", "b.pdf", 20, "processing"),
        ]
        real = db_mod.pg_connection

        @contextmanager
        def fake():
            yield _FakeConn(rows)

        db_mod.pg_connection = fake
        try:
            resp = NotebookInspectTool().execute(
                ToolRequest(tool_id="notebook.inspect", input={"notebook_id": "nb-1"})
            )
        finally:
            db_mod.pg_connection = real
        assert resp.ok
        assert len(resp.data["files"]) == 2
        assert resp.data["files"][0]["file_id"] == "fid-1"
        assert "a.pdf" in (resp.output or "")

    def test_missing_notebook_id(self):
        resp = NotebookInspectTool().execute(
            ToolRequest(tool_id="notebook.inspect", input={})
        )
        assert not resp.ok and "notebook_id" in (resp.error or "")

    def test_registered(self):
        reg = get_default_tool_registry(rag=object())
        assert "notebook.inspect" in reg
        assert "doc.convert" in reg
        assert len(reg) == 7


class TestDocConvert:
    def _tool(self):
        return DocConvertTool()

    def test_missing_file_id(self):
        resp = self._tool().execute(
            ToolRequest(
                tool_id="doc.convert",
                input={"notebook_id": "nb", "target_format": "md"},
            )
        )
        assert not resp.ok and "file_id" in (resp.error or "")

    def test_bad_target_format(self):
        resp = self._tool().execute(
            ToolRequest(
                tool_id="doc.convert",
                input={"notebook_id": "nb", "file_id": "f", "target_format": "txt"},
            )
        )
        assert not resp.ok and "target_format" in (resp.error or "")

    def test_unknown_file_id(self, monkeypatch):
        import app.tools.doc_convert as dc_mod

        monkeypatch.setattr(
            dc_mod, "_resolve_source", lambda nb, fid: (_ for _ in ()).throw(ValueError("unknown file_id: x"))
        )
        resp = self._tool().execute(
            ToolRequest(
                tool_id="doc.convert",
                input={"notebook_id": "nb", "file_id": "x", "target_format": "md"},
            )
        )
        assert not resp.ok and "unknown file_id" in (resp.error or "")

    def test_md_lossless(self, monkeypatch):
        import app.tools.doc_convert as dc_mod

        monkeypatch.setattr(
            dc_mod, "_resolve_source", lambda nb, fid: ("/tmp/a.pdf", "a.pdf", ".pdf")
        )
        monkeypatch.setattr(dc_mod, "_load_markdown", lambda path, ext: "# T\n\nbody text")
        resp = self._tool().execute(
            ToolRequest(
                tool_id="doc.convert",
                input={"notebook_id": "nb", "file_id": "fid-1", "target_format": "md"},
            )
        )
        assert resp.ok
        assert resp.output == "# T\n\nbody text"
        assert resp.data["source_file_id"] == "fid-1"
        assert resp.data["target_format"] == "md"

    def test_docx_target(self, monkeypatch):
        import app.tools.doc_convert as dc_mod

        monkeypatch.setattr(
            dc_mod, "_resolve_source", lambda nb, fid: ("/tmp/a.pdf", "a.pdf", ".pdf")
        )
        monkeypatch.setattr(dc_mod, "_load_markdown", lambda path, ext: "hello")
        resp = self._tool().execute(
            ToolRequest(
                tool_id="doc.convert",
                input={"notebook_id": "nb", "file_id": "fid-1", "target_format": "docx"},
            )
        )
        assert resp.ok and resp.data.get("docx_b64")

    def test_no_rag_no_llm(self, monkeypatch):
        """Convert path touches only resolve+load+render — patch all three."""
        import app.tools.doc_convert as dc_mod

        seen = {}

        def fake_resolve(nb, fid):
            seen["resolve"] = (nb, fid)
            return ("/tmp/a.pdf", "a.pdf", ".pdf")

        def fake_load(path, ext):
            seen["load"] = path
            return "full text here"

        monkeypatch.setattr(dc_mod, "_resolve_source", fake_resolve)
        monkeypatch.setattr(dc_mod, "_load_markdown", fake_load)
        resp = self._tool().execute(
            ToolRequest(
                tool_id="doc.convert",
                input={"notebook_id": "nb", "file_id": "fid-9", "target_format": "md"},
            )
        )
        assert resp.ok and seen == {"resolve": ("nb", "fid-9"), "load": "/tmp/a.pdf"}

    def test_file_name_alias(self, monkeypatch):
        import app.tools.doc_convert as dc_mod

        monkeypatch.setattr(dc_mod, "_resolve_by_name", lambda nb, name: ("fid-1", "/tmp/a.pdf", "a.pdf", ".pdf"))
        monkeypatch.setattr(dc_mod, "_load_markdown", lambda path, ext: "txt")
        resp = self._tool().execute(
            ToolRequest(
                tool_id="doc.convert",
                input={"notebook_id": "nb", "file_name": "a.pdf", "target_format": "md"},
            )
        )
        assert resp.ok and resp.data["source_file_name"] == "a.pdf"

    def test_convert_all_star(self, monkeypatch):
        import app.tools.doc_convert as dc_mod
        import os

        def fake_list_ready(nb):
            return [("fid-1", "a.pdf", ".pdf"), ("fid-2", "b.pdf", ".pdf")]

        monkeypatch.setattr(dc_mod, "_list_ready", fake_list_ready)
        monkeypatch.setattr(dc_mod, "_disk_path", lambda nb, fid, ext: f"/tmp/{fid}{ext}")
        monkeypatch.setattr(os.path, "isfile", lambda p: True)
        def fake_convert(nb, fid, path, name, ext, target):
            return ("content", {"source_file_id": fid, "source_file_name": name, "target_format": target, "markdown": "x"})
        monkeypatch.setattr(dc_mod, "_convert_one", fake_convert)
        resp = self._tool().execute(
            ToolRequest(
                tool_id="doc.convert",
                input={"notebook_id": "nb", "file_id": "*", "target_format": "md"},
            )
        )
        assert resp.ok
        conv = resp.data.get("conversions", [])
        assert len(conv) == 2
        assert conv[0]["source_file_id"] == "fid-1"
        assert conv[1]["source_file_id"] == "fid-2"


class TestConvertArtifacts:
    def test_md_with_source_id_becomes_file(self, tmp_path):
        step = StepResult(
            step_id="2",
            agent_id="doc.convert",
            status=StepStatus.SUCCESS,
            output="# T",
            data={"markdown": "# T", "source_file_id": "fid-1"},
        )
        found = collect_artifacts(
            [step], upload_dir=str(tmp_path), notebook_id="nb", run_id="run-1"
        )
        assert any(a["filename"].endswith(".md") for a in found)

    def test_bare_markdown_without_source_stays_text(self, tmp_path):
        step = StepResult(
            step_id="2",
            agent_id="reasoning",
            status=StepStatus.SUCCESS,
            output="# T",
            data={"markdown": "# T"},
        )
        found = collect_artifacts(
            [step], upload_dir=str(tmp_path), notebook_id="nb", run_id="run-2"
        )
        assert found == []


class _FakeProvider:
    def __init__(self, plan):
        self._plan = plan
        self.seen_messages = None

    def generate_structured(self, model, messages, schema, temperature=0):
        self.seen_messages = messages
        return self._plan


class TestPlannerDocAwareness:
    def test_snapshot_rendered_in_prompt(self):
        from app.agents.registry import AgentRegistry
        from app.orchestration.planner import Planner
        from app.tools.registry import ToolRegistry

        plan_json = {
            "goal": "g",
            "steps": [{"step_id": "1", "agent_id": "reasoning", "input": {"message": "hi"}, "depends_on": []}],
        }
        provider = _FakeProvider(plan_json)
        planner = Planner(provider, AgentRegistry(), ToolRegistry())
        planner.plan("hi", notebook_context="1 file(s): a.pdf [ready] id=fid-1")
        system = provider.seen_messages[0]["content"]
        assert "a.pdf" in system
        assert "notebook.inspect" in system
        assert "doc.convert" in system

    def test_no_docs_snapshot(self):
        from app.agents.registry import AgentRegistry
        from app.orchestration.planner import Planner
        from app.tools.registry import ToolRegistry

        plan_json = {
            "goal": "g",
            "steps": [{"step_id": "1", "agent_id": "reasoning", "input": {"message": "hi"}, "depends_on": []}],
        }
        provider = _FakeProvider(plan_json)
        Planner(provider, AgentRegistry(), ToolRegistry()).plan("hi")
        assert "(no documents)" in provider.seen_messages[0]["content"]
