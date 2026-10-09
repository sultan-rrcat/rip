"""Unit tests for the deepened ReAct fallback modules.

Covers the new seams introduced by the ReAct deepening:
- `idle_guard.IdleGuard` — the extracted idle-turn guard
- `corpus.get_corpus_state` — the shared corpus tri-state
- `react_engine._default_react_mode` — purity (never mutates input)

The full loop is covered through the `run_react` wrapper in
`test_layered.py`; these tests pin the new module interfaces directly.
"""

from app.orchestration.corpus import _ready_files, _snapshot_files, get_corpus_state
from app.orchestration.idle_guard import MAX_IDLE_TURNS, IdleGuard
from app.orchestration.react_engine import (
    _default_react_mode,
    _is_empty_file_claim,
    _validate_react_input,
)


class TestIdleGuard:
    def test_breaks_at_threshold(self) -> None:
        guard = IdleGuard()
        assert guard.record_idle() is False
        assert guard.record_idle() is True

    def test_progress_resets_counter(self) -> None:
        guard = IdleGuard()
        assert guard.record_idle() is False
        guard.record_progress()
        assert guard.idle_turns == 0
        assert guard.record_idle() is False
        assert guard.record_idle() is True

    def test_custom_threshold(self) -> None:
        guard = IdleGuard(max_idle_turns=3)
        assert guard.record_idle() is False
        assert guard.record_idle() is False
        assert guard.record_idle() is True

    def test_default_threshold_is_two(self) -> None:
        assert MAX_IDLE_TURNS == 2
        assert IdleGuard().record_idle() is False


class TestCorpusState:
    def test_none_is_unknown(self) -> None:
        assert get_corpus_state(None) == "unknown"

    def test_no_files_is_empty(self) -> None:
        assert get_corpus_state("") == "empty"
        assert get_corpus_state("(no documents)") == "empty"

    def test_ready_file_is_ready(self) -> None:
        ctx = "report.pdf [ready] id=abc123"
        assert get_corpus_state(ctx) == "ready"

    def test_processing_only_is_processing(self) -> None:
        ctx = "report.pdf [processing] id=abc123"
        assert get_corpus_state(ctx) == "processing"

    def test_mixed_ready_and_processing_is_ready(self) -> None:
        ctx = "a.pdf [processing] id=aaa\nb.pdf [ready] id=bbb"
        assert get_corpus_state(ctx) == "ready"

    def test_snapshot_parsing(self) -> None:
        files = _snapshot_files("report.pdf [ready] id=abc123;,")
        assert files == [("report.pdf", "ready", "abc123")]
        assert _ready_files("report.pdf [ready] id=abc123") == [
            ("report.pdf", "abc123")
        ]
        assert _ready_files("report.pdf [processing] id=abc123") == []

    def test_snapshot_strips_count_prefix(self) -> None:
        # Manager format "N file(s): ...": the first filename must not
        # carry the count prefix (observed live: "File 1 file(s): memory.py").
        ctx = (
            "2 file(s): app.py [ready:code] "
            "id=11111111-1111-1111-1111-111111111111; "
            "doc.pdf [ready] id=22222222-2222-2222-2222-222222222222"
        )
        assert _snapshot_files(ctx) == [
            ("app.py", "ready:code", "11111111-1111-1111-1111-111111111111"),
            ("doc.pdf", "ready", "22222222-2222-2222-2222-222222222222"),
        ]


class TestDefaultReactModePurity:
    def test_does_not_mutate_input(self) -> None:
        action_input: dict = {"query": "summarize the docs"}
        result = _default_react_mode("summarize the documents", action_input)
        assert result["mode"] == "overview"
        assert "mode" not in action_input

    def test_leaves_specific_default_alone(self) -> None:
        action_input: dict = {"query": "what is the total?"}
        result = _default_react_mode("what is the total?", action_input)
        assert "mode" not in result

    def test_respects_explicit_mode(self) -> None:
        action_input: dict = {"query": "x", "mode": "specific"}
        assert _default_react_mode("summarize everything", action_input) == action_input


