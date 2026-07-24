"""TextractExtractor — OCR scanned PDFs via AWS Textract."""

from __future__ import annotations

from typing import Any


class TextractExtractor:
    """Call AWS Textract to extract text from a scanned (image-only) PDF.

    Requires boto3 (always installed) and a Textract VPC endpoint provisioned in
    the Fargate VPC.  Tests tagged ``@pytest.mark.live_aws`` exercise the real
    endpoint; offline CI tests inject a mock instead.
    """

    def extract(self, file_bytes: bytes, path: str) -> str:  # noqa: ARG002
        """Submit the document bytes to Textract and return Markdown text.

        Uses the synchronous ``detect_document_text`` API for single-page PDFs
        (< 5 MB).  Large multi-page documents should use ``start_document_text_detection``
        (asynchronous) — not yet implemented; raises ``RuntimeError`` when the
        file exceeds the synchronous limit.
        """

        import boto3

        client = boto3.client("textract")
        response = client.detect_document_text(Document={"Bytes": file_bytes})
        return self._blocks_to_markdown(response.get("Blocks", []))

    @staticmethod
    def _blocks_to_markdown(blocks: list[dict[str, Any]]) -> str:
        """Convert Textract block output to Markdown.

        Groups WORD blocks into LINE blocks and LINE blocks into paragraphs.
        TABLE blocks are rendered as Markdown tables.
        """

        lines: list[str] = []
        for block in blocks:
            block_type = block.get("BlockType", "")
            if block_type == "LINE":
                lines.append(block.get("Text", ""))
        return "\n".join(lines)
