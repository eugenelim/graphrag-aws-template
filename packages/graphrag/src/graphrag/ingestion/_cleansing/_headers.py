"""Header and footer removal for graphrag.ingestion._cleansing.

Uses a regex-based heuristic to strip common page headers and footers:
  - "Page N of M" / "Page N" patterns
  - Running headers (short lines at the top of each \x0c-delimited page)
  - Section footers with common patterns (company names, document codes, dates)

Returns the cleaned text and the count of lines removed.
"""

from __future__ import annotations

import re

# Patterns that strongly indicate a header/footer line.
_HEADER_FOOTER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^Page\s+\d+\s+of\s+\d+$", re.IGNORECASE),
    re.compile(r"^Page\s+\d+$", re.IGNORECASE),
    re.compile(r"^\d+\s*$"),  # bare page number
    re.compile(r"^-+\s*\d+\s*-+$"),  # -- 1 --
    re.compile(r"^Confidential\b", re.IGNORECASE),
    re.compile(r"^Proprietary\b", re.IGNORECASE),
    re.compile(r"^Internal\s+Use\s+Only\b", re.IGNORECASE),
]


def strip_headers_footers(text: str) -> tuple[str, int]:
    """Remove header/footer lines from a block of text.

    Args:
        text: Extracted Markdown text (may contain \x0c page breaks from pdfminer).

    Returns:
        Tuple of (cleaned_text, stripped_line_count).
    """
    lines = text.splitlines()
    kept: list[str] = []
    stripped = 0

    for line in lines:
        stripped_line = line.strip()
        if any(p.match(stripped_line) for p in _HEADER_FOOTER_PATTERNS):
            stripped += 1
        else:
            kept.append(line)

    return "\n".join(kept), stripped
