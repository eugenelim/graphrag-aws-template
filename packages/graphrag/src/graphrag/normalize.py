"""Normalization — the narratable heart of cross-source entity resolution.

A node's ID *is* its normalized key, so two mentions that normalize to the same
ID are the same node (resolution is "upsert by normalized ID", not a post-hoc
merge pass). Keeping this as plain, inspectable functions — no trained model — is
charter pattern 1: the merge is explainable as "these two source rows produced the
same ID".
"""

from __future__ import annotations

import re

_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_handle(raw: str, aliases: dict[str, str] | None = None) -> str:
    """Normalize a GitHub handle or a prose display-name to a canonical handle.

    Handles arrive inconsistently across the two sources: ``@thockin`` (kep.yaml
    approver), ``thockin`` (a bare reviewer entry), ``@SergeyKanzhelev`` (mixed
    case in sigs.yaml). We strip a leading ``@`` and lower-case. A display name
    (e.g. ``Tim Hockin`` in a pre-kep.yaml KEP's prose) is mapped through the
    alias table *first* — that is the only place a non-mechanical mapping lives,
    and it is small, hand-authored data, never a model.

    >>> normalize_handle("@Thockin")
    'thockin'
    >>> normalize_handle("thockin")
    'thockin'
    >>> normalize_handle("Tim Hockin", {"tim hockin": "thockin"})
    'thockin'
    """
    stripped = raw.strip()
    if aliases:
        alias_hit = aliases.get(stripped.lower())
        if alias_hit is not None:
            return alias_hit.lstrip("@").lower()
    return stripped.lstrip("@").strip().lower()


def normalize_slug(raw: str) -> str:
    """Normalize a SIG name or slug to its controlled-vocabulary slug.

    ``kep.yaml``'s ``owning-sig`` already uses the same slug as the ``community``
    SIG directory (``sig-network``), so this is mostly idempotent; it also folds a
    human label (``SIG Network`` / ``Network``) onto the slug shape.

    >>> normalize_slug("sig-network")
    'sig-network'
    >>> normalize_slug("SIG Network")
    'sig-network'
    >>> normalize_slug("Network")
    'sig-network'
    """
    s = _SLUG_NON_ALNUM.sub("-", raw.strip().lower()).strip("-")
    if not s.startswith("sig-"):
        s = f"sig-{s}"
    return s


def kep_id(number: int | str) -> str:
    """Stable node ID for a KEP: ``kep-<number>``.

    Canonicalizes the source forms — an int from ``kep.yaml`` (``1287``), a
    zero-padded dir-name prefix (``0009``), or an already-prefixed string — to one
    ID, so the same KEP from two sources lands on one node.

    >>> kep_id(1287)
    'kep-1287'
    >>> kep_id("2086")
    'kep-2086'
    >>> kep_id("0009")
    'kep-9'
    """
    raw = str(number).strip()
    if raw.lower().startswith("kep-"):
        raw = raw[4:]
    if raw.isdigit():
        raw = str(int(raw))
    return f"kep-{raw}"


def person_id(handle_or_name: str, aliases: dict[str, str] | None = None) -> str:
    """Stable node ID for a Person: ``person:<normalized-handle>``."""
    return f"person:{normalize_handle(handle_or_name, aliases)}"


def sig_id(name_or_slug: str) -> str:
    """Stable node ID for a SIG: ``sig:<slug>``."""
    return f"sig:{normalize_slug(name_or_slug)}"


def subproject_id(sig_slug: str, name: str) -> str:
    """Stable node ID for a Subproject: ``subproject:<sig-slug>/<name>``."""
    safe = _SLUG_NON_ALNUM.sub("-", name.strip().lower()).strip("-")
    return f"subproject:{normalize_slug(sig_slug)}/{safe}"
