"""graphrag.ingestion._cleansing — Silver quality gates and cleansing report.

Public API
----------
CleansingPipeline — orchestrates all Silver gates and produces CleansingReport.
"""

from __future__ import annotations

import dataclasses
import json

from graphrag.ingestion._cleansing._gates import (
    gate_min_content,
    gate_structure,
    strip_binary_residue,
)
from graphrag.ingestion._cleansing._headers import strip_headers_footers
from graphrag.ingestion._cleansing._pii import detect_pii
from graphrag.ingestion._types import CleansingReport

__all__ = ["CleansingPipeline"]


class CleansingPipeline:
    """Run Silver quality gates over extracted Markdown text.

    Pipeline order:
    1. Header/footer removal (position heuristic + regex)
    2. Binary residue stripping
    3. Minimum-content gate (< 200 chars → quarantine)
    4. Structure gate (no headings and no paragraph blocks → quarantine)
    5. PII detection (flag-and-surface; never quarantine on PII alone)

    Args:
        extractor: Name of the extractor that produced the text (recorded in
            the cleansing report for provenance).
    """

    def __init__(self, extractor: str = "unknown") -> None:
        self._extractor = extractor

    def run(
        self,
        text: str,
        doc_uri: str,
        sha: str,
    ) -> tuple[str, CleansingReport]:
        """Run all Silver gates over ``text``.

        Args:
            text: Raw Markdown text from the extractor.
            doc_uri: Stable document URI (for the report).
            sha: Git commit SHA (for the report).

        Returns:
            ``(clean_text, report)`` — the cleansed text and the audit report.
            When ``report.quarantined`` is ``True``, ``clean_text`` is the
            partially-cleaned text up to the point of failure; the caller must
            not write a Silver artifact and must emit a quarantine record instead.
        """
        char_count_raw = len(text)

        # 1. Header/footer removal
        text, headers_stripped = strip_headers_footers(text)

        # 2. Binary residue stripping
        text, binary_stripped = strip_binary_residue(text)

        char_count_clean = len(text)

        gates_passed: list[str] = []
        gates_failed: list[str] = []

        # 3. Minimum content gate
        result = gate_min_content(text)
        if result is not None:
            gates_failed.append(result)
            report = CleansingReport(
                doc_uri=doc_uri,
                sha=sha,
                extractor=self._extractor,
                char_count_raw=char_count_raw,
                char_count_clean=char_count_clean,
                gates_passed=gates_passed,
                gates_failed=gates_failed,
                quarantined=True,
                headers_stripped=headers_stripped,
                binary_blocks_stripped=binary_stripped,
            )
            return text, report
        gates_passed.append("min_content")

        # 4. Structure gate
        result = gate_structure(text)
        if result is not None:
            gates_failed.append(result)
            report = CleansingReport(
                doc_uri=doc_uri,
                sha=sha,
                extractor=self._extractor,
                char_count_raw=char_count_raw,
                char_count_clean=char_count_clean,
                gates_passed=gates_passed,
                gates_failed=gates_failed,
                quarantined=True,
                headers_stripped=headers_stripped,
                binary_blocks_stripped=binary_stripped,
            )
            return text, report
        gates_passed.append("structure")

        # 5. PII detection (never quarantine; always flag)
        pii_result = detect_pii(text)

        report = CleansingReport(
            doc_uri=doc_uri,
            sha=sha,
            extractor=self._extractor,
            char_count_raw=char_count_raw,
            char_count_clean=char_count_clean,
            gates_passed=gates_passed,
            gates_failed=gates_failed,
            pii_flagged=pii_result.flagged,
            pii_entities_detected=pii_result.entity_count,
            quarantined=False,
            headers_stripped=headers_stripped,
            binary_blocks_stripped=binary_stripped,
        )
        return text, report

    @staticmethod
    def report_to_json(report: CleansingReport) -> str:
        """Serialise a CleansingReport to a JSON string for S3 upload."""
        return json.dumps(dataclasses.asdict(report), indent=2)
