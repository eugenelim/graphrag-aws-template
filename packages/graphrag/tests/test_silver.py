"""medallion-staging T2 — the content+config-addressed Silver cache.

Offline (`HashEmbedder` + a spy extractor): a cache hit makes zero embed/extract calls; a miss
computes once and writes both artifacts; an embedder-fp bump invalidates only chunks and a
schema-fp bump only candidates; the serialized JSON round-trip is exact; and the Silver key is
confined to server-derived hex components (CWE-23).
"""

from __future__ import annotations

import pytest

from graphrag.embed import HashEmbedder, embedder_fingerprint
from graphrag.extract_llm import (
    EXTRACTION_SCHEMA,
    CandidateTriple,
    ExtractionSchema,
    SchemaEdge,
    schema_fingerprint,
)
from graphrag.model import EntityKind
from graphrag.parse import ParsedMarkdown
from graphrag.silver import (
    MemoryArtifactStore,
    candidates_from_json,
    candidates_to_json,
    chunks_from_json,
    chunks_to_json,
    materialize_silver,
    silver_key,
)
from graphrag.sources import COMMUNITY, ParsedDoc

EMB_FP = "aaaa1111"
EXT_FP = "bbbb2222"


def _doc(slug: str, body: str) -> ParsedDoc:
    return ParsedDoc(
        COMMUNITY,
        f"{slug}/README.md",
        "sig_readme",
        payload={"slug": slug},
        markdown=ParsedMarkdown(front_matter={}, headings=[], body=body),
    )


class _SpyEmbedder:
    """HashEmbedder wrapped to count embed() invocations (the zero-on-hit probe)."""

    def __init__(self) -> None:
        self._inner = HashEmbedder()
        self.embed_calls = 0

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    def fingerprint(self) -> str:
        return self._inner.fingerprint()

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls += 1
        return self._inner.embed(texts)


class _SpyExtractor:
    """Returns one candidate per doc and counts extract() invocations."""

    def __init__(self) -> None:
        self.extract_calls = 0

    @property
    def model_id(self) -> str:
        return "spy (test)"

    def extract(self, doc: ParsedDoc, schema: ExtractionSchema) -> list[CandidateTriple]:
        self.extract_calls += 1
        return [
            CandidateTriple("sig:sig-network", "COLLABORATES_WITH", "sig:sig-node", doc.doc_id, "s")
        ]


# --- AC1: hit/miss ---------------------------------------------------------------------


def test_miss_computes_once_and_writes_both_artifacts() -> None:
    artifacts = MemoryArtifactStore()
    emb, ext = _SpyEmbedder(), _SpyExtractor()
    art = materialize_silver(
        _doc("sig-network", "SIG Network does routing."),
        artifacts,
        emb,
        content_hash="cafe00",
        embedder_fp=EMB_FP,
        extraction_fp=EXT_FP,
        extractor=ext,
    )
    assert emb.embed_calls == 1 and ext.extract_calls == 1
    assert art.chunks and art.candidates
    assert artifacts.get(silver_key(EMB_FP, "cafe00", "chunks")) is not None
    assert artifacts.get(silver_key(EXT_FP, "cafe00", "candidates")) is not None


def test_hit_makes_zero_embed_and_zero_extract_calls() -> None:
    artifacts = MemoryArtifactStore()
    doc = _doc("sig-network", "SIG Network does routing.")
    materialize_silver(
        doc, artifacts, _SpyEmbedder(), content_hash="cafe00",
        embedder_fp=EMB_FP, extraction_fp=EXT_FP, extractor=_SpyExtractor(),
    )
    # Second materialize at the same hash+fps is a pure hit.
    emb, ext = _SpyEmbedder(), _SpyExtractor()
    art = materialize_silver(
        doc, artifacts, emb, content_hash="cafe00",
        embedder_fp=EMB_FP, extraction_fp=EXT_FP, extractor=ext,
    )
    assert emb.embed_calls == 0 and ext.extract_calls == 0
    assert art.chunks and art.candidates  # served from cache


# --- AC2 + AC3: fingerprint granularity, moved doc, content-only -----------------------


