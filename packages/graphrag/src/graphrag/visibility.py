"""Synthetic visibility labels + persona clearance — a TEACHING stand-in for ACLs.

**Not real authorization** (charter principle 5 / design D1 Non-goals): these labels stand
in for real access-control so an architect can *see* permission-filtered retrieval ride the
same query path as the three modes — they are never production IAM, multi-tenancy, or data
authz. This module is the **read side**: an ordered visibility tier scale, a
most-restrictive-wins ``compose``, and a ``Clearance`` resolved from a named ``persona``.

It is **pure** — no ``yaml``, no store, no network — so the in-VPC query Lambda (PyYAML-free
``Code.from_asset`` bundle) imports it freely. Label *assignment* (which reads the packaged
``labels.yaml``) lives on the ingest path in ``labels.py`` and is never imported here.

Fail-closed by construction:

- an **unknown persona** raises ``ValueError`` (no default-allow fallthrough);
- a ``Clearance`` with an **empty** ``allowed`` set sees nothing — it never falls through to
  unrestricted; only the *query layer's* literal ``clearance=None`` means unrestricted, the
  opt-in teaching default that is safe only because the labels are non-authz behind a
  trusted, IAM-auth scoped-principal ingress (see the slice-4 spec Boundaries).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Visibility(StrEnum):
    """An ordered sensitivity tier. Derived labels compose most-restrictive-wins."""

    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"


# Ascending sensitivity rank, keyed by the tier string. Anything unlabeled / unknown is
# treated as the least-restrictive tier (PUBLIC) — the safe default for a teaching demo.
_RANK: dict[str, int] = {
    Visibility.PUBLIC.value: 0,
    Visibility.INTERNAL.value: 1,
    Visibility.RESTRICTED.value: 2,
}

DEFAULT_VISIBILITY = Visibility.PUBLIC.value


def rank(label: str) -> int:
    """Sensitivity rank of a tier string (unknown → PUBLIC's rank, the safe default)."""
    return _RANK.get(label, 0)


def compose(*labels: str) -> str:
    """The most-restrictive (max-rank) of the given tiers; empty → ``public``.

    An edge is as sensitive as its more-sensitive endpoint; a chunk as its most-sensitive
    owning entity — so ``compose`` is how every derived label is built from its inputs.
    """
    best = Visibility.PUBLIC.value
    for label in labels:
        if rank(label) > rank(best):
            best = label
    return best


@dataclass(frozen=True)
class Clearance:
    """A persona's clearance — the set of tiers it may see; downward-closed by construction.

    ``allowed`` empty ⇒ sees nothing (fail-closed). A teaching stand-in for an ACL
    principal, never real authz.
    """

    persona: str
    allowed: frozenset[str]

    def allows(self, label: str) -> bool:
        """True iff ``label`` is within this clearance."""
        return label in self.allowed


def _at_or_below(level: Visibility) -> frozenset[str]:
    """The downward-closed tier set at or below ``level`` (rank-wise)."""
    return frozenset(v.value for v in Visibility if rank(v.value) <= rank(level.value))


# The synthetic demo personas, each mapping to the downward-closed set of tiers it may see.
# Hand-authored and small (charter principle 1 — narratable). Changing this set is an
# Ask-first boundary in the slice-4 spec.
PERSONAS: dict[str, frozenset[str]] = {
    "public-reader": _at_or_below(Visibility.PUBLIC),
    "member": _at_or_below(Visibility.INTERNAL),
    "maintainer": _at_or_below(Visibility.RESTRICTED),
}


def resolve_clearance(persona: str) -> Clearance:
    """Resolve a persona name to its ``Clearance``; an unknown persona raises ``ValueError``.

    Fail-closed: there is no default-allow fallthrough — an unrecognized persona is an
    error, not silent unrestricted access.
    """
    allowed = PERSONAS.get(persona)
    if allowed is None:
        known = ", ".join(sorted(PERSONAS))
        raise ValueError(f"unknown persona {persona!r}; known personas: {known}")
    return Clearance(persona=persona, allowed=allowed)
