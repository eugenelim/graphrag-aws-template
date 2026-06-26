"""The ``CommunityStore`` seam — persists per-community summaries (global-community-summary).

The Global Community Summary pattern detects communities over the entity graph (Louvain,
in the Fargate ingest task — ``community_detect.py``, ADR-0005) and stores one
LLM-generated **summary** per community. Those summaries are the corpus-wide map-reduce
substrate the ``global`` query mode reads (``globalsearch.py``).

A ``Community`` is a distinct concern from the entity graph (a different Neptune node label,
``Community``), so it gets its own store family — the ``NeptuneCommunityStore`` writes/reads
``Community`` nodes on the **existing** cluster (no new service, ADR-0002), and the
``MemoryCommunityStore`` is the offline / test / demo twin. Both apply the **same**
clearance predicate so a given clearance returns the same community set offline and live
(the slice-4 backend-identical invariant).

This module is **PyYAML-free and networkx-free** (pure dataclasses + an ABC), so it bundles
in the ``Code.from_asset`` query Lambda — which reads community summaries but never detects.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class Community:
    """A detected community plus its LLM-generated summary — the unit stored in Neptune and
    map-reduced at query time.

    ``tier`` is the community's composed (most-restrictive) member visibility; a summary
    blends all its members, so it is served only to a persona whose clearance dominates
    ``tier`` (``globalsearch.global_query``). ``entity_ids`` is the canonical membership (the
    community↔entity mapping); the member ``Entity`` nodes additionally carry a
    ``communityId`` property (set via ``CommunityStore.set_community_id``)."""

    id: str
    title: str
    summary: str
    entity_ids: tuple[str, ...]
    tier: str
    size: int


class CommunityStore(ABC):
    """A backend that persists ``Community`` summaries and reads them back, clearance-gated."""

    def create(self) -> None:  # noqa: B027  (intentional optional hook, not abstract)
        """Create any backend schema needed before writing. No-op by default (the in-memory
        store has nothing to create; the Neptune label is schema-less)."""

    @abstractmethod
    def upsert_community(self, community: Community) -> None: ...

    @abstractmethod
    def set_community_id(self, entity_id: str, community_id: str) -> None:
        """Stamp ``communityId`` on a member ``Entity`` node — the narratable entity→community
        affordance (and the literal "write communityId back" of the feasibility note). The
        canonical membership is the ``Community`` node's ``entity_ids``; this is the denormalized
        per-entity mirror, written from the same one detection pass so the two cannot disagree."""

    @abstractmethod
    def all_communities(
        self, *, allowed_labels: frozenset[str] | None = None
    ) -> list[Community]:
        """Every stored community whose ``tier`` is within ``allowed_labels``.

        The clearance gate for corpus-wide summaries (a teaching stand-in for an ACL, never
        real authz): a summary blends all its members, so it is served only if the persona's
        clearance dominates the community's composed tier. ``None`` ⇒ unrestricted (return
        all); an **empty** set ⇒ **nothing** (fail-closed — the slice-4 ``None``-vs-empty
        semantics). The filter is applied here, **before** the map step, so an above-clearance
        community never reaches the synthesizer or the trace."""

    @abstractmethod
    def count(self) -> int: ...

    @abstractmethod
    def clear(self) -> None:
        """Remove every community (the full-ingest / ``--rebuild`` rebuild resets them)."""
