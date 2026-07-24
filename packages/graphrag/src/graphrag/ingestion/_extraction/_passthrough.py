"""PassThroughExtractor — plain-text and Markdown files need no conversion."""

from __future__ import annotations


class PassThroughExtractor:
    """Return the file content decoded as UTF-8; no conversion needed.

    Used for ``.md``, ``.txt``, and ``.rst`` files.
    """

    def extract(self, file_bytes: bytes, path: str) -> str:  # noqa: ARG002
        """Decode file_bytes as UTF-8, replacing invalid sequences."""
        return file_bytes.decode("utf-8", errors="replace")
