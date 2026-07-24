"""TDD tests for graphrag.ingestion._extraction.FormatRouter and DoclingExtractor."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from graphrag.ingestion._extraction import Extractor, FormatRouter
from graphrag.ingestion._extraction._docling import DoclingExtractor


def _make_extractor(return_value: str = "# Extracted") -> MagicMock:
    """Return a MagicMock that satisfies the Extractor protocol."""
    mock = MagicMock(spec=Extractor)
    mock.extract.return_value = return_value
    return mock


def _router_with_mocks(**overrides: MagicMock) -> tuple[FormatRouter, dict[str, MagicMock]]:
    """Build a FormatRouter with mocked extractors; return (router, mocks)."""
    mocks: dict[str, MagicMock] = {
        "pandoc": _make_extractor("# Pandoc output"),
        "docling": _make_extractor("# Docling output"),
        "markitdown": _make_extractor("# Markitdown output"),
        "passthrough": _make_extractor("# Passthrough output"),
    }
    mocks.update(overrides)
    router = FormatRouter(extractors=mocks)
    return router, mocks


# ---------------------------------------------------------------------------
# T1-1: .docx → PandocExtractor dispatched
# ---------------------------------------------------------------------------


def test_docx_routes_to_pandoc() -> None:
    router, mocks = _router_with_mocks()
    router.route(b"fake docx bytes", "docs/hr_policy.docx")
    mocks["pandoc"].extract.assert_called_once_with(b"fake docx bytes", "docs/hr_policy.docx")
    mocks["docling"].extract.assert_not_called()


# ---------------------------------------------------------------------------
# T1-2: .pdf (digital, text layer) → DoclingExtractor dispatched
# ---------------------------------------------------------------------------


def test_pdf_digital_routes_to_docling() -> None:
    router, mocks = _router_with_mocks()
    router.route(b"fake pdf bytes", "reports/q1.pdf")
    mocks["docling"].extract.assert_called_once_with(b"fake pdf bytes", "reports/q1.pdf")
    mocks["pandoc"].extract.assert_not_called()


# ---------------------------------------------------------------------------
# T1-3: .pptx → MarkitdownExtractor dispatched
# ---------------------------------------------------------------------------


def test_pptx_routes_to_markitdown() -> None:
    router, mocks = _router_with_mocks()
    router.route(b"fake pptx bytes", "decks/onboarding.pptx")
    mocks["markitdown"].extract.assert_called_once()
    mocks["pandoc"].extract.assert_not_called()


# ---------------------------------------------------------------------------
# T1-4: .xlsx → MarkitdownExtractor dispatched
# ---------------------------------------------------------------------------


def test_xlsx_routes_to_markitdown() -> None:
    router, mocks = _router_with_mocks()
    router.route(b"fake xlsx bytes", "data/matrix.xlsx")
    mocks["markitdown"].extract.assert_called_once()


# ---------------------------------------------------------------------------
# T1-5: .md → PassThroughExtractor dispatched
# ---------------------------------------------------------------------------


def test_md_routes_to_passthrough() -> None:
    router, mocks = _router_with_mocks()
    md_bytes = b"# Hello\nThis is a markdown file."
    router.route(md_bytes, "sops/incident_response.md")
    mocks["passthrough"].extract.assert_called_once_with(md_bytes, "sops/incident_response.md")
    mocks["pandoc"].extract.assert_not_called()
    mocks["docling"].extract.assert_not_called()


# ---------------------------------------------------------------------------
# T1-6: .txt and .rst also route to passthrough
# ---------------------------------------------------------------------------


def test_txt_routes_to_passthrough() -> None:
    router, mocks = _router_with_mocks()
    router.route(b"plain text", "notes/readme.txt")
    mocks["passthrough"].extract.assert_called_once()


def test_rst_routes_to_passthrough() -> None:
    router, mocks = _router_with_mocks()
    router.route(b"rst content", "docs/guide.rst")
    mocks["passthrough"].extract.assert_called_once()


# ---------------------------------------------------------------------------
# Unsupported extension
# ---------------------------------------------------------------------------


def test_unsupported_extension_raises() -> None:
    router, _ = _router_with_mocks()
    with pytest.raises(ValueError, match="Unsupported file extension"):
        router.route(b"data", "archive.zip")


# ---------------------------------------------------------------------------
# route() return value carries the extractor key
# ---------------------------------------------------------------------------


def test_route_returns_markdown_and_extractor_key() -> None:
    router, mocks = _router_with_mocks()
    mocks["pandoc"].extract.return_value = "# HR Policy"
    markdown, key = router.route(b"bytes", "policy.docx")
    assert markdown == "# HR Policy"
    assert key == "pandoc"


# ---------------------------------------------------------------------------
# DoclingExtractor: scanned PDF delegates to textract_fallback
# ---------------------------------------------------------------------------


def test_docling_scanned_pdf_delegates_to_textract() -> None:
    """DoclingExtractor.extract() calls textract_fallback.extract() for scanned PDFs."""
    textract_mock = _make_extractor("## Textract output")

    with patch.object(DoclingExtractor, "is_scanned", return_value=True):
        docling = DoclingExtractor(textract_fallback=textract_mock)
        result = docling.extract(b"scanned pdf bytes", "reports/scan.pdf")

    textract_mock.extract.assert_called_once_with(b"scanned pdf bytes", "reports/scan.pdf")
    assert result == "## Textract output"


def test_docling_digital_pdf_does_not_call_textract() -> None:
    """DoclingExtractor.extract() does NOT call textract_fallback for digital PDFs."""
    textract_mock = _make_extractor("## Textract output")

    with (
        patch.object(DoclingExtractor, "is_scanned", return_value=False),
        patch(
            "graphrag.ingestion._extraction._docling.DoclingExtractor.extract",
            wraps=None,
        ) as _,
    ):
        # Use a separate approach: mock the docling import inside extract()
        with patch.dict(
            "sys.modules",
            {"docling": MagicMock(), "docling.document_converter": MagicMock()},
        ):
            import sys

            mock_dc = MagicMock()
            extract_md = mock_dc.DocumentConverter.return_value
            extract_md.convert.return_value.document.export_to_markdown.return_value = (
                "# Docling extracted"
            )
            sys.modules["docling.document_converter"] = mock_dc
            docling = DoclingExtractor(textract_fallback=textract_mock)
            with patch.object(docling, "is_scanned", return_value=False):
                docling.extract(b"digital pdf bytes", "reports/digital.pdf")

    # textract was NOT called
    textract_mock.extract.assert_not_called()
