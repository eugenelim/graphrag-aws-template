"""Signal constants for RuleQueryRouter — compiled regex and verb sets.

These are tunable constants separated from the routing logic so tests can
import them directly and the routing matrix can be read at a glance.

No boto3 / botocore import is allowed in this file.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Entity URI detection
# ---------------------------------------------------------------------------
# Matches any token that starts with a URI scheme or the "biz:" prefix.
# \S+ is greedy but not backtracking-catastrophic: \S matches non-whitespace
# without any nested alternation, so there is no super-linear blowup on
# adversarial input.
ENTITY_URI_PATTERN: re.Pattern[str] = re.compile(r"(?:urn:|https?://|biz:)\S+")

# ---------------------------------------------------------------------------
# Aggregation verbs  →  strategy=structured
# ---------------------------------------------------------------------------
# Phrase-match (lower-cased question); order does not matter.
AGGREGATION_VERBS: frozenset[str] = frozenset(
    {
        "how many",
        "count",
        "list all",
        "total",
        "sum",
    }
)

# ---------------------------------------------------------------------------
# Relationship verbs  →  strategy=graph_expand  (when entity URI also present)
# ---------------------------------------------------------------------------
RELATIONSHIP_VERBS: frozenset[str] = frozenset(
    {
        "related to",
        "relate to",
        "relates to",
        "connected to",
        "links to",
        "refers to",
    }
)

# ---------------------------------------------------------------------------
# Thematic markers  →  strategy=global  (when no entity URI present)
# ---------------------------------------------------------------------------
THEMATIC_MARKERS: frozenset[str] = frozenset(
    {
        "broadly",
        "in general",
        "overview",
        "tell me about",
    }
)


def detect_entity_uris(text: str) -> list[str]:
    """Return all entity URI tokens found in *text*."""
    return ENTITY_URI_PATTERN.findall(text)


def has_aggregation_verb(text: str) -> bool:
    """Return True if *text* (lower-cased) contains any aggregation verb."""
    lower = text.lower()
    return any(v in lower for v in AGGREGATION_VERBS)


def has_relationship_verb(text: str) -> bool:
    """Return True if *text* (lower-cased) contains any relationship verb."""
    lower = text.lower()
    return any(v in lower for v in RELATIONSHIP_VERBS)


def has_thematic_marker(text: str) -> bool:
    """Return True if *text* (lower-cased) contains any thematic marker."""
    lower = text.lower()
    return any(m in lower for m in THEMATIC_MARKERS)
