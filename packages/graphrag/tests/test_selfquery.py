"""Self-query metadata filtering — schema, validation, extractors, orchestration.

The Bedrock extractor is verified against a **mock** (no live call); the offline rule
extractor is deterministic and non-semantic; validation is the single pure chokepoint
(no store, corpus-blind). The orchestrator threads the filter into vector + the vector
leg of hybrid, composing with the slice-4 clearance (fail-closed preserved).

# STUB: AC1
# STUB: AC2
# STUB: AC4
# STUB: AC5
"""

from __future__ import annotations

import sys
from typing import Any

from graphrag.chunk import Chunk
from graphrag.model import EntityKind, Node
from graphrag.selfquery import (
    DEFAULT_EXTRACT_MAX_TOKENS,
    FIELD_BY_NAME,
    FIELDS,
    BedrockMetadataExtractor,
    MetadataFilter,
    RuleMetadataExtractor,
    selfquery_query,
    validate_filter,
)
from graphrag.store import EmbeddedChunk, MemoryGraphStore, MemoryVectorStore
from graphrag.synthesize import DEFAULT_SYNTHESIS_MODEL_ID, TemplateSynthesizer
from graphrag.visibility import resolve_clearance


class _FixedEmbedder:
    """A preset vector for any text — so all chunks score equally (the filter is the variable)."""

    @property
    def model_id(self) -> str:
        return "fixed-test"

    @property
    def dimensions(self) -> int:
        return 2

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


