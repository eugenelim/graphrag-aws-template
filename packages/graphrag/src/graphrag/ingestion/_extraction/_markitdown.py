"""MarkitdownExtractor — convert .pptx and .xlsx to Markdown via markitdown."""

from __future__ import annotations

import io


class MarkitdownExtractor:
    """Use markitdown to convert .pptx and .xlsx files to Markdown.

    Requires markitdown to be installed (``pip install 'graphrag-aws-demo[ingest]'``).
    CI tests inject a mock so the library is not required offline.
    """

    def extract(self, file_bytes: bytes, path: str) -> str:  # noqa: ARG002
        try:
            from markitdown import MarkItDown
        except ImportError as exc:
            raise RuntimeError(
                "markitdown is not installed. Run: pip install 'graphrag-aws-demo[ingest]'"
            ) from exc

        md = MarkItDown()
        result = md.convert_stream(io.BytesIO(file_bytes))
        return result.text_content
