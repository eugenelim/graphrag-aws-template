"""Parent-child data model + grouping (AC1) and orchestration + trace (AC3).

# STUB: AC1
# STUB: AC3
"""

from __future__ import annotations

import builtins
import importlib
import sys
from typing import Any

import pytest

from graphrag.chunk import Chunk
from graphrag.parentchild import (
    DEFAULT_K,
    ParentChildResult,
    group_into_parents,
    parentchild_query,
)
from graphrag.store.parentchild_base import ChildVector, ParentDoc
from graphrag.store.parentchild_memory import MemoryParentChildStore
from graphrag.store.vector_base import EmbeddedChunk, VectorHit
from graphrag.synthesize import SynthesisResult
from graphrag.visibility import Clearance


def _ec(
    chunk_id: str,
    text: str,
    *,
    source: str,
    doc_path: str,
    heading: str,
    entity_ids: list[str],
    visibility: str,
    vector: list[float],
) -> EmbeddedChunk:
    return EmbeddedChunk(
        Chunk(
            id=chunk_id,
            text=text,
            source=source,
            doc_path=doc_path,
            heading=heading,
            entity_ids=entity_ids,
            visibility=visibility,
        ),
        vector,
    )


# --- AC1: grouping --------------------------------------------------------------------


def test_group_into_parents_groups_by_parent_key_ordered_by_ordinal() -> None:
    # Two chunks of one doc (out of ordinal order) + one chunk of a second doc.
    chunks = [
        _ec(
            "enhancements/keps/sig-node/1287-x/README.md#1",
            "second section",
            source="enhancements",
            doc_path="keps/sig-node/1287-x/README.md",
            heading="Design",
            entity_ids=["kep-1287", "sig:sig-node"],
            visibility="public",
            vector=[1.0, 0.0],
        ),
        _ec(
            "enhancements/keps/sig-node/1287-x/README.md#0",
            "first section",
            source="enhancements",
            doc_path="keps/sig-node/1287-x/README.md",
            heading="Summary",
            entity_ids=["kep-1287", "sig:sig-node"],
            visibility="public",
            vector=[0.0, 1.0],
        ),
        _ec(
            "community/sig-node/README.md#0",
            "charter",
            source="community",
            doc_path="sig-node/README.md",
            heading="Charter",
            entity_ids=["sig:sig-node"],
            visibility="public",
            vector=[0.5, 0.5],
        ),
    ]
    bodies = {
        "enhancements/keps/sig-node/1287-x/README.md": "FULL KEP BODY",
        "community/sig-node/README.md": "FULL CHARTER BODY",
    }
    parents = group_into_parents(chunks, bodies)

    assert len(parents) == 2
    kep = next(p for p in parents if p.parent_id == "enhancements/keps/sig-node/1287-x/README.md")
    # children ordered by ordinal (0 before 1)
    assert [c.child_id for c in kep.children] == [
        "enhancements/keps/sig-node/1287-x/README.md#0",
        "enhancements/keps/sig-node/1287-x/README.md#1",
    ]
    assert kep.body == "FULL KEP BODY"
    assert kep.source == "enhancements"
    assert kep.doc_path == "keps/sig-node/1287-x/README.md"
    # heading == the ordinal-0 child's heading (a stable parent label)
    assert kep.heading == "Summary"
    # entity_ids / visibility inherited from the document's chunks
    assert kep.entity_ids == ("kep-1287", "sig:sig-node")
    assert kep.visibility == "public"


def test_group_into_parents_same_doc_path_across_sources_stays_distinct() -> None:
    chunks = [
        _ec(
            "community/README.md#0",
            "c",
            source="community",
            doc_path="README.md",
            heading="H",
            entity_ids=[],
            visibility="public",
            vector=[1.0],
        ),
        _ec(
            "enhancements/README.md#0",
            "e",
            source="enhancements",
            doc_path="README.md",
            heading="H",
            entity_ids=[],
            visibility="public",
            vector=[1.0],
        ),
    ]
    bodies = {"community/README.md": "C", "enhancements/README.md": "E"}
    parents = group_into_parents(chunks, bodies)
    assert {p.parent_id for p in parents} == {"community/README.md", "enhancements/README.md"}


def test_group_into_parents_missing_body_raises() -> None:
    chunks = [
        _ec(
            "community/sig-x/README.md#0",
            "t",
            source="community",
            doc_path="sig-x/README.md",
            heading="H",
            entity_ids=[],
            visibility="public",
            vector=[1.0],
        ),
    ]
    with pytest.raises(ValueError, match="no parent body"):
        group_into_parents(chunks, bodies={})  # the document's body is missing