class TestCodeReadValidation:
    """code.read is the ReAct fallback file reader (trace 987e6ceb)."""

    def test_missing_identifiers_rejected(self) -> None:
        hint = _validate_react_input("code.read", {})
        assert hint is not None and "file_id" in hint

    def test_file_id_accepted(self) -> None:
        assert (
            _validate_react_input(
                "code.read",
                {"file_id": "b38684db-3ff3-4f05-8b8d-bab7c5545b9d"},
            )
            is None
        )

    def test_file_name_accepted(self) -> None:
        assert (
            _validate_react_input("code.read", {"file_name": "plan_graph.py"})
            is None
        )

    def test_placeholder_file_id_rejected(self) -> None:
        hint = _validate_react_input("code.read", {"file_id": "{{1}}"})
        assert hint is not None and "placeholder" in hint

    def test_empty_notebook_points_at_coding_not_file_id(self) -> None:
        # Trace c1bbae95: with no files, demanding a file_id is a dead end —
        # it burned both idle turns and killed the run. Point at coding.
        hint = _validate_react_input("code.read", {}, has_files=False)
        assert hint is not None
        assert "NO files" in hint and "coding" in hint
        assert "file_id" not in hint

    def test_populated_notebook_keeps_file_id_hint(self) -> None:
        hint = _validate_react_input("code.read", {}, has_files=True)
        assert hint is not None and "file_id" in hint

    def test_named_file_on_empty_notebook_refused(self) -> None:
        # Live "hii" trace: the model named README.md on a file-less
        # notebook, validation passed on presence alone, execution failed
        # and burned a step. Presence must not bypass the empty guard.
        hint = _validate_react_input(
            "code.read", {"file_name": "README.md"}, has_files=False
        )
        assert hint is not None and "NO files" in hint

    def test_named_file_on_populated_notebook_passes(self) -> None:
        assert (
            _validate_react_input(
                "code.read", {"file_name": "README.md"}, has_files=True
            )
            is None
        )


class TestEmptyNotebookToolGuards:
    """File-dependent tools are dead ends with no files — refuse pre-flight."""

    def test_doc_convert_on_empty_notebook_refused(self) -> None:
        hint = _validate_react_input(
            "doc.convert",
            {"file_id": "abc123", "target_format": "pdf"},
            has_files=False,
        )
        assert hint is not None and "NO files" in hint

    def test_doc_convert_with_files_validates_shape(self) -> None:
        assert (
            _validate_react_input(
                "doc.convert",
                {"file_id": "abc123", "target_format": "pdf"},
                has_files=True,
            )
            is None
        )
        hint = _validate_react_input(
            "doc.convert", {"file_id": "abc123"}, has_files=True
        )
        assert hint is not None and "target_format" in hint


class TestRemapMisnamedCoding:
    """A coding call filed under code.read must still execute (trace c1bbae95)."""

    def test_code_read_with_message_becomes_coding(self) -> None:
        from app.orchestration.react_engine import _remap_executor

        action_input = {"message": "Create a self-contained HTML page..."}
        assert _remap_executor("code.read", action_input) == "coding"

    def test_code_read_with_null_file_name_becomes_coding(self) -> None:
        # The exact shape trace c1bbae95 emitted: file_name=null + message.
        from app.orchestration.react_engine import _remap_executor

        action_input = {"message": "build the page", "file_name": None}
        assert _remap_executor("code.read", action_input) == "coding"

    def test_real_code_read_is_untouched(self) -> None:
        from app.orchestration.react_engine import _remap_executor

        action_input = {
            "file_id": "b38684db-3ff3-4f05-8b8d-bab7c5545b9d",
            "message": "explain this",
        }
        assert _remap_executor("code.read", action_input) == "code.read"

    def test_other_executors_untouched(self) -> None:
        from app.orchestration.react_engine import _remap_executor

        assert _remap_executor("coding", {"message": "x"}) == "coding"
        assert _remap_executor("plot.chart", {"values": [1]}) == "plot.chart"


class TestEmptyFileClaim:
    """Agent 'I can't inspect the file' successes are futile (trace 987e6ceb)."""

    def test_detects_missing_content_claims(self) -> None:
        assert _is_empty_file_claim(
            "I can't inspect that file because no content was attached "
            "to your message"
        )
        assert _is_empty_file_claim("There's no file content available for me to read")

    def test_real_answers_are_not_claims(self) -> None:
        assert not _is_empty_file_claim("Here is the test script for plan_graph.py")
        assert not _is_empty_file_claim("")
