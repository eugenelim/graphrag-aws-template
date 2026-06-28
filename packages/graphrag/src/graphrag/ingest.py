"""Ingestion orchestration — parse → resolve → write, with a narratable report.

Resolution happens in an in-memory ``Graph`` first (so the merge is
backend-independent), then the resolved nodes/edges are written to the target
store. The ``IngestReport`` is the narration the demo prints: parsed counts, the
entity/edge tallies, and — the punchline — which entities resolved across *both*
sources (AC10).

This slice does a full, idempotent upsert only; delta/orphan-removal is slice 5.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .chunk import chunk_corpus
from .delta import Delta, Manifest, diff_manifests, manifest_from_docs
from .embed import Embedder
from .graphdelta import apply_graph_delta, plan_graph_delta
from .labels import label_chunks, label_graph, load_labels
from .model import Graph
from .resolve import cross_source_merges, load_aliases, resolve
from .sources import load_corpus
from .store.base import GraphStore
from .store.vector_base import EmbeddedChunk, VectorStore

logger = logging.getLogger("graphrag.ingest")


def _safe_count(vector_store: VectorStore) -> int:
    """Chunk count for the narration, degrading to ``-1`` ("unknown") on a backend error — a
    cosmetic trace value must never gate the delta or the manifest write (a live OpenSearch
    ``_count`` is a network round-trip)."""
    try:
        return vector_store.count()
    except Exception:  # narration only — never fail the delta on a count round-trip
        logger.warning("vector_store.count() failed; chunk count unknown", exc_info=True)
        return -1


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


def _reconcile_graph(store: GraphStore, scratch: Graph, removed_ids: set[str]) -> int:
    """Reconcile the store to the new corpus state by provenance set, returning the orphan count.

    The thin composition of the plan/apply pair (medallion-staging T3): plan the reconciliation as
    a `GraphDelta` (pure), then apply it (the single mutating step). Behavior is unchanged from the
    pre-refactor inline implementation — byte-identical store state and the same set of mutating
    calls (unchanged rows are never re-written; an edge incident to a deleted node is removed by the
    node cascade, not a separate delete). See `graphdelta.plan_graph_delta` / `apply_graph_delta`.
    """
    return apply_graph_delta(store, plan_graph_delta(store, scratch, removed_ids))


@dataclass
class DeltaReport:
    """The narratable result of a ``--delta`` run — counts before, the classified delta, the
    orphans removed, and counts after (charter principle 1; AC10). Carries ``new_manifest`` so
    the caller persists it (AC8)."""

    delta: Delta
    new_manifest: Manifest
    before_nodes: int
    before_edges: int
    before_chunks: int
    after_nodes: int
    after_edges: int
    after_chunks: int
    orphans_removed: int
    indexed_chunks: int
    full_ingest: bool = False  # the no-prior-manifest fallback ran a full ingest (AC8b)

    def render(self) -> str:
        d = self.delta
        lines = [
            "== delta re-ingest ==",
            "(full ingest — no prior manifest)" if self.full_ingest else "(incremental)",
            f"added: {len(d.added)}  changed: {len(d.changed)}  "
            f"deleted: {len(d.deleted)}  moved: {len(d.moved)}",
        ]
        for did in d.added:
            lines.append(f"  + {did}")
        for did in d.changed:
            lines.append(f"  ~ {did}")
        for did in d.deleted:
            lines.append(f"  - {did}")
        for old, new in d.moved:
            lines.append(f"  > {old} -> {new}")

        def _c(n: int) -> str:
            return "?" if n < 0 else str(n)  # -1 = count unavailable (a backend error)

        lines += [
            f"orphans removed: {self.orphans_removed}",
            f"nodes: {self.before_nodes} -> {self.after_nodes}   "
            f"edges: {self.before_edges} -> {self.after_edges}   "
            f"chunks: {_c(self.before_chunks)} -> {_c(self.after_chunks)}",
            f"re-embedded chunks (delta only): {self.indexed_chunks}",
        ]
        return "\n".join(lines)


def ingest_delta(
    prev_manifest: Manifest | None,
    community_root: Path,
    enhancements_root: Path,
    graph_store: GraphStore,
    vector_store: VectorStore,
    embedder: Embedder,
    aliases: dict[str, str] | None = None,
    labels: dict[str, str] | None = None,
) -> DeltaReport:
    """Re-ingest only the delta between ``prev_manifest`` and the new snapshot, keeping both
    stores consistent with an explicit orphan-removal pass (slice 5; charter pattern 8).

    The new manifest is built and the ``Delta`` is classified **internally** (so the
    no-prior-manifest fallback — ``prev_manifest is None`` → a full ingest of both stores — is
    pure logic callable with no S3, AC8b). Only added/changed/moved-to documents are re-parsed,
    re-chunked, and re-embedded; unchanged documents are never re-embedded (AC2). Graph
    reconciliation is provenance-set based (no full recompute); vector deletion is by source-
    qualified document id.
    """
    aliases = aliases if aliases is not None else load_aliases()
    labels = labels if labels is not None else load_labels()

    docs = load_corpus(community_root, enhancements_root)
    new_manifest = manifest_from_docs(docs, community_root, enhancements_root)
    delta = diff_manifests(prev_manifest, new_manifest)

    before_nodes = len(graph_store.all_nodes())
    before_edges = len(graph_store.all_edges())
    before_chunks = _safe_count(vector_store)

    added_ids = delta.added_doc_ids()
    removed_ids = delta.removed_doc_ids()
    delta_docs = [doc for doc in docs if doc.doc_id in added_ids]
    logger.info(
        "delta%s: +%d ~%d -%d >%d (%d docs to re-ingest)",
        " (full — no prior manifest)" if prev_manifest is None else "",
        len(delta.added),
        len(delta.changed),
        len(delta.deleted),
        len(delta.moved),
        len(delta_docs),
    )

    # Graph: resolve only the delta docs into a scratch graph, then reconcile by provenance.
    scratch = resolve(delta_docs, aliases)
    label_graph(scratch, labels)
    orphans = _reconcile_graph(graph_store, scratch, removed_ids)
    logger.info("graph reconciled: %d orphan node(s)/edge(s) removed", orphans)

    # Vector: delete chunks of EVERY doc the delta touches (removed + added/changed/moved-to),
    # then re-index the added/changed/moved-to set. Deleting the added set too — not only the
    # removed set — makes a retry after a partial failure idempotent: a crashed run's chunks are
    # cleared before re-indexing, so OpenSearch's auto-id `_doc` POST can't accumulate duplicates.
    touched_ids = removed_ids | added_ids
    if touched_ids:
        logger.info("vector: deleting chunks of %d touched doc(s)", len(touched_ids))
        vector_store.delete_by_doc(sorted(touched_ids))
    chunks = chunk_corpus(delta_docs)
    label_chunks(chunks, labels)
    vectors = embedder.embed([c.text for c in chunks]) if chunks else []
    for chunk, vector in zip(chunks, vectors, strict=True):
        vector_store.index_chunk(EmbeddedChunk(chunk, vector))
    logger.info("vector: indexed %d delta chunk(s)", len(chunks))

    return DeltaReport(
        delta=delta,
        new_manifest=new_manifest,
        before_nodes=before_nodes,
        before_edges=before_edges,
        before_chunks=before_chunks,
        after_nodes=len(graph_store.all_nodes()),
        after_edges=len(graph_store.all_edges()),
        after_chunks=_safe_count(vector_store),
        orphans_removed=orphans,
        indexed_chunks=len(chunks),
        full_ingest=prev_manifest is None,
    )


def rebuild(
    community_root: Path,
    enhancements_root: Path,
    graph_store: GraphStore,
    vector_store: VectorStore,
    embedder: Embedder,
    aliases: dict[str, str] | None = None,
    labels: dict[str, str] | None = None,
) -> DeltaReport:
    """The ``--rebuild`` escape hatch (slice 5; charter pattern 8): clear both stores, then
    full-ingest from scratch. The ground-truth reset — identical end state to a clean first
    ingest. Returns a ``DeltaReport`` (every document classified as added).

    Blast radius is the **whole** graph + vector store **by design** — and that is safe because
    the Neptune cluster and OpenSearch domain are single-tenant to this demo (ADR-0002's
    ephemeral, teardown-first topology). A future multi-tenant reuse must not inherit this
    full-wipe without re-scoping ``GraphStore.clear`` / ``VectorStore.clear``."""
    logger.warning("rebuild: clearing BOTH stores (full wipe) before re-ingest")
    graph_store.clear()
    vector_store.clear()
    return ingest_delta(
        None,
        community_root,
        enhancements_root,
        graph_store,
        vector_store,
        embedder,
        aliases=aliases,
        labels=labels,
    )