def test_embedder_fp_bump_invalidates_only_chunks() -> None:
    artifacts = MemoryArtifactStore()
    docs = [
        (_doc("sig-network", "Network prose."), "aa11"),
        (_doc("sig-node", "Node prose."), "bb22"),
    ]
    for d, h in docs:  # warm both at EMB_FP/EXT_FP
        materialize_silver(
            d, artifacts, _SpyEmbedder(), content_hash=h,
            embedder_fp=EMB_FP, extraction_fp=EXT_FP, extractor=_SpyExtractor(),
        )
    emb, ext = _SpyEmbedder(), _SpyExtractor()
    for d, h in docs:  # bump ONLY the embedder fp
        materialize_silver(
            d, artifacts, emb, content_hash=h,
            embedder_fp="ffff9999", extraction_fp=EXT_FP, extractor=ext,
        )
    assert emb.embed_calls == len(docs)  # every doc's chunks recomputed
    assert ext.extract_calls == 0  # candidates stayed hits (extraction fp unchanged)


def test_schema_fp_bump_invalidates_only_candidates() -> None:
    artifacts = MemoryArtifactStore()
    doc = _doc("sig-network", "Network prose.")
    materialize_silver(
        doc, artifacts, _SpyEmbedder(), content_hash="f1",
        embedder_fp=EMB_FP, extraction_fp=EXT_FP, extractor=_SpyExtractor(),
    )
    emb, ext = _SpyEmbedder(), _SpyExtractor()
    materialize_silver(
        doc, artifacts, emb, content_hash="f1",
        embedder_fp=EMB_FP, extraction_fp="dddd8888", extractor=ext,
    )
    assert emb.embed_calls == 0  # chunks stayed a hit (embedder fp unchanged)
    assert ext.extract_calls == 1  # candidates recomputed


def test_moved_doc_same_hash_and_fp_is_a_hit() -> None:
    artifacts = MemoryArtifactStore()
    materialize_silver(
        _doc("sig-network", "prose"), artifacts, _SpyEmbedder(), content_hash="abcabc",
        embedder_fp=EMB_FP, extraction_fp=EXT_FP, extractor=_SpyExtractor(),
    )
    # Same content_hash (a move keeps the hash), new path/doc — still a hit because the key is
    # content+config-addressed, not path-addressed.
    emb, ext = _SpyEmbedder(), _SpyExtractor()
    materialize_silver(
        _doc("sig-network-renamed", "prose"), artifacts, emb, content_hash="abcabc",
        embedder_fp=EMB_FP, extraction_fp=EXT_FP, extractor=ext,
    )
    assert emb.embed_calls == 0 and ext.extract_calls == 0


def test_content_only_change_recomputes_only_the_changed_doc() -> None:
    artifacts = MemoryArtifactStore()
    a, b = _doc("sig-network", "Network prose."), _doc("sig-node", "Node prose.")
    for d, h in ((a, "aa"), (b, "bb")):
        materialize_silver(
            d, artifacts, _SpyEmbedder(), content_hash=h,
            embedder_fp=EMB_FP, extraction_fp=EXT_FP, extractor=_SpyExtractor(),
        )
    emb, ext = _SpyEmbedder(), _SpyExtractor()
    # 'a' changed (new content_hash); 'b' unchanged (same hash).
    materialize_silver(a, artifacts, emb, content_hash="aa99",
                       embedder_fp=EMB_FP, extraction_fp=EXT_FP, extractor=ext)
    materialize_silver(b, artifacts, emb, content_hash="bb",
                       embedder_fp=EMB_FP, extraction_fp=EXT_FP, extractor=ext)
    assert emb.embed_calls == 1 and ext.extract_calls == 1  # only the changed doc recomputed


def test_delta_run_without_extractor_caches_chunks_only() -> None:
    artifacts = MemoryArtifactStore()
    art = materialize_silver(
        _doc("sig-network", "prose"), artifacts, _SpyEmbedder(), content_hash="f1",
        embedder_fp=EMB_FP,  # no extractor / extraction_fp → schema-guided is full/rebuild-only
    )
    assert art.candidates == []
    assert artifacts.get(silver_key(EMB_FP, "f1", "chunks")) is not None
    assert artifacts.get(silver_key(EXT_FP, "f1", "candidates")) is None


