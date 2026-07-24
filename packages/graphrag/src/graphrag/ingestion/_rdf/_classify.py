"""Document classifier for graphrag.ingestion._rdf.

Maps a document (file path + Markdown text) to an RDF class and named-graph
partition.  The classifier is heuristic — it uses path components and optional
front-matter metadata.  Semantic mis-classification is the residual named in
ADR-0012; SHACL validates required fields, not rdf:type correctness.

Classification priority:
1. Front-matter ``type:`` field (explicit, highest confidence)
2. Path component keywords (e.g. ``policies/``, ``sops/``)
3. Default: ``"sop"`` → biz:SOP, descriptive partition

All functions are importable without boto3 or botocore.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Supported doc types and their RDF class / partition
# ---------------------------------------------------------------------------

_NORMATIVE_TYPES = {"policy", "standard", "guideline"}

_TYPE_TO_CLASS: dict[str, str] = {
    "policy": "https://graphrag-aws.demo/biz-ops/ontology#Policy",
    "standard": "https://graphrag-aws.demo/biz-ops/ontology#Standard",
    "guideline": "https://graphrag-aws.demo/biz-ops/ontology#Guideline",
    "sop": "https://graphrag-aws.demo/biz-ops/ontology#SOP",
    "jobaid": "https://graphrag-aws.demo/biz-ops/ontology#JobAid",
    "transcript": "https://graphrag-aws.demo/biz-ops/ontology#Transcript",
}

_NORMATIVE_PARTITION = "urn:graph:normative"
_DESCRIPTIVE_PARTITION = "urn:graph:descriptive"

# Type → path-keyword regex (checked against each path component)
_PATH_KEYWORDS: list[tuple[str, re.Pattern[str]]] = [
    ("policy", re.compile(r"polic(?:y|ies)", re.IGNORECASE)),
    ("standard", re.compile(r"standards?", re.IGNORECASE)),
    ("guideline", re.compile(r"guidelines?", re.IGNORECASE)),
    ("sop", re.compile(r"sops?|procedures?|process(?:es)?", re.IGNORECASE)),
    ("jobaid", re.compile(r"job[-_]?aids?|quick[-_]?refs?|cheat[-_]?sheets?", re.IGNORECASE)),
    ("transcript", re.compile(r"transcripts?|meetings?|calls?", re.IGNORECASE)),
]

# Front-matter block delimited by "---"
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_FM_FIELD_RE = re.compile(r"^([\w_]+)\s*:\s*(.+)$", re.MULTILINE)


@dataclass
class Classification:
    """Classification result for a document."""

    doc_type: str  # "policy" | "sop" | "standard" | "guideline" | "jobaid" | "transcript"
    rdf_class: str  # fully-qualified biz: URI
    partition: str  # "urn:graph:normative" | "urn:graph:descriptive"
    name: str  # display name for schema:name triple
    effective_date: str | None  # xsd:date string from front-matter "effective_date"
    scope: str | None  # from front-matter "scope"


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Extract simple key: value pairs from an optional YAML front-matter block."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    block = m.group(1)
    return {k.lower(): v.strip() for k, v in _FM_FIELD_RE.findall(block)}


def _type_from_path(path: str) -> str | None:
    """Return a doc-type string based on directory/filename keyword matching."""
    parts = path.replace("\\", "/").split("/")
    for part in parts:
        for doc_type, pat in _PATH_KEYWORDS:
            if pat.search(part):
                return doc_type
    return None


def _normalise_type(raw: str) -> str:
    """Normalise a raw front-matter type string to a known doc-type key."""
    t = raw.lower().strip()
    # Handle plurals: "policies" → "policy", "sops" → "sop"
    if t.endswith("ies"):
        t = t[:-3] + "y"
    elif t.endswith("s") and t[:-1] in _TYPE_TO_CLASS:
        t = t[:-1]
    return t


def classify(path: str, markdown: str) -> Classification:
    """Classify a document and return its RDF class + partition.

    Args:
        path: Repository-relative file path.
        markdown: Extracted/cleansed Markdown text (may include front-matter).

    Returns:
        Classification with rdf_class, partition, name, effective_date, scope.
    """
    fm = _parse_frontmatter(markdown)

    # Priority 1: explicit front-matter type field
    doc_type = ""
    raw_type = fm.get("type", "")
    if raw_type:
        candidate = _normalise_type(raw_type)
        if candidate in _TYPE_TO_CLASS:
            doc_type = candidate

    # Priority 2: path component heuristic
    if not doc_type:
        doc_type = _type_from_path(path) or "sop"

    rdf_class = _TYPE_TO_CLASS[doc_type]
    partition = _NORMATIVE_PARTITION if doc_type in _NORMATIVE_TYPES else _DESCRIPTIVE_PARTITION

    # Name: from front-matter title/name, else filename stem
    name = fm.get("title") or fm.get("name") or os.path.splitext(os.path.basename(path))[0]

    effective_date = fm.get("effective_date") or fm.get("effectivedate") or None
    scope = fm.get("scope") or None

    return Classification(
        doc_type=doc_type,
        rdf_class=rdf_class,
        partition=partition,
        name=name,
        effective_date=effective_date,
        scope=scope,
    )
