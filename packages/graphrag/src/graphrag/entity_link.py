"""Question → entity-ID linking on the controlled vocabulary (slice-3 AC1).

The seed-and-expand hybrid (ADR-0001) seeds graph entities from *two* sides; this
module is the question side. It extracts candidate entities from a natural question
and normalizes each to a graph node ID **via the slice-1 ``normalize`` functions**,
so a question-linked seed is byte-identical to the resolved node it should hit —
there is no new matching model (ADR-0001 "reuse"; charter pattern 1: narratable).

It is pure (no store, no network): ``link_question`` returns *candidates*, each
carrying its surface form, resolved id, and the ``via`` that produced it. The hybrid
layer (``hybrid.py``) confirms each candidate against the graph (``get_node``) before
seeding, recording the unconfirmed ones — so a misseed is filtered and visible, never
silently expanded.

Only the mechanical normalizers (``@handle``, SIG slug, KEP number) plus the
**display-name → handle** alias table are used — exactly the controlled vocabulary of
the corpus (SIG slugs, GitHub handles, KEP numbers).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .normalize import kep_id, normalize_handle, normalize_slug

CandidateKind = Literal["person", "sig", "kep"]
CandidateVia = Literal["handle", "alias", "slug", "kep-number"]

# A GitHub @handle (the leading @ is required here so a bare prose word is not a
# false person candidate; bare-handle resolution is the alias path).
_HANDLE_RE = re.compile(r"@([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)")
# A KEP reference: KEP-1287 / KEP 1287 / kep1287.
_KEP_RE = re.compile(r"\bKEP[-\s]?(\d+)\b", re.IGNORECASE)
# A SIG mention anchored on the literal "SIG" token, either order:
#   "SIG Network" / "sig-network" / "Network SIG". The captured group is the slug
#   body (everything but the SIG token), normalized via normalize_slug.
_SIG_AFTER_RE = re.compile(r"\bSIG[-\s]+([A-Za-z][A-Za-z0-9-]*)", re.IGNORECASE)
_SIG_BEFORE_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9-]*)[-\s]+SIG\b", re.IGNORECASE)
# Articles/determiners that precede "SIG" in prose ("the SIG", "a SIG") are not the
# SIG's name — drop them so they don't become a spurious (always-dropped) candidate.
_SIG_STOPWORDS = frozenset({"the", "a", "an", "this", "that", "which", "owning", "owns"})


@dataclass
class Candidate:
    """A question-linked entity candidate, before graph confirmation.

    ``entity_id`` is byte-equal to the slice-1 ``normalize`` output for ``surface``,
    so a candidate can be checked against the resolved graph node directly.
    """

    surface: str
    entity_id: str
    kind: CandidateKind
    via: CandidateVia


def _dedupe(candidates: list[Candidate]) -> list[Candidate]:
    """First-seen-wins de-dupe by entity_id (preserves discovery order)."""
    seen: set[str] = set()
    out: list[Candidate] = []
    for c in candidates:
        if c.entity_id not in seen:
            seen.add(c.entity_id)
            out.append(c)
    return out


def link_question(question: str, aliases: dict[str, str]) -> list[Candidate]:
    """Extract + normalize question entities to graph node IDs (AC1).

    ``aliases`` is the slice-1 **display-name → handle** map
    (``resolve.load_aliases()``); pass ``{}`` to link without the display-name table
    (the live Lambda path uses the mechanical normalizers only — see ``query_lambda``).

    A question naming no known-vocabulary entity yields ``[]``.
    """
    candidates: list[Candidate] = []

    # KEP references: KEP-1287 / KEP 1287.
    for m in _KEP_RE.finditer(question):
        candidates.append(
            Candidate(
                surface=m.group(0),
                entity_id=kep_id(m.group(1)),
                kind="kep",
                via="kep-number",
            )
        )

    # SIG mentions (both word orders), anchored on the literal SIG token.
    for pattern in (_SIG_AFTER_RE, _SIG_BEFORE_RE):
        for m in pattern.finditer(question):
            body = m.group(1)
            if body.lower() in _SIG_STOPWORDS:
                continue  # "the SIG" / "owning SIG" — an article, not the SIG's name
            candidates.append(
                Candidate(
                    surface=m.group(0),
                    entity_id=f"sig:{normalize_slug(body)}",
                    kind="sig",
                    via="slug",
                )
            )

    # GitHub @handles → person via the mechanical handle normalizer.
    for m in _HANDLE_RE.finditer(question):
        candidates.append(
            Candidate(
                surface=m.group(0),
                entity_id=f"person:{normalize_handle(m.group(0))}",
                kind="person",
                via="handle",
            )
        )

    # Display names → person via the alias table (the only non-mechanical step).
    for display, handle in aliases.items():
        if re.search(rf"\b{re.escape(display)}\b", question, re.IGNORECASE):
            candidates.append(
                Candidate(
                    surface=display,
                    entity_id=f"person:{normalize_handle(handle)}",
                    kind="person",
                    via="alias",
                )
            )

    return _dedupe(candidates)
