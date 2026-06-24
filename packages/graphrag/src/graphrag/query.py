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

from .model import Direction, EdgeKind, Node
from .store.base import GraphStore

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
                f"{arrow} {to}{note}"
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
) -> TraversalResult:
    """Expand ``seed_ids`` through ``steps`` (one hop each), tracing every hop."""
    if len(steps) > max_hops:
        raise ValueError(f"{len(steps)} hops requested exceeds max_hops={max_hops}")

    frontier = _dedupe(seed_ids)
    result = TraversalResult(seed_ids=frontier)

    for hop, (edge_kind, direction) in enumerate(steps, start=1):
        reached: list[str] = []
        for node_id in frontier:
            reached.extend(n.id for n in store.neighbors(node_id, edge_kind, direction))
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
