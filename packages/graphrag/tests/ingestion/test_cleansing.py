"""TDD tests for graphrag.ingestion._cleansing.CleansingPipeline."""

from __future__ import annotations

import json

from graphrag.ingestion._cleansing import CleansingPipeline
from graphrag.ingestion._cleansing._gates import gate_min_content, gate_structure
from graphrag.ingestion._cleansing._headers import strip_headers_footers
from graphrag.ingestion._cleansing._pii import detect_pii

DOC_URI = "urn:doc:test-repo:sops/incident.md"
SHA = "deadbeef"  # pragma: allowlist secret


# ---------------------------------------------------------------------------
# T2-1: min_content gate
# ---------------------------------------------------------------------------


def test_short_content_is_quarantined() -> None:
    pipeline = CleansingPipeline(extractor="passthrough")
    short_text = "Hi there."
    _, report = pipeline.run(short_text, doc_uri=DOC_URI, sha=SHA)
    assert report.quarantined is True
    assert any("min_content" in g for g in report.gates_failed)


def test_min_content_gate_fail_includes_observed_count() -> None:
    result = gate_min_content("x" * 10)
    assert result is not None
    assert "10 chars" in result


def test_min_content_gate_pass_at_boundary() -> None:
    result = gate_min_content("x" * 200)
    assert result is None


# ---------------------------------------------------------------------------
# T2-2: structure gate
# ---------------------------------------------------------------------------


def test_no_structure_is_quarantined() -> None:
    pipeline = CleansingPipeline(extractor="passthrough")
    # Short word tokens on separate lines: no headings, no paragraph-length lines,
    # but >= 200 chars total so the min_content gate passes first.
    # Words are 2 chars so no line exceeds 11 chars (the paragraph threshold).
    # Not bare digits, so the page-number header stripper doesn't remove them.
    words = ["ab", "cd", "ef", "gh", "ij", "kl", "mn", "op", "qr", "st"]
    structureless = "\n".join(words * 22)  # 220 lines x 3 chars = 660 chars
    _, report = pipeline.run(structureless, doc_uri=DOC_URI, sha=SHA)
    assert report.quarantined is True
    assert any("structure" in g for g in report.gates_failed)


def test_structure_gate_passes_with_heading() -> None:
    text = "# Introduction\n" + "Some content " * 20
    result = gate_structure(text)
    assert result is None


def test_structure_gate_passes_with_paragraph() -> None:
    result = gate_structure("This is a long enough paragraph sentence that definitely passes.")
    assert result is None


def test_structure_gate_fails_without_headings_or_paragraphs() -> None:
    # Each line is ≤ 2 chars — below the paragraph threshold; no headings either.
    result = gate_structure("\n".join(["ab", "cd", "ef", "gh"] * 5))
    assert result == "structure"


# ---------------------------------------------------------------------------
# T2-3: Header/footer removal
# ---------------------------------------------------------------------------


def test_page_header_is_stripped() -> None:
    text = "# Introduction\n\nPage 1 of 10\n\nSome content here."
    cleaned, stripped_count = strip_headers_footers(text)
    assert "Page 1 of 10" not in cleaned
    assert stripped_count >= 1


def test_page_number_only_line_is_stripped() -> None:
    text = "# Report\n\n42\n\nReal content goes here."
    cleaned, count = strip_headers_footers(text)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    assert "42" not in lines
    assert count >= 1


def test_clean_content_not_stripped() -> None:
    text = "# HR Policy\n\nThis document describes our HR procedures.\n\nSection 2."
    cleaned, count = strip_headers_footers(text)
    assert "HR Policy" in cleaned
    assert count == 0


# ---------------------------------------------------------------------------
# T2-4: PII detection
# ---------------------------------------------------------------------------


def test_pii_email_detected() -> None:
    text = (
        "Contact us at test@example.com for support. "
        "This is a document with sufficient content to pass all gates. "
        "We are committed to serving you well. Please reach out anytime."
    )
    result = detect_pii(text)
    assert result.flagged is True
    assert result.entity_count >= 1
    assert "email" in result.entity_types


def test_pii_phone_detected() -> None:
    text = "Call us at +1-800-555-1234 for assistance."
    result = detect_pii(text)
    assert result.flagged is True
    assert "phone" in result.entity_types


def test_no_pii_returns_unflagged() -> None:
    text = "This text has no personal information at all."
    result = detect_pii(text)
    assert result.flagged is False
    assert result.entity_count == 0


def test_pii_does_not_quarantine_document() -> None:
    """PII flag must not cause quarantine — flag-and-surface only."""
    pipeline = CleansingPipeline(extractor="passthrough")
    text = (
        "# HR Policy\n\n"
        "Contact test@example.com for support.\n\n"
        "This document describes policies that employees must follow. "
        "Please review carefully and confirm your understanding.\n\n"
        "Failure to comply may result in disciplinary action."
    )
    _, report = pipeline.run(text, doc_uri=DOC_URI, sha=SHA)
    assert report.quarantined is False
    assert report.pii_flagged is True
    assert report.pii_entities_detected >= 1


# ---------------------------------------------------------------------------
# T2-5: Binary residue stripping
# ---------------------------------------------------------------------------


def test_binary_residue_stripped() -> None:
    from graphrag.ingestion._cleansing._gates import strip_binary_residue

    binary_heavy = "\x00\x01\x02" * 50 + "a" * 50
    cleaned, blocks = strip_binary_residue(binary_heavy)
    assert blocks == 1
    assert "\x00" not in cleaned


def test_light_binary_not_stripped() -> None:
    from graphrag.ingestion._cleansing._gates import strip_binary_residue

    light = "a" * 100 + "\x00" * 5
    _, blocks = strip_binary_residue(light)
    assert blocks == 0


# ---------------------------------------------------------------------------
# T2-6: CleansingReport JSON serialisation
# ---------------------------------------------------------------------------


def test_cleansing_report_json_schema_pass() -> None:
    pipeline = CleansingPipeline(extractor="pandoc")
    text = (
        "# Incident Response Procedure\n\n"
        "This document defines how our team responds to security incidents. "
        "All responders must follow these steps carefully and document every action. "
        "Escalate immediately if the incident affects production systems."
    )
    _, report = pipeline.run(text, doc_uri=DOC_URI, sha=SHA)
    json_str = CleansingPipeline.report_to_json(report)
    data = json.loads(json_str)
    assert data["quarantined"] is False
    assert data["doc_uri"] == DOC_URI
    assert data["sha"] == SHA
    assert data["extractor"] == "pandoc"
    assert isinstance(data["gates_passed"], list)
    assert "min_content" in data["gates_passed"]


def test_cleansing_report_json_schema_quarantine() -> None:
    pipeline = CleansingPipeline(extractor="passthrough")
    _, report = pipeline.run("too short", doc_uri=DOC_URI, sha=SHA)
    json_str = CleansingPipeline.report_to_json(report)
    data = json.loads(json_str)
    assert data["quarantined"] is True
    assert any("min_content" in g for g in data["gates_failed"])
