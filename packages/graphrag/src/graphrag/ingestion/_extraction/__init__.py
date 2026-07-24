"""graphrag.ingestion._extraction — format router and extractor protocol.

Public API
----------
Extractor   — Protocol all extractor classes satisfy.
FormatRouter — Routes file bytes to the correct extractor by extension.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

__all__ = ["Extractor", "FormatRouter"]


@runtime_checkable
class Extractor(Protocol):
    """Protocol for document extractors: raw bytes → Markdown text.

    Every concrete extractor class satisfies this protocol.  The extractor
    is responsible for format-specific conversion only; header/footer removal
    and quality gating are the CleansingPipeline's responsibility.
    """

    def extract(self, file_bytes: bytes, path: str) -> str:
        """Convert raw file bytes to a Markdown string.

        Args:
            file_bytes: Raw content of the source file.
            path: Repository-relative file path (used for extension detection
                  and logging; not read from disk by the extractor).

        Returns:
            Markdown text.

        Raises:
            RuntimeError: On extraction failure (malformed input, tool error,
                missing binary dependency, or AWS API error).
        """
        ...


class FormatRouter:
    """Route a document to the correct extractor by file extension.

    ``FormatRouter`` is the single decision point that maps a file extension
    to an ``Extractor`` instance.  For ``.pdf`` files, scanned-PDF detection
    is handled inside ``DoclingExtractor`` (not here): the router always
    dispatches ``.pdf`` to ``DoclingExtractor``, which then falls back to
    ``TextractExtractor`` when the text layer is absent.

    Args:
        extractors: Map from logical extractor key (``"pandoc"``, ``"docling"``,
            ``"markitdown"``, ``"passthrough"``) to an ``Extractor`` instance.
            If ``None``, uses production defaults (lazy import of each extractor
            class — no extractor library is imported at ``FormatRouter()`` call
            time, only at ``route()`` time when the default factory runs).

    Usage::

        # Production — extractor libraries must be installed:
        router = FormatRouter()
        markdown, ext_name = router.route(file_bytes, "policies/hr.docx")

        # Tests — inject mocks to avoid loading heavy libraries:
        router = FormatRouter(extractors={"pandoc": mock, "docling": mock2, ...})
    """

    # Map file extension → logical extractor key.
    _EXT_MAP: dict[str, str] = {
        ".docx": "pandoc",
        ".pdf": "docling",
        ".pptx": "markitdown",
        ".xlsx": "markitdown",
        ".md": "passthrough",
        ".txt": "passthrough",
        ".rst": "passthrough",
    }

    def __init__(self, extractors: dict[str, Extractor] | None = None) -> None:
        if extractors is not None:
            self._extractors = extractors
        else:
            self._extractors = self._default_extractors()

    @staticmethod
    def _default_extractors() -> dict[str, Extractor]:
        """Lazily import and instantiate production extractor classes.

        Imports are deferred so that ``import FormatRouter`` succeeds even when
        heavy libraries (docling, pypandoc) are not installed.  The import only
        runs when a ``FormatRouter()`` with no injected extractors is created.
        """
        from graphrag.ingestion._extraction._docling import DoclingExtractor
        from graphrag.ingestion._extraction._markitdown import MarkitdownExtractor
        from graphrag.ingestion._extraction._pandoc import PandocExtractor
        from graphrag.ingestion._extraction._passthrough import PassThroughExtractor

        return {
            "pandoc": PandocExtractor(),
            "docling": DoclingExtractor(),
            "markitdown": MarkitdownExtractor(),
            "passthrough": PassThroughExtractor(),
        }

    def route(self, file_bytes: bytes, path: str) -> tuple[str, str]:
        """Extract text, routing by file extension.

        Args:
            file_bytes: Raw file content.
            path: Repository-relative file path (extension determines routing).

        Returns:
            ``(markdown_text, extractor_key)`` — the extracted Markdown and the
            logical extractor key that was used (e.g. ``"pandoc"``).

        Raises:
            ValueError: For unsupported file extensions.
            RuntimeError: On extractor failure.
        """
        ext = os.path.splitext(path)[-1].lower()
        key = self._EXT_MAP.get(ext)
        if key is None:
            raise ValueError(
                f"Unsupported file extension: {ext!r} (path={path!r}). "
                f"Supported: {sorted(self._EXT_MAP)}"
            )
        extractor = self._extractors[key]
        markdown = extractor.extract(file_bytes, path)
        return markdown, key
