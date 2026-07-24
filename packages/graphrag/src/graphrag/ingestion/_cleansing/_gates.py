"""Silver quality gates for graphrag.ingestion._cleansing.

Each gate function takes the cleansed text and returns ``None`` on pass or
a ``str`` gate-name on failure.  The gate name is recorded in the CleansingReport
and forms the ``biz:quarantineReason`` triple on SHACL/Silver gate failure.
"""

from __future__ import annotations

import re

# Minimum character count after stripping artifacts.
_MIN_CONTENT_CHARS = 200

# Structural markers: at least one heading OR one paragraph block required.
_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_PARAGRAPH_RE = re.compile(r"\S.{10,}\S", re.MULTILINE)

# Binary residue: if > 15% of the document is non-UTF-8-representable characters
# (null bytes, control chars, etc.), flag it as binary residue.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0e-\x1f\x7f-\x9f]")
_BINARY_THRESHOLD = 0.15


def gate_min_content(text: str) -> str | None:
    """Fail if the cleansed text is < 200 characters.

    Returns:
        ``None`` on pass; ``"min_content"`` gate name on failure with
        context appended: ``"min_content (observed: N chars)"``.
    """
    count = len(text.strip())
    if count < _MIN_CONTENT_CHARS:
        return f"min_content (observed: {count} chars)"
    return None


def gate_structure(text: str) -> str | None:
    """Fail if the text has no headings AND no paragraph-length lines.

    Returns:
        ``None`` on pass; ``"structure"`` on failure.
    """
    has_heading = bool(_HEADING_RE.search(text))
    has_paragraph = bool(_PARAGRAPH_RE.search(text))
    if not has_heading and not has_paragraph:
        return "structure"
    return None


def strip_binary_residue(text: str) -> tuple[str, int]:
    """Replace binary / control-character blocks and return (clean_text, blocks_stripped).

    A "block" is a contiguous run of non-UTF-8-safe characters.
    """
    control_matches = _CONTROL_CHAR_RE.findall(text)
    ratio = len(control_matches) / max(len(text), 1)
    if ratio >= _BINARY_THRESHOLD:
        # Strip all control characters.
        cleaned = _CONTROL_CHAR_RE.sub("", text)
        return cleaned, 1
    return text, 0