# --- serialized round-trip -------------------------------------------------------------


def test_chunks_serialized_round_trip_is_exact() -> None:
    artifacts = MemoryArtifactStore()
    art = materialize_silver(
        _doc("sig-network", "Network prose with floats."), artifacts, HashEmbedder(),
        content_hash="f1", embedder_fp=EMB_FP,
    )
    # Round-trip through the SERIALIZED text (not the in-memory dict): provenance + float vectors.
    assert chunks_from_json(chunks_to_json(art.chunks)) == art.chunks


def test_candidates_serialized_round_trip_is_exact() -> None:
    cands = [CandidateTriple("a", "COLLABORATES_WITH", "b", "src/doc", "the span")]
    assert candidates_from_json(candidates_to_json(cands)) == cands


# --- AC6: key confinement (CWE-23) -----------------------------------------------------


def test_silver_key_refuses_non_hex_components() -> None:
    with pytest.raises(ValueError, match="non-hex"):
        silver_key("aaaa", "community/sig-x/../../etc/passwd", "chunks")
    with pytest.raises(ValueError, match="non-hex"):
        silver_key("not/a/fingerprint", "cafe", "chunks")


def test_silver_key_refuses_unknown_artifact() -> None:
    with pytest.raises(ValueError, match="unknown silver artifact"):
        silver_key("aaaa", "cafe", "../escape")


def test_silver_key_is_prefix_confined_for_hex_components() -> None:
    key = silver_key("aaaa1111", "cafe00", "chunks")
    assert key == "silver/aaaa1111/cafe00/chunks.json"
    assert key.startswith("silver/") and ".." not in key


def test_doc_id_with_traversal_cannot_alter_the_key() -> None:
    # A poisoned doc_id never reaches the key — the key is built from the server-computed hash only.
    artifacts = MemoryArtifactStore()
    evil = ParsedDoc(
        COMMUNITY, "../../etc/passwd", "sig_readme", payload={"slug": "sig-network"},
        markdown=ParsedMarkdown(front_matter={}, headings=[], body="prose"),
    )
    materialize_silver(evil, artifacts, HashEmbedder(), content_hash="5afe", embedder_fp=EMB_FP)
    # The only key written is the confined, hash-addressed one.
    assert artifacts.get(silver_key(EMB_FP, "5afe", "chunks")) is not None
    assert all(k.startswith("silver/") and ".." not in k for k in artifacts._blobs)


# --- fingerprint stability -------------------------------------------------------------


def test_embedder_fingerprint_is_stable_and_field_sensitive() -> None:
    assert embedder_fingerprint("m", 256) == embedder_fingerprint("m", 256)  # stable
    assert embedder_fingerprint("m", 256) != embedder_fingerprint("m", 512)  # dims matter
    assert embedder_fingerprint("m", 256) != embedder_fingerprint("n", 256)  # model matters
    assert HashEmbedder(256).fingerprint() != HashEmbedder(512).fingerprint()


def test_schema_fingerprint_ignores_description_but_tracks_structure() -> None:
    base = schema_fingerprint(EXTRACTION_SCHEMA)
    assert base == schema_fingerprint(EXTRACTION_SCHEMA)  # stable across calls
    # A description-only edit does NOT bump the fingerprint (the cache stays valid).
    desc_edit = ExtractionSchema(
        edges=tuple(
            SchemaEdge(e.kind, e.src_kind, e.dst_kind, e.description + " (reworded)")
            for e in EXTRACTION_SCHEMA.edges
        )
    )
    assert schema_fingerprint(desc_edit) == base
    # Removing a kind DOES bump it.
    fewer = ExtractionSchema(edges=EXTRACTION_SCHEMA.edges[:-1])
    assert schema_fingerprint(fewer) != base
    # Changing an endpoint pair DOES bump it.
    first = EXTRACTION_SCHEMA.edges[0]
    swapped = ExtractionSchema(
        edges=(SchemaEdge(first.kind, EntityKind.KEP, EntityKind.KEP, first.description),)
        + EXTRACTION_SCHEMA.edges[1:]
    )
    assert schema_fingerprint(swapped) != base
