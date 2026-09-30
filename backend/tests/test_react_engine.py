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
from app.orchestration.react_engine import _default_react_mode


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
