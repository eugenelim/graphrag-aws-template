"""DoclingExtractor — convert digital PDFs to Markdown using docling.

Scanned-PDF detection: if pdfminer reports < 20 chars average per page,
the document is treated as scanned and delegated to TextractExtractor.

``TRANSFORMERS_OFFLINE=1`` must be set in the Fargate task environment to
prevent docling from attempting network calls for model weights at runtime
(weights are baked into the Docker image at build time).
"""

from __future__ import annotations

import io
import os
import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graphrag.ingestion._extraction import Extractor

# Average chars per page below this threshold → treat as scanned.
_SCANNED_THRESHOLD = 20


class DoclingExtractor:
    """Use docling for digital PDF extraction.  Falls back to TextractExtractor
    for scanned (image-only) PDFs detected by the pdfminer text-layer heuristic.

    Args:
        textract_fallback: Optional pre-instantiated ``TextractExtractor`` to use
            when a scanned PDF is detected.  If ``None``, one is created lazily.
            Inject a mock in tests to verify scanned-PDF delegation without AWS.

    Requires ``docling`` to be installed:
        ``pip install 'graphrag-aws-demo[ingest-full]'``
    """

    def __init__(self, textract_fallback: Extractor | None = None) -> None:
        self._textract: Extractor | None = textract_fallback

    def is_scanned(self, file_bytes: bytes) -> bool:
        """Return True when the PDF has < 20 chars per page on average.

        Uses pdfminer.six to extract the text layer.  Falls back to False
        (assume digital) when pdfminer is not installed.
        """
        try:
            from pdfminer.high_level import extract_text
        except ImportError:
            return False

        text = extract_text(io.BytesIO(file_bytes))
        stripped = text.strip() if text else ""
        if not stripped:
            return True
        # Rough page count from pdfminer page breaks (\x0c = form feed).
        page_count = max(1, text.count("\x0c") + 1)
        return (len(stripped) / page_count) < _SCANNED_THRESHOLD

    def extract(self, file_bytes: bytes, path: str) -> str:
        """Extract text from a PDF, falling back to Textract for scanned pages."""
        if self.is_scanned(file_bytes):
            if self._textract is None:
                from graphrag.ingestion._extraction._textract import TextractExtractor

                self._textract = TextractExtractor()
            return self._textract.extract(file_bytes, path)

        try:
            from docling.document_converter import DocumentConverter
        except ImportError as exc:
            raise RuntimeError(
                "docling is not installed. Run: pip install 'graphrag-aws-demo[ingest-full]'"
            ) from exc

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            converter = DocumentConverter()
            result = converter.convert(tmp_path)
            return result.document.export_to_markdown()
        finally:
            os.unlink(tmp_path)
