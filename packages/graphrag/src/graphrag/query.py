"""Multi-hop traversal with a visible trace — the narratable graph query.

Traversal runs in the application layer over ``GraphStore.neighbors`` (not pushed
into openCypher), so the trace is identical on every backend. Each step expands the
current frontier along one edge kind + direction; the result carries an ordered
trace naming every seed, every hop, and every node it reached — charter principle 1
(no black-box hop) made into a data structure (AC6 / AC10).

Bounds (ADR-0001): hops are capped (1–2) and the frontier is capped, so two seed
sources cannot over-expand and bury the answer; truncation is recorded in the
trace, never silent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Direction, EdgeKind, Node, extraction_method_for_kind
from .store.base import GraphStore
from .visibility import Clearance

DEFAULT_MAX_HOPS = 2
DEFAULT_FRONTIER_CAP = 50

# A traversal step: follow ``edge_kind`` in ``direction``.
Step = tuple[EdgeKind, Direction]


@dataclass
class TraceEntry:
    hop: int
    edge_kind: EdgeKind
    direction: Direction
    from_ids: list[str]
    to_ids: list[str]
    truncated: bool = False

    @property
    def extraction_method(self) -> str:
        """The provenance method of this hop's edge kind (``deterministic`` /
        ``schema-guided-llm``) — so a path that traversed a model-asserted edge shows it in the
        trace and is never blended silently into an answer (AC11)."""
        return extraction_method_for_kind(self.edge_kind)


@dataclass
class TraversalResult:
    seed_ids: list[str]
    trace: list[TraceEntry] = field(default_factory=list)
    result_ids: list[str] = field(default_factory=list)

    def render(self) -> str:
        """Render the trace as a human-readable narration."""
        lines = [f"seeds: {', '.join(self.seed_ids) or '(none)'}"]
        for entry in self.trace:
            arrow = "->" if entry.direction is Direction.OUT else "<-"
            to = ", ".join(entry.to_ids) or "(none)"
            note = "  [frontier truncated]" if entry.truncated else ""
            lines.append(
                f"  hop {entry.hop}: {entry.edge_kind.value} {entry.direction.value} "
                f"[{entry.extraction_method}] {arrow} {to}{note}"
            )
        lines.append(f"result: {', '.join(self.result_ids) or '(none)'}")
        return "\n".join(lines)


def _dedupe(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def traverse(
    store: GraphStore,
    seed_ids: list[str],
    steps: list[Step],
    *,
    max_hops: int = DEFAULT_MAX_HOPS,
    frontier_cap: int = DEFAULT_FRONTIER_CAP,
    clearance: Clearance | None = None,
) -> TraversalResult:
    """Expand ``seed_ids`` through ``steps`` (one hop each), tracing every hop.

    When ``clearance`` is set (slice-4 permission filter), each hop filters edges by
    visibility during traversal, so a forbidden node never enters the frontier — the same
    leak guard as ``expand_neighborhood``. ``None`` = unfiltered.
    """
    if len(steps) > max_hops:
        raise ValueError(f"{len(steps)} hops requested exceeds max_hops={max_hops}")

    allowed = clearance.allowed if clearance is not None else None
    # The seed is an *explicit* user-named start (the typed-path `graph-query` verb), so it
    # is shown as-is — seed-visibility filtering is the orchestration layer's job
    # (hybrid/compare drop+record a forbidden seed). Here the clearance filters the *hops*:
    # a forbidden neighbor is excluded during traversal, so the path cannot reach past it.
    # No separate final-set guard is needed (unlike hybrid/graph-only, which re-resolve
    # nodes via resolve_nodes and could re-materialize one): every node in `result_ids`
    # is a neighbor that already passed the edge predicate's `neighbor.visibility ∈ allowed`
    # check, so the final frontier is within clearance by construction.
    frontier = _dedupe(seed_ids)
    result = TraversalResult(seed_ids=frontier)

    for hop, (edge_kind, direction) in enumerate(steps, start=1):
        reached: list[str] = []
        for node_id in frontier:
            reached.extend(
                n.id for n in store.neighbors(node_id, edge_kind, direction, allowed_labels=allowed)
            )
        reached = _dedupe(reached)
        truncated = len(reached) > frontier_cap
        if truncated:
            reached = reached[:frontier_cap]
        result.trace.append(
            TraceEntry(hop, edge_kind, direction, list(frontier), reached, truncated)
        )
        frontier = reached

    result.result_ids = frontier
    return result


def resolve_nodes(store: GraphStore, ids: list[str]) -> list[Node]:
    """Look up nodes for a list of IDs (skipping any that are absent)."""
    return [n for n in (store.get_node(i) for i in ids) if n is not None]


# --- Seed-and-expand neighborhood (slice-3 AC3) -----------------------------------


@dataclass
class NeighborhoodTrace:
    """One hop of an undirected-over-all-edge-kinds expansion.

    ``frontier_in`` is the set this hop expanded from; ``reached`` is the new nodes it
    discovered; ``edge_kinds`` names every edge kind that contributed a reached node;
    ``truncated`` records a hop whose frontier exceeded ``frontier_cap``.
    """

    hop: int
    frontier_in: list[str]
    reached: list[str]
    edge_kinds: list[EdgeKind] = field(default_factory=list)
    truncated: bool = False

    @property
    def extraction_methods(self) -> list[str]:
        """The distinct provenance methods of the edge kinds that contributed this hop, sorted.

        Seed-and-expand traverses **all** edge kinds, so a schema-guided-llm edge rides into the
        neighborhood by default (the teaching payoff — an answer reachable only via an LLM edge).
        Surfacing the per-hop method here is what keeps that honest: any answer leaning on a
        model-asserted edge shows ``schema-guided-llm`` in its trace, never blended silently
        (AC11). Derived from the edge kind via the disjoint-set invariant (``model.py``)."""
        return sorted({extraction_method_for_kind(k) for k in self.edge_kinds})


@dataclass
class NeighborhoodResult:
    seed_ids: list[str]
    trace: list[NeighborhoodTrace] = field(default_factory=list)
    result_ids: list[str] = field(default_factory=list)

    def render(self) -> str:
        """Render the expansion as a human-readable narration (charter principle 1)."""
        lines = [f"seeds: {', '.join(self.seed_ids) or '(none)'}"]
        for entry in self.trace:
            kinds = ", ".join(ek.value for ek in entry.edge_kinds) or "(none)"
            methods = ", ".join(entry.extraction_methods) or "(none)"
            reached = ", ".join(entry.reached) or "(none)"
            note = "  [frontier truncated]" if entry.truncated else ""
            lines.append(f"  hop {entry.hop}: via {kinds} [{methods}] -> {reached}{note}")
        lines.append(f"reached: {', '.join(self.result_ids) or '(none)'}")
        return "\n".join(lines)


def expand_neighborhood(
    store: GraphStore,
    seed_ids: list[str],
    *,
    max_hops: int = DEFAULT_MAX_HOPS,
    frontier_cap: int = DEFAULT_FRONTIER_CAP,
    clearance: Clearance | None = None,
) -> NeighborhoodResult:
    """Expand ``seed_ids`` up to ``max_hops`` over **all** edge kinds in both directions.

    The graph-side twin of ``traverse``: where ``traverse`` follows a fixed
    edge-kind/direction path, this gathers the whole neighborhood (every ``EdgeKind`` ×
    ``Direction``), so seed-and-expand collects structural facts around a seed without
    naming the path. It expands over the ``GraphStore.neighbors_batch`` seam — a default
    app-layer fan-out over ``neighbors()`` (in-memory) or a backend-batched query
    (Neptune) — and **sorts** the reached set + edge kinds each hop, so the trace is
    identical regardless of which backend or what order the store returns edges in.

    Bounds (ADR-0001): a hop whose newly-reached frontier exceeds ``frontier_cap`` is
    truncated and the truncation recorded. Because the reached set is **sorted before**
    the cap, the surviving nodes are the lexicographically-smallest IDs (a deterministic
    set), not the first-discovered ones — the price of a backend-independent trace. An
    empty seed set expands to nothing. Returns the reached node IDs (cumulative,
    excluding the seeds) and the per-hop trace.

    When ``clearance`` is set (slice-4 permission filter — a teaching stand-in for an ACL,
    not real authz), the allowed visibility tiers are threaded into ``neighbors_batch`` so
    the filter is applied **during traversal, on edges**: a forbidden node never enters the
    frontier, never appears in the trace, and so can never bridge to a node reachable only
    through it. Seed-visibility filtering (dropping a forbidden seed before expansion) is
    the orchestration layer's job — by the time seeds reach here they are already cleared.
    """
    allowed = clearance.allowed if clearance is not None else None
    seeds = _dedupe(seed_ids)
    result = NeighborhoodResult(seed_ids=seeds)
    visited: set[str] = set(seeds)
    frontier = list(seeds)

    for hop in range(1, max_hops + 1):
        if not frontier:
            break
        # One batched fetch per hop (Neptune: two openCypher queries; in-memory: a
        # fan-out over neighbors()). The reached set + contributing edge kinds are
        # sorted before recording, so the trace is deterministic and identical across
        # backends regardless of the order the store returns edges in.
        edges = store.neighbors_batch(frontier, allowed_labels=allowed)
        newly = {e.neighbor.id for e in edges if e.neighbor.id not in visited}
        truncated = len(newly) > frontier_cap
        reached = sorted(newly)[:frontier_cap]
        reached_set = set(reached)
        contributing = sorted(
            {e.edge_kind for e in edges if e.neighbor.id in reached_set},
            key=lambda k: k.value,
        )
        result.trace.append(
            NeighborhoodTrace(
                hop=hop,
                frontier_in=list(frontier),
                reached=list(reached),
                edge_kinds=list(contributing),
                truncated=truncated,
            )
        )
        visited.update(reached)
        result.result_ids.extend(reached)
        frontier = reached

    return result
