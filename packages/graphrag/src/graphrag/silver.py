"""Silver — the content+config-addressed cache of the Bedrock-expensive per-document outputs.

Silver caches the two per-document outputs that cost a Bedrock call: a document's **chunks +
embedding vectors** (keyed by the *embedder* fingerprint) and its **ungrounded schema-guided LLM
candidate triples** (keyed by the *extraction* fingerprint). The two are addressed independently —
`silver/{fingerprint}/{content_hash}/{chunks,candidates}.json` — so an embedder change recomputes
only the chunks artifact and a schema change only the candidates artifact (medallion-staging AC2).
A re-ingest of an unchanged document at unchanged fingerprints is a pure cache **hit** that makes
zero Bedrock calls (AC1).

What is **not** cached: the deterministic `extract()`/`resolve()` output — it makes no Bedrock call
and Gold re-derives it, so a cached copy would be dead or divergent data (ADR-0007). Grounding is
**not** done here either — it is global and belongs in Gold (`schema_extract.ground_candidates`).

The Silver S3 key is built **only** from the server-computed `content_hash` (sha256 hex) and the
server-derived fingerprint (hex) — never from a doc id, a path, a span, or model output — so a
poisoned document cannot write outside the `silver/` prefix (CWE-23, the trace-key confinement
pattern). Like `delta.py`, this module is **ingest-path only** — it must never be imported by the
PyYAML-free query Lambda.

**Integrity trust.** The cache is *integrity-trusted*: only the ingest task role writes the
`silver/*` prefix (a private, encrypted, SSL-enforced bucket), and a cached `Chunk.visibility` is
the project's synthetic teaching label, **not** a real ACL (charter principle 5) — so the cache is
not integrity-protected (no checksum/signature). A future change that promotes `visibility` to a
real authorization control must add integrity protection here, since a tampered cached artifact
would otherwise be served verbatim.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from .chunk import Chunk, chunk_corpus
from .embed import Embedder
from .extract_llm import EXTRACTION_SCHEMA, CandidateTriple, ExtractionSchema, TripleExtractor
from .sources import ParsedDoc

SILVER_PREFIX = "silver/"

# The two independently-keyed Silver artifact names (chunks by embedder fp, candidates by
# extraction fp). A fixed literal set — never doc/model-derived — part of the key confinement.
_ARTIFACT_NAMES = ("chunks", "candidates")

# A Silver key component (content hash / fingerprint) must be lowercase hex — a sha256 digest or a
# fingerprint digest. Anything else (a doc id, a path with `/` or `..`, model text) is refused.
_HEX_RE = re.compile(r"\A[0-9a-f]+\Z")


def silver_key(fingerprint: str, content_hash: str, artifact: str) -> str:
    """Build the Silver object key from **server-derived components only** (CWE-23 confinement).

    ``fingerprint`` and ``content_hash`` must be lowercase hex; ``artifact`` is one of the fixed
    literals. A doc id / path / span / model-supplied string never reaches the key — mirroring the
    schema-extraction trace-key confinement in the ingest entrypoint."""
    if artifact not in _ARTIFACT_NAMES:
        raise ValueError(f"unknown silver artifact {artifact!r}: expected one of {_ARTIFACT_NAMES}")
    for part in (fingerprint, content_hash):
        if not _HEX_RE.match(part):
            raise ValueError(f"refusing non-hex silver key component: {part!r}")
    return f"{SILVER_PREFIX}{fingerprint}/{content_hash}/{artifact}.json"


class ArtifactStore(Protocol):
    """A keyed blob store for Silver artifacts — the cache twin of the `S3Client` seam.

    Keys are the server-derived `silver_key` strings; bodies are the serialized JSON text. The
    in-memory implementation (`MemoryArtifactStore`) is the offline/test backend; the deployed task
    backs this with S3 over the existing boto3 client (T4b). Holding the **serialized text** (not
    live objects) means the in-memory backend exercises the same JSON codec the S3 backend does.

    `get` returns the body or ``None`` when absent — a **single** round-trip that collapses the
    has-then-load probe, so a warm-cache hit (the path the whole feature optimizes) is one fetch."""

    def get(self, key: str) -> str | None: ...
    def write(self, key: str, body: str) -> None: ...


class MemoryArtifactStore:
    """An in-memory `ArtifactStore` — key -> serialized-JSON text (the offline/test backend)."""

    def __init__(self) -> None:
        self._blobs: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._blobs.get(key)

    def write(self, key: str, body: str) -> None:
        self._blobs[key] = body


@dataclass
class SilverArtifact:
    """One document's materialized Silver outputs — its chunks+vectors and ungrounded candidates.

    ``candidates`` holds **only** ungrounded schema-guided LLM candidate triples (empty when no
    extractor ran); the deterministic nodes/edges are not cached (Gold re-derives them)."""

    doc_id: str
    chunks: list[tuple[Chunk, list[float]]]
    candidates: list[CandidateTriple]


# --- serialization (the JSON codec both the in-memory and S3 backends share) ---------------


def chunks_to_json(chunks: list[tuple[Chunk, list[float]]]) -> str:
    """Serialize chunks+vectors to JSON text (the cached chunks artifact body)."""
    return json.dumps(
        [
            {
                "chunk": {
                    "id": c.id,
                    "text": c.text,
                    "source": c.source,
                    "doc_path": c.doc_path,
                    "heading": c.heading,
                    "entity_ids": list(c.entity_ids),
                    "visibility": c.visibility,
                },
                "vector": [float(x) for x in vec],
            }
            for c, vec in chunks
        ]
    )


def chunks_from_json(text: str) -> list[tuple[Chunk, list[float]]]:
    """Parse a chunks artifact back to ``(Chunk, vector)`` pairs (inverse of `chunks_to_json`)."""
    out: list[tuple[Chunk, list[float]]] = []
    for item in json.loads(text):
        cd = item["chunk"]
        chunk = Chunk(
            id=cd["id"],
            text=cd["text"],
            source=cd["source"],
            doc_path=cd["doc_path"],
            heading=cd["heading"],
            entity_ids=list(cd["entity_ids"]),
            visibility=cd["visibility"],
        )
        out.append((chunk, [float(x) for x in item["vector"]]))
    return out


def candidates_to_json(candidates: list[CandidateTriple]) -> str:
    """Serialize ungrounded candidate triples to JSON text (the cached candidates artifact body)."""
    return json.dumps(
        [
            {
                "subject": c.subject,
                "predicate": c.predicate,
                "object": c.object,
                "source_doc": c.source_doc,
                "span": c.span,
            }
            for c in candidates
        ]
    )


def candidates_from_json(text: str) -> list[CandidateTriple]:
    """Parse a candidates artifact back to `CandidateTriple`s (inverse of `candidates_to_json`)."""
    return [
        CandidateTriple(
            subject=d["subject"],
            predicate=d["predicate"],
            object=d["object"],
            source_doc=d["source_doc"],
            span=d["span"],
        )
        for d in json.loads(text)
    ]


def materialize_silver(
    doc: ParsedDoc,
    artifacts: ArtifactStore,
    embedder: Embedder,
    *,
    content_hash: str,
    embedder_fp: str,
    extraction_fp: str | None = None,
    extractor: TripleExtractor | None = None,
    schema: ExtractionSchema = EXTRACTION_SCHEMA,
) -> SilverArtifact:
    """Cache-or-compute one document's Silver artifacts; a hit makes **zero** Bedrock calls.

    Chunks+vectors are addressed by ``embedder_fp``; candidate triples by ``extraction_fp``. On a
    miss the artifact is computed once and written; on a hit it is loaded from the cache. Candidate
    triples are materialized **only** when an ``extractor`` (and an ``extraction_fp``) is supplied —
    schema-guided extraction is full/rebuild-only and default-off (ADR-0006); a delta run passes no
    extractor and so caches/serves chunks only. Grounding is **not** done here (it is global —
    Gold's `ground_candidates` consumes these cached candidates)."""
    chunks_key = silver_key(embedder_fp, content_hash, "chunks")
    cached_chunks = artifacts.get(chunks_key)
    if cached_chunks is not None:
        chunks = chunks_from_json(cached_chunks)
    else:
        raw_chunks = chunk_corpus([doc])
        vectors = embedder.embed([c.text for c in raw_chunks]) if raw_chunks else []
        chunks = list(zip(raw_chunks, vectors, strict=True))
        artifacts.write(chunks_key, chunks_to_json(chunks))

    candidates: list[CandidateTriple] = []
    if extractor is not None and extraction_fp is not None:
        cand_key = silver_key(extraction_fp, content_hash, "candidates")
        cached_candidates = artifacts.get(cand_key)
        if cached_candidates is not None:
            candidates = candidates_from_json(cached_candidates)
        else:
            candidates = list(extractor.extract(doc, schema))
            artifacts.write(cand_key, candidates_to_json(candidates))

    return SilverArtifact(doc_id=doc.doc_id, chunks=chunks, candidates=candidates)