class _FakeBedrock:
    """Records the converse() call and returns a canned text payload."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"output": {"message": {"content": [{"text": self.text}]}}, "stopReason": "end_turn"}


def _chunk(cid: str, source: str, entity_ids: list[str], visibility: str = "public") -> Chunk:
    return Chunk(cid, f"text {cid}", source, f"{source}/{cid}.md", "H", entity_ids, visibility)


def _store() -> MemoryVectorStore:
    """A small corpus tagged across both repos + visibility tiers."""
    store = MemoryVectorStore()
    chunks = [
        _chunk("c1", "enhancements", ["kep-1880", "sig:sig-node"]),
        _chunk("c2", "community", ["sig:sig-node"]),
        _chunk("c3", "enhancements", ["kep-2086"], visibility="restricted"),
        _chunk("c4", "community", ["sig:sig-network"]),
    ]
    for c in chunks:
        store.index_chunk(EmbeddedChunk(c, [1.0, 0.0]))
    return store


# ---- AC1: declared field schema + MetadataFilter model -----------------------------------


def test_fields_declare_exactly_source_and_entity_ids() -> None:
    assert [f.name for f in FIELDS] == ["source", "entity_ids"]
    assert FIELD_BY_NAME["source"].kind == "enum"
    assert FIELD_BY_NAME["source"].choices == ("community", "enhancements")
    assert FIELD_BY_NAME["entity_ids"].kind == "entity"
    assert "visibility" not in FIELD_BY_NAME  # visibility is the permission filter, not self-query


def test_metadata_filter_empty_and_clauses() -> None:
    assert MetadataFilter().is_empty
    f = MetadataFilter({"source": ("enhancements",), "entity_ids": ("sig:sig-node", "kep-1880")})
    assert not f.is_empty
    clauses = f.as_filter_clauses()
    assert {"terms": {"source": ["enhancements"]}} in clauses
    assert {"terms": {"entity_ids": ["kep-1880", "sig:sig-node"]}} in clauses  # sorted


def test_metadata_filter_matches_or_within_and_across() -> None:
    # OR within a field: a multi-value entity_ids matches a chunk carrying EITHER id.
    f = MetadataFilter({"entity_ids": ("sig:sig-node", "kep-9999")})
    assert f.matches(_chunk("x", "enhancements", ["kep-1880", "sig:sig-node"]))
    assert not f.matches(_chunk("y", "enhancements", ["kep-1880"]))
    # AND across fields: source AND entity_ids must both match.
    f2 = MetadataFilter({"source": ("enhancements",), "entity_ids": ("sig:sig-node",)})
    assert f2.matches(_chunk("a", "enhancements", ["sig:sig-node"]))
    assert not f2.matches(_chunk("b", "community", ["sig:sig-node"]))  # wrong source
    assert not f2.matches(_chunk("c", "enhancements", ["sig:sig-network"]))  # wrong entity


def test_selfquery_import_is_pyyaml_free() -> None:
    # Importing the self-query module must not pull yaml into the graph (Lambda bundle).
    mods = {name for name in sys.modules if name == "yaml" or name.startswith("yaml.")}
    # Re-import is a no-op if already imported elsewhere in the session; assert the module
    # itself declares no yaml dependency by checking its own import graph is clean of a fresh one.
    import importlib

    import graphrag.selfquery as sq

    importlib.reload(sq)
    assert not ({n for n in sys.modules if n == "yaml" or n.startswith("yaml.")} - mods), (
        "graphrag.selfquery must not import yaml"
    )


# ---- AC2: deterministic validation (pure, no store) --------------------------------------


def test_validate_filter_keeps_valid_and_drops_invalid() -> None:
    fe = validate_filter(
        {
            "source": ["enhancements", "bogus"],
            "entity_ids": ["SIG Node", "KEP-1880", "zzz nonsense"],
            "junk": ["x"],
        },
        aliases={},
    )
    assert fe.filter.terms["source"] == ("enhancements",)
    assert fe.filter.terms["entity_ids"] == ("sig:sig-node", "kep-1880")
    reasons = {(d.field, d.reason) for d in fe.dropped}
    assert ("source", "not in enum") in reasons
    assert ("entity_ids", "no declared-entity match") in reasons
    assert ("junk", "undeclared field") in reasons


def test_validate_filter_empty_is_no_filter() -> None:
    assert validate_filter({}, aliases={}).filter.is_empty
    assert validate_filter({"entity_ids": ["nothing matches"]}, aliases={}).filter.is_empty


def test_validate_filter_takes_no_store_argument() -> None:
    import inspect

    params = inspect.signature(validate_filter).parameters
    assert "store" not in params and "graph_store" not in params


# ---- AC4: extractors -----------------------------------------------------------------------


def test_bedrock_extractor_returns_validated_filter_and_is_well_formed() -> None:
    client = _FakeBedrock('{"source": ["enhancements"], "entity_ids": ["SIG Node"]}')
    extractor = BedrockMetadataExtractor(client=client)
    assert extractor.model_id == DEFAULT_SYNTHESIS_MODEL_ID

    fe = extractor.extract("in the enhancements repo, what does SIG Node own?", aliases={})
    assert fe.filter.terms == {"source": ("enhancements",), "entity_ids": ("sig:sig-node",)}

    call = client.calls[0]
    assert call["modelId"] == DEFAULT_SYNTHESIS_MODEL_ID
    system_text = " ".join(b["text"] for b in call["system"]).lower()
    assert "untrusted" in system_text and "instruction" in system_text
    user_text = " ".join(
        b["text"] for m in call["messages"] if m["role"] == "user" for b in m["content"]
    )
    assert "SIG Node" in user_text  # question rides messages as data...
    # ...never the system block: the distinctive question phrasing is absent from the
    # static system prompt (the prompt's own field example may mention "SIG Node").
    assert "what does sig node own" not in system_text
    assert 0 < call["inferenceConfig"]["maxTokens"] <= 512
    assert DEFAULT_EXTRACT_MAX_TOKENS <= 512


def test_bedrock_extractor_drops_undeclared_unresolvable_and_malformed() -> None:
    drop_field = BedrockMetadataExtractor(
        client=_FakeBedrock('{"drop_table": ["x"], "source": ["community"]}')
    ).extract("q", aliases={})
    assert drop_field.filter.terms == {"source": ("community",)}
    assert any(d.field == "drop_table" for d in drop_field.dropped)

    unresolvable = BedrockMetadataExtractor(
        client=_FakeBedrock('{"entity_ids": ["not an entity"]}')
    ).extract("q", aliases={})
    assert unresolvable.filter.is_empty

    malformed = BedrockMetadataExtractor(client=_FakeBedrock("not json at all")).extract(
        "q", aliases={}
    )
    assert malformed.filter.is_empty


def test_rule_extractor_is_deterministic_and_non_semantic() -> None:
    ext = RuleMetadataExtractor()
    assert "non-semantic" in ext.model_id.lower()
    fe = ext.extract("in the enhancements repo, which KEPs does SIG Node own?", aliases={})
    assert fe.filter.terms["source"] == ("enhancements",)
    assert "sig:sig-node" in fe.filter.terms["entity_ids"]
    # a question with no repo keyword and no known entity ⇒ empty filter.
    assert ext.extract("tell me something interesting", aliases={}).filter.is_empty


# ---- AC5: orchestration (vector + hybrid), trace, no-filter, clearance compose ------------


def test_selfquery_vector_mode_narrows_and_renders_in_order() -> None:
    store = _store()
    result = selfquery_query(
        "in the enhancements repo, which KEPs does SIG Node own?",
        extractor=RuleMetadataExtractor(),
        vector_store=store,
        embedder=_FixedEmbedder(),
        synthesizer=TemplateSynthesizer(),
        aliases={},
        mode="vector",
    )
    # filter = source=enhancements AND entity_ids=sig:sig-node ⇒ only c1.
    assert [h.chunk.id for h in result.hits] == ["c1"]
    rendered = result.render()
    assert rendered.index("question:") < rendered.index("extracted filter:")
    assert rendered.index("extracted filter:") < rendered.index("filtered hits:")
    assert rendered.index("filtered hits:") < rendered.index("answer:")
    assert "source (enum) = enhancements" in rendered


def test_selfquery_no_filter_question_is_unfiltered() -> None:
    store = _store()
    result = selfquery_query(
        "tell me something interesting",
        extractor=RuleMetadataExtractor(),
        vector_store=store,
        embedder=_FixedEmbedder(),
        synthesizer=TemplateSynthesizer(),
        aliases={},
        mode="vector",
    )
    assert result.extraction.filter.is_empty
    assert len(result.hits) == 4  # all chunks, unfiltered
    assert "no filter extracted" in result.render()


def test_selfquery_hybrid_mode_threads_filter_into_vector_leg() -> None:
    store = _store()
    graph = MemoryGraphStore()
    graph.upsert_node(Node("sig:sig-node", EntityKind.SIG))
    result = selfquery_query(
        "in the enhancements repo, what does SIG Node own?",
        extractor=RuleMetadataExtractor(),
        vector_store=store,
        embedder=_FixedEmbedder(),
        synthesizer=TemplateSynthesizer(),
        aliases={},
        mode="hybrid",
        graph_store=graph,
    )
    # the vector leg of hybrid only saw the filter-matching chunk.
    assert [h.chunk.id for h in result.hits] == ["c1"]
    assert result.hybrid_result is not None


def test_selfquery_filter_composes_with_clearance_fail_closed() -> None:
    store = _store()
    # public-reader sees only `public`; c3 is restricted. A filter for kep-2086 (only on the
    # restricted c3) plus a public-reader clearance ⇒ zero hits (clearance wins; fail-closed).
    public = resolve_clearance("public-reader")
    restricted_filter = MetadataFilter({"entity_ids": ("kep-2086",)})
    # direct on the store seam: clearance AND filter compose.
    hits = store.knn(
        [1.0, 0.0], 5, allowed_labels=public.allowed, metadata_filter=restricted_filter
    )
    assert hits == []
    # an EMPTY clearance filters everything regardless of metadata_filter (fail-closed).
    empty = frozenset()
    assert store.knn([1.0, 0.0], 5, allowed_labels=empty, metadata_filter=MetadataFilter()) == []
    # None clearance + empty filter ⇒ unrestricted, unfiltered.
    assert len(store.knn([1.0, 0.0], 5, allowed_labels=None, metadata_filter=MetadataFilter())) == 4
