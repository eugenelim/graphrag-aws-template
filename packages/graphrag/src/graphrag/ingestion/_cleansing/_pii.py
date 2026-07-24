"""PII regex pattern library for graphrag.ingestion._cleansing.

Patterns cover: email addresses, phone numbers (E.164 + common US/UK formats),
US SSNs, credit-card numbers, and national IDs (UK NI, Australian TFN).

All functions are importable without boto3 or botocore.
Optional Comprehend integration is triggered by ``ENABLE_COMPREHEND_PII=1``
in the task environment (requires the Comprehend VPC interface endpoint).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Compiled regex patterns
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)

_PHONE_RE = re.compile(
    r"(?:"
    r"\+?1?[\s\-.]?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}"  # US/CA
    r"|\+44\s?\d{2,4}\s?\d{3,4}\s?\d{3,4}"  # UK
    r"|\+\d{1,3}[\s\-]?\d{6,14}"  # E.164
    r")",
)

_SSN_RE = re.compile(r"\b\d{3}[\-\s]\d{2}[\-\s]\d{4}\b")

# Luhn-checked credit card numbers (simplified: 13–19 digit sequences with optional
# separators — full Luhn check is too expensive for regex; accept false positives).
_CC_RE = re.compile(
    r"(?:4[0-9]{3}|5[1-5][0-9]{2}|3[47][0-9]{2}|6(?:011|5[0-9]{2}))"
    r"[\s\-]?(?:[0-9]{4}[\s\-]?){2,3}[0-9]{1,4}"
)

_UK_NI_RE = re.compile(
    r"\b[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[ABCD]\b",
    re.IGNORECASE,
)

_AU_TFN_RE = re.compile(r"\b\d{3}[\s\-]?\d{3}[\s\-]?\d{3}\b")

_ALL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", _EMAIL_RE),
    ("phone", _PHONE_RE),
    ("ssn", _SSN_RE),
    ("credit_card", _CC_RE),
    ("uk_ni", _UK_NI_RE),
    ("au_tfn", _AU_TFN_RE),
]


@dataclass
class PIIResult:
    """Result of PII detection over a block of text."""

    flagged: bool = False
    entity_count: int = 0
    entity_types: list[str] = field(default_factory=list)


def detect_pii(text: str) -> PIIResult:
    """Run regex PII detection over ``text``.

    Optionally also calls AWS Comprehend when ``ENABLE_COMPREHEND_PII=1`` is
    set and the Comprehend VPC endpoint is available.

    Args:
        text: The cleansed document text to scan.

    Returns:
        PIIResult with ``flagged=True`` and entity counts when PII is found.
    """
    entity_types: list[str] = []

    for label, pattern in _ALL_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            entity_types.extend([label] * len(matches))

    if os.environ.get("ENABLE_COMPREHEND_PII") == "1":
        comprehend_types = _detect_pii_comprehend(text)
        entity_types.extend(comprehend_types)

    return PIIResult(
        flagged=len(entity_types) > 0,
        entity_count=len(entity_types),
        entity_types=entity_types,
    )


def _detect_pii_comprehend(text: str) -> list[str]:
    """Call AWS Comprehend DetectPiiEntities on ``text``.

    Returns a list of PII entity type strings (e.g. ``["EMAIL", "PHONE"]``).
    Silently returns ``[]`` on any AWS error (network, quota, endpoint unavailable).
    """
    try:
        import boto3

        client = boto3.client("comprehend")
        # Comprehend: max 5000 bytes per request.
        chunk = text[:5000] if len(text) > 5000 else text
        response = client.detect_pii_entities(Text=chunk, LanguageCode="en")
        return [e["Type"] for e in response.get("Entities", [])]
    except Exception:  # noqa: BLE001
        return []