def test_parentchild_modules_are_pyyaml_free() -> None:
    """`import graphrag.parentchild` + `store.parentchild_base` must not pull in yaml
    (they ride the Code.from_asset query Lambda bundle)."""
    real_import = builtins.__import__

    def _blocking(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "yaml" or name.startswith("yaml."):
            raise ImportError("yaml is not bundled in the query Lambda")
        return real_import(name, *args, **kwargs)

    def _is_target(mod: str) -> bool:
        return mod == "yaml" or mod.startswith("yaml.") or mod.startswith("graphrag")

    saved = {m: sys.modules.pop(m) for m in list(sys.modules) if _is_target(m)}
    builtins.__import__ = _blocking
    try:
        importlib.import_module("graphrag.parentchild")
        importlib.import_module("graphrag.store.parentchild_base")
        importlib.import_module("graphrag.store.parentchild_memory")
        importlib.import_module("graphrag.store.parentchild_opensearch")
        assert "graphrag.labels" not in sys.modules
    finally:
        builtins.__import__ = real_import
        for m in [m for m in list(sys.modules) if _is_target(m)]:
            sys.modules.pop(m, None)
        sys.modules.update(saved)


# --- AC3: orchestration + trace -------------------------------------------------------


class FixedEmbedder:
    """A deterministic embedder returning a preset query vector (so the match is fixed)."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    @property
    def model_id(self) -> str:
        return "fixed-test"

    @property
    def dimensions(self) -> int:
        return len(self._vector)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(self._vector) for _ in texts]


class SpySynthesizer:
    """Records the context chunks it was handed, so a test can assert synthesis ran over the
    PARENT BODY (not the matched child text)."""

    def __init__(self) -> None:
        self.context_texts: list[str] = []
        self.context_ids: list[str] = []

    @property
    def model_id(self) -> str:
        return "spy"

    def synthesize(
        self, question: str, context_chunks: list[VectorHit], graph_facts: list
    ) -> SynthesisResult:
        self.context_texts = [h.chunk.text for h in context_chunks]
        self.context_ids = [h.chunk.id for h in context_chunks]
        return SynthesisResult(answer="ANSWER", citations=[h.chunk.id for h in context_chunks])


def _two_parent_store() -> MemoryParentChildStore:
    store = MemoryParentChildStore()
    # Parent A: a child vector close to [1, 0]; restricted tier. Parent B: close to [0, 1]; public.
    store.index_parent(
        ParentDoc(
            parent_id="enhancements/a/README.md",
            source="enhancements",
            doc_path="a/README.md",
            heading="Summary",
            entity_ids=("kep-1",),
            visibility="restricted",
            body="A FULL BODY",
            children=(
                ChildVector("enhancements/a/README.md#0", "Summary", "child a0 text", [1.0, 0.0]),
            ),
        )
    )
    store.index_parent(
        ParentDoc(
            parent_id="community/b/README.md",
            source="community",
            doc_path="b/README.md",
            heading="Charter",
            entity_ids=("sig:b",),
            visibility="public",
            body="B FULL BODY",
            children=(
                ChildVector("community/b/README.md#0", "Charter", "child b0 text", [0.0, 1.0]),
            ),
        )
    )
    return store


def test_parentchild_query_synthesizes_over_parent_body_with_ordered_trace() -> None:
    store = _two_parent_store()
    spy = SpySynthesizer()
    result = parentchild_query(
        "what does doc A say?",
        store=store,
        embedder=FixedEmbedder([1.0, 0.0]),  # closest to parent A's child
        synthesizer=spy,
        k=1,
    )
    # top hit is parent A, matched on its child
    assert result.hits[0].parent.parent_id == "enhancements/a/README.md"
    assert result.hits[0].matched_child is not None
    assert result.hits[0].matched_child.child_id == "enhancements/a/README.md#0"
    # synthesis ran over the PARENT BODY, not the matched child text
    assert spy.context_texts == ["A FULL BODY"]
    assert spy.context_ids == ["enhancements/a/README.md"]
    # trace order: question -> matched child -> returned parents -> answer
    rendered = result.render()
    assert rendered.index("question:") < rendered.index("matched child")
    assert rendered.index("matched child") < rendered.index("returned parents")
    assert rendered.index("returned parents") < rendered.index("answer:")
    # the parent body is shown by length/count in the trace, not inlined verbatim
    assert "body 11 chars" in rendered
    assert isinstance(result, ParentChildResult)


def test_parentchild_query_clearance_excludes_above_clearance_parent() -> None:
    store = _two_parent_store()
    # A "public-only" clearance: parent A (restricted) must be absent even though it matches best.
    result = parentchild_query(
        "what does doc A say?",
        store=store,
        embedder=FixedEmbedder([1.0, 0.0]),
        synthesizer=SpySynthesizer(),
        k=5,
        clearance=Clearance(persona="reader", allowed=frozenset({"public"})),
    )
    ids = {h.parent.parent_id for h in result.hits}
    assert "enhancements/a/README.md" not in ids  # above clearance, excluded
    assert "community/b/README.md" in ids


def test_parentchild_query_empty_clearance_is_fail_closed() -> None:
    store = _two_parent_store()
    result = parentchild_query(
        "anything",
        store=store,
        embedder=FixedEmbedder([1.0, 0.0]),
        synthesizer=SpySynthesizer(),
        clearance=Clearance(persona="nobody", allowed=frozenset()),
    )
    assert result.hits == []  # empty allowed set ⇒ zero hits (fail-closed)
    assert "(no hits)" in result.render()  # the graceful no-context narration still renders


def test_parentchild_default_k_matches_vector_default() -> None:
    assert DEFAULT_K == 5
