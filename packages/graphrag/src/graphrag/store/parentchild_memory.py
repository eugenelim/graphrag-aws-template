"""In-memory nested parent-child store — the offline / test / demo backend (parent-child slice).

Scores each parent by its **best** child's cosine (the in-memory twin of the OpenSearch
nested query's ``score_mode: max``) and applies the identical parent-level visibility
predicate, so for a given query + clearance it returns the same parent hit set as the
OpenSearch adapter (the slice-4 backend-identical invariant)."""

from __future__ import annotations

from .parentchild_base import ParentChildStore, ParentDoc, ParentHit
from .vector_memory import cosine


class MemoryParentChildStore(ParentChildStore):
    """A ``ParentChildStore`` backed by an in-memory dict, scored by best-child cosine on query."""

    def __init__(self) -> None:
        self._items: dict[str, ParentDoc] = {}

    def index_parent(self, parent: ParentDoc) -> None:
        self._items[parent.parent_id] = parent

    def search(
        self,
        vector: list[float],
        k: int,
        *,
        allowed_labels: frozenset[str] | None = None,
    ) -> list[ParentHit]:
        hits: list[ParentHit] = []
        for parent in self._items.values():
            # slice-4 permission filter: a parent above clearance is not even a candidate.
            # `None` ⇒ unrestricted; an empty set ⇒ matches nothing (fail-closed).
            if allowed_labels is not None and parent.visibility not in allowed_labels:
                continue
            best_child = None
            best_score = float("-inf")  # -inf so the first child always wins on its real score
            for child in parent.children:
                score = cosine(vector, child.vector)
                if score > best_score:
                    best_child = child
                    best_score = score
            if best_child is not None:  # a parent with no children is never a candidate
                hits.append(ParentHit(parent=parent, score=best_score, matched_child=best_child))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[: max(0, k)]

    def count(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        self._items = {}
