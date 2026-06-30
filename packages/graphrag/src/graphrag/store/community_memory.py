"""In-memory community store — the offline / test / demo backend (global-community-summary).

Holds ``Community`` records in a dict and applies the **identical** clearance predicate the
Neptune adapter applies (``tier`` within ``allowed_labels``; ``None`` ⇒ all, empty ⇒ none —
fail-closed), so for a given clearance it returns the same community set as the live backend
(the slice-4 backend-identical invariant). The per-entity ``communityId`` stamp is held in a
side map purely so the offline trace can answer "which community is entity X in".
"""

from __future__ import annotations

from .community_base import Community, CommunityStore


class MemoryCommunityStore(CommunityStore):
    """A ``CommunityStore`` backed by an in-memory dict."""

    def __init__(self) -> None:
        self._items: dict[str, Community] = {}
        self._entity_community: dict[str, str] = {}

    def upsert_community(self, community: Community) -> None:
        self._items[community.id] = community

    def set_community_id(self, entity_id: str, community_id: str) -> None:
        self._entity_community[entity_id] = community_id

    def community_of(self, entity_id: str) -> str | None:
        """The community an entity belongs to (offline trace affordance; not on the ABC)."""
        return self._entity_community.get(entity_id)

    def all_communities(self, *, allowed_labels: frozenset[str] | None = None) -> list[Community]:
        out = [
            c
            for c in self._items.values()
            # `None` ⇒ unrestricted; an empty set ⇒ nothing (fail-closed). The summary blends
            # all members, so it is gated by its composed (most-restrictive) tier.
            if allowed_labels is None or c.tier in allowed_labels
        ]
        # Stable order: largest first, then id — mirrors the detection ordering so the trace
        # is deterministic regardless of dict insertion order.
        out.sort(key=lambda c: (-c.size, c.id))
        return out

    def count(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        self._items = {}
        self._entity_community = {}
