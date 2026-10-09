"""Unit tests for the shared placeholder + fenced-code scanning helpers.

These tests pin the contract: fenced code blocks are stripped before
placeholder scans, so literal `{{...}}` source text is never wired.
"""

from __future__ import annotations

from app.orchestration.placeholder import (
    FENCED_CODE,
    PLACEHOLDER,
    has_unresolved_placeholder,
    placeholders_outside_code,
    strip_fenced_code,
)


class TestStripFencedCode:
    def test_removes_fenced_blocks(self) -> None:
        text = "before ```code {{1}}``` after"
        assert strip_fenced_code(text) == "before  after"

    def test_preserves_text_outside_fences(self) -> None:
        text = "see {{1}} here"
        assert strip_fenced_code(text) == text

    def test_handles_multiple_fences(self) -> None:
        text = "a ```{{1}}``` b ```{{2}}``` c"
        assert strip_fenced_code(text) == "a  b  c"

    def test_handles_empty_string(self) -> None:
        assert strip_fenced_code("") == ""

    def test_handles_no_fences(self) -> None:
        text = "no fences here"
        assert strip_fenced_code(text) == text


class TestPlaceholdersOutsideCode:
    def test_finds_placeholder_in_string(self) -> None:
        assert placeholders_outside_code("see {{1}} now") == {"1"}

    def test_ignores_placeholder_in_fenced_code(self) -> None:
        assert placeholders_outside_code("```jinja\n{{ variable }}\n```") == set()

    def test_finds_placeholder_outside_fenced_code(self) -> None:
        text = "see {{1}} and ```code {{2}}``` and {{3}}"
        assert placeholders_outside_code(text) == {"1", "3"}

    def test_recurses_into_dict(self) -> None:
        value = {"a": "{{1}}", "b": {"c": "{{2}}"}}
        assert placeholders_outside_code(value) == {"1", "2"}

    def test_recurses_into_list(self) -> None:
        value = ["{{1}}", "{{2}}", {"a": "{{3}}"}]
        assert placeholders_outside_code(value) == {"1", "2", "3"}

    def test_handles_empty_string(self) -> None:
        assert placeholders_outside_code("") == set()

    def test_handles_none(self) -> None:
        assert placeholders_outside_code(None) == set()

    def test_handles_int(self) -> None:
        assert placeholders_outside_code(42) == set()


class TestHasUnresolvedPlaceholder:
    def test_true_for_unresolved(self) -> None:
        assert has_unresolved_placeholder("see {{1}} now") is True

    def test_false_for_fenced(self) -> None:
        assert has_unresolved_placeholder("```jinja\n{{ variable }}\n```") is False

    def test_false_for_none(self) -> None:
        assert has_unresolved_placeholder(None) is False

    def test_false_for_empty(self) -> None:
        assert has_unresolved_placeholder("") is False

    def test_true_for_mixed(self) -> None:
        assert has_unresolved_placeholder("see {{1}} and ```code {{2}}```") is True


class TestRegexPatterns:
    def test_placeholder_matches_simple(self) -> None:
        assert PLACEHOLDER.findall("{{1}}") == ["1"]

    def test_placeholder_matches_with_spaces(self) -> None:
        assert PLACEHOLDER.findall("{{ 1 }}") == ["1"]

    def test_placeholder_matches_underscore(self) -> None:
        assert PLACEHOLDER.findall("{{step_1}}") == ["step_1"]

    def test_fenced_code_matches(self) -> None:
        assert FENCED_CODE.findall("```code```") == ["```code```"]

    def test_fenced_code_matches_multiline(self) -> None:
        assert FENCED_CODE.findall("```\ncode\n```") == ["```\ncode\n```"]