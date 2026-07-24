"""AC5 — Content-capture convention static linter.

Reads the source of ``_tools.py`` and asserts that none of the forbidden
span attribute keys from ADR-0015 appear in any ``create_span()`` or
``set_attribute()`` call.

The files ``text2sparql/_orchestrator.py`` and ``text2sparql/_generator.py``
do not yet exist (they are future specs); this test checks their absence and
reports a skip rather than a failure so CI stays green.

Forbidden attribute keys (content-capture policy):
    question.text, query.text, sparql.query, document.content, chunk.text
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths to check
# ---------------------------------------------------------------------------

_MCP_SRC = Path(__file__).parent.parent.parent / "src/graphrag/mcp"

_TOOLS_PY = _MCP_SRC / "_tools.py"
_ORCHESTRATOR_PY = _MCP_SRC.parent / "text2sparql/_orchestrator.py"
_GENERATOR_PY = _MCP_SRC.parent / "text2sparql/_generator.py"

# Forbidden span attribute strings (ADR-0015 content-capture policy)
_FORBIDDEN_ATTRS = [
    "question.text",
    "query.text",
    "sparql.query",
    "document.content",
    "chunk.text",
]

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _assert_no_forbidden_attrs(source: str, path: Path) -> None:
    """Assert none of the forbidden attribute strings appear in the source."""
    violations: list[str] = []
    for attr in _FORBIDDEN_ATTRS:
        if attr in source:
            violations.append(attr)
    if violations:
        pytest.fail(
            f"{path}: forbidden span attribute(s) found: {violations!r}\n"
            "These strings must not appear as span attribute keys per ADR-0015."
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_tools_py_no_forbidden_content_capture_attrs() -> None:
    """_tools.py contains no forbidden content-capture span attribute keys (AC5)."""
    assert _TOOLS_PY.exists(), f"_tools.py not found at {_TOOLS_PY}"
    source = _TOOLS_PY.read_text()
    _assert_no_forbidden_attrs(source, _TOOLS_PY)


def test_orchestrator_py_no_forbidden_attrs_or_absent() -> None:
    """text2sparql/_orchestrator.py either doesn't exist (future spec) or is clean."""
    if not _ORCHESTRATOR_PY.exists():
        pytest.skip("text2sparql/_orchestrator.py not yet implemented (future spec)")
    source = _ORCHESTRATOR_PY.read_text()
    _assert_no_forbidden_attrs(source, _ORCHESTRATOR_PY)


def test_generator_py_no_forbidden_attrs_or_absent() -> None:
    """text2sparql/_generator.py either doesn't exist (future spec) or is clean."""
    if not _GENERATOR_PY.exists():
        pytest.skip("text2sparql/_generator.py not yet implemented (future spec)")
    source = _GENERATOR_PY.read_text()
    _assert_no_forbidden_attrs(source, _GENERATOR_PY)


def test_forbidden_attrs_list_completeness() -> None:
    """The forbidden attribute list matches the ADR-0015 canonical list."""
    expected = {
        "question.text",
        "query.text",
        "sparql.query",
        "document.content",
        "chunk.text",
    }
    assert set(_FORBIDDEN_ATTRS) == expected, (
        "Update _FORBIDDEN_ATTRS to match ADR-0015 canonical content-capture policy"
    )
