"""Parent-Child Retriever — match on a small child chunk's vector, synthesize over the
larger parent document body (the graphrag.com *Parent-Child Retriever* pattern).

A flat chunk index forces one tradeoff: a small chunk matches precisely but truncates the
context handed to the LLM, while a large chunk gives context but dilutes the match vector.
Parent-child **decouples** the two — the **child** is sized for match precision (one
``chunk_corpus`` chunk = one vector), the **parent** is the whole document, returned in
full for synthesis context. On OpenSearch the two live in one nested document (RFC-0001 §3;
see ``store.parentchild_base``); the match runs **during** the nested ANN scan on the Lucene
engine, and synthesis reads the returned **parent body**, not the matched child fragment.

``group_into_parents`` (ingest-side) turns embedded chunks + a parent-body map into
``ParentDoc``s; ``parentchild_query`` (query-side) embeds the question, runs the nested
search, and synthesizes over the parent bodies with a legible trace.

PyYAML-free (imports ``chunk``/``embed``/``synthesize``/``visibility``/``store``, none of
which import ``yaml`` at module load) — so it bundles in the ``Code.from_asset`` query Lambda.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .chunk import Chunk
from .embed import Embedder
from .store.parentchild_base import ChildVector, ParentChildStore, ParentDoc, ParentHit
from .store.vector_base import EmbeddedChunk, VectorHit
from .synthesize import Synthesizer
from .visibility import Clearance

# Reuse the flat vector default-k so a parent-child query returns the same breadth.
DEFAULT_K = 5


def _parent_key(chunk_id: str) -> str:
    """The source-qualified ``{source}/{doc_path}`` parent key the chunk id embeds
    (``{source}/{path}#{ordinal}`` → strip the trailing ``#<ordinal>``)."""
    return chunk_id.rsplit("#", 1)[0]


def _ordinal(chunk_id: str) -> int:
    """The chunk's ordinal (the ``#<n>`` suffix); 0 when absent (defensive)."""
    suffix = chunk_id.rsplit("#", 1)
    return int(suffix[1]) if len(suffix) == 2 and suffix[1].isdigit() else 0


def group_into_parents(
    embedded_chunks: list[EmbeddedChunk], bodies: Mapping[str, str]
) -> list[ParentDoc]:
    """Group embedded child chunks into ``ParentDoc``s by their ``{source}/{doc_path}`` key.

    Children are ordered by ordinal; the parent ``body`` is taken from ``bodies`` (the
    document's full prose) — a parent key **absent** from ``bodies`` is a loud ``ValueError``,
    never a silent empty body. The parent inherits the document's ``entity_ids`` and
    ``visibility`` (a document's chunks share one composed tier), and its ``heading`` is the
    first (ordinal-0) child's heading — a stable parent label (the parent represents the whole
    document and is cited by ``doc_path``, not a single section).
    """
    groups: dict[str, list[EmbeddedChunk]] = {}
    order: list[str] = []
    for ec in embedded_chunks:
        key = _parent_key(ec.chunk.id)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(ec)

    parents: list[ParentDoc] = []
    for key in order:
        members = sorted(groups[key], key=lambda ec: _ordinal(ec.chunk.id))
        if key not in bodies:
            raise ValueError(f"no parent body for {key!r} (bodies map is missing the document)")
        first = members[0].chunk
        children = tuple(
            ChildVector(
                child_id=ec.chunk.id,
                heading=ec.chunk.heading,
                text=ec.chunk.text,
                vector=ec.vector,
            )
            for ec in members
        )
        parents.append(
            ParentDoc(
                parent_id=key,
                source=first.source,
                doc_path=first.doc_path,
                heading=first.heading,
                entity_ids=tuple(first.entity_ids),
                visibility=first.visibility,
                body=bodies[key],
                children=children,
            )
        )
    return parents


@dataclass
class ParentChildResult:
    """The traced output of a parent-child query: the matched children (precise) and the
    returned parents (full body), plus the answer synthesized over the parent bodies."""

    question: str
    hits: list[ParentHit] = field(default_factory=list)
    answer: str = ""
    citations: list[str] = field(default_factory=list)
    clearance: Clearance | None = None

    def render(self) -> str:
        """Narrate: question → matched child(ren) (precise) → returned parent(s) (full body)
        → answer — charter principle 1 (no black-box hop) as a data structure."""
        lines = ["== parentchild ==", f"question: {self.question}"]
        if self.clearance is not None:
            allowed = ", ".join(sorted(self.clearance.allowed))
            lines.append(
                f"clearance: persona={self.clearance.persona} allows=[{allowed}] "
                "(synthetic visibility labels — a teaching stand-in for ACLs, not real authz)"
            )
        lines.append("matched child per parent (the precise match):")
        for rank, hit in enumerate(self.hits, start=1):
            child = hit.matched_child
            child_label = (
                f"{child.child_id} # {child.heading or '(intro)'}"
                if child is not None
                else "(none)"
            )
            lines.append(f"  {rank}. score={hit.score:.4f}  {child_label}")
        if not self.hits:
            lines.append("  (no hits)")
        lines.append("returned parents (the full body synthesized over):")
        for rank, hit in enumerate(self.hits, start=1):
            parent = hit.parent
            lines.append(
                f"  {rank}. [{parent.source}] {parent.doc_path} "
                f"(body {len(parent.body)} chars, {len(parent.children)} child chunks)"
            )
        lines.append(f"answer: {self.answer}")
        return "\n".join(lines)


def _parent_as_context(hit: ParentHit) -> VectorHit:
    """Wrap a returned parent's **body** as the ``VectorHit`` the ``Synthesizer`` reads, so
    Claude sees the full parent context (not the matched child fragment) and the citation
    surface (``{source}:{doc_path}#{heading}``) resolves to the **parent**, not a child."""
    parent = hit.parent
    chunk = Chunk(
        id=parent.parent_id,
        text=parent.body,
        source=parent.source,
        doc_path=parent.doc_path,
        heading=parent.heading,
        entity_ids=list(parent.entity_ids),
        visibility=parent.visibility,
    )
    return VectorHit(chunk, hit.score)


def parentchild_query(
    question: str,
    *,
    store: ParentChildStore,
    embedder: Embedder,
    synthesizer: Synthesizer,
    k: int = DEFAULT_K,
    clearance: Clearance | None = None,
) -> ParentChildResult:
    """Embed ``question`` → nested child match (during ANN) → synthesize over the **parent
    bodies**, with a trace.

    The match is precise (a small child vector), the context is complete (the whole parent
    document body). When ``clearance`` is set (the slice-4 permission filter) the allowed
    visibility tiers are threaded into ``search`` as a parent-level filter composed AND with the
    child match — so a document above clearance is never returned (it can only narrow). ``None``
    = unrestricted.
    """
    vector = embedder.embed([question])[0]
    allowed = clearance.allowed if clearance is not None else None
    hits = store.search(vector, k, allowed_labels=allowed)
    context = [_parent_as_context(hit) for hit in hits]
    synthesis = synthesizer.synthesize(question, context, [])
    return ParentChildResult(
        question=question,
        hits=hits,
        answer=synthesis.answer,
        citations=synthesis.citations,
        clearance=clearance,
    )
