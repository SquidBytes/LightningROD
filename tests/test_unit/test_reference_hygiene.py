"""Unit tests for scripts.check_reference_hygiene."""

from pathlib import Path

import pytest

from scripts.check_reference_hygiene import find_violations

pytestmark = pytest.mark.unit


def test_detects_decision_token_in_comment():
    source = "# Uses D-B3 fallback\nvalue = 1\n"
    violations = find_violations(path=Path("x.py"), source=source)
    assert any(v.rule_name == "decision token" and v.line == 1 for v in violations)


def test_detects_phase_reference_in_docstring():
    source = '''"""Phase 29 behavior lock."""\n\ndef f():\n    return 1\n'''
    violations = find_violations(path=Path("x.py"), source=source)
    assert any(v.rule_name == "phase reference" and v.kind == "docstring" for v in violations)


def test_ignores_regular_string_literals():
    source = "label = 'Phase 29 chart'\n"
    violations = find_violations(path=Path("x.py"), source=source)
    assert violations == []


def test_line_filter_limits_report_scope():
    source = "# D2\n# D3\n"
    violations = find_violations(
        path=Path("x.py"),
        source=source,
        line_filter={2},
    )
    assert len(violations) == 1
    assert violations[0].line == 2
