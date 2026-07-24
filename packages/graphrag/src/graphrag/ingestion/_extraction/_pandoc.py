"""PandocExtractor — convert .docx (and other pandoc-supported formats) to Markdown."""

from __future__ import annotations

import os
import tempfile


class PandocExtractor:
    """Use pypandoc to convert .docx (or any pandoc-supported format) to Markdown.

    Requires pypandoc to be installed (``pip install 'graphrag-aws-demo[ingest]'``)
    AND the pandoc binary to be present on PATH.  The Docker image bakes in the
    pandoc binary; CI tests inject a mock so no binary is required offline.
    """

    def extract(self, file_bytes: bytes, path: str) -> str:
        try:
            import pypandoc
        except ImportError as exc:
            raise RuntimeError(
                "pypandoc is not installed. Run: pip install 'graphrag-aws-demo[ingest]'"
            ) from exc

        ext = os.path.splitext(path)[-1].lower()
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            return pypandoc.convert_file(tmp_path, "markdown")
        finally:
            os.unlink(tmp_path)
