"""Ingestion orchestration — parse → resolve → write, with a narratable report.

Resolution happens in an in-memory ``Graph`` first (so the merge is
backend-independent), then the resolved nodes/edges are written to the target
store. The ``IngestReport`` is the narration the demo prints: parsed counts, the
entity/edge tallies, and — the punchline — which entities resolved across *both*
sources (AC10).

This slice does a full, idempotent upsert only; delta/orphan-removal is slice 5.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .labels import label_graph, load_labels
from .model import Graph
from .resolve import cross_source_merges, load_aliases, resolve
from .sources import load_corpus
from .store.base import GraphStore


@dataclass
class IngestReport:
    parsed_docs: int = 0
    nodes: int = 0
    edges: int = 0
    by_entity_kind: dict[str, int] = field(default_factory=dict)
    by_edge_kind: dict[str, int] = field(default_factory=dict)
    merges: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "== ingest ==",
            f"parsed docs: {self.parsed_docs}",
            f"nodes: {self.nodes}  edges: {self.edges}",
            "entities: " + ", ".join(f"{k}={v}" for k, v in sorted(self.by_entity_kind.items())),
            "edges:    " + ", ".join(f"{k}={v}" for k, v in sorted(self.by_edge_kind.items())),
            f"cross-source resolved nodes ({len(self.merges)}):",
        ]
        lines += [f"  - {m} (appeared in both sources -> one node)" for m in self.merges]
        return "\n".join(lines)


def _report(graph: Graph, parsed_docs: int) -> IngestReport:
    return IngestReport(
        parsed_docs=parsed_docs,
        nodes=len(graph.nodes),
        edges=len(graph.edges),
        by_entity_kind=dict(Counter(n.kind.value for n in graph.nodes.values())),
        by_edge_kind=dict(Counter(e.kind.value for e in graph.edges)),
        merges=cross_source_merges(graph),
    )


def ingest(
    community_root: Path,
    enhancements_root: Path,
    store: GraphStore,
    aliases: dict[str, str] | None = None,
    labels: dict[str, str] | None = None,
) -> IngestReport:
    """Parse both sources, resolve, label visibility, write into ``store``, and report.

    The synthetic visibility labels (slice 4) are stamped onto the resolved graph's nodes
    and edges **before** the upsert, so the deployed store carries node/edge ``visibility``
    props for the during-traversal permission filter — written from the same pass as every
    other property (charter pattern 2). ``labels`` defaults to the packaged ``labels.yaml``.
    """
    docs = load_corpus(community_root, enhancements_root)
    graph = resolve(docs, aliases if aliases is not None else load_aliases())
    label_graph(graph, labels if labels is not None else load_labels())
    for node in graph.nodes.values():
        store.upsert_node(node)
    for edge in graph.edges:
        store.upsert_edge(edge)
    return _report(graph, parsed_docs=len(docs))
