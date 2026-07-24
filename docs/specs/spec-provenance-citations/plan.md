# Plan: spec-provenance-citations

- **Spec:** [`spec.md`](spec.md)
- **Status:** Drafting <!-- Drafting | Executing | Done -->

> **Plan contract:** this is the implementation strategy. Unlike the spec, this
> document is allowed to change as you learn. When it changes substantially
> (a different approach, not just a re-ordering), note why in the changelog
> at the bottom.

## Approach

Three tasks. T1 (ProvenanceEmitter) is pure rdflib — no external dependencies — and is the foundation the rest builds on. T2 (CitationResolver) depends on T1's provenance URI scheme because it issues SPARQL queries against the emitted provenance triples. T3 (merge + full integration test) confirms the emitted provenance graph merges cleanly with the document's RDF triple graph, and that the CitationResolver resolves citations correctly against a complete fixture corpus.

The riskiest part is graceful partial resolution: the CitationResolver must not raise when provenance triples are absent (e.g. a freshly-added document); it returns a `Citation` with `None` fields. This behaviour is tested before T3's integration layer exists.

No AWS credentials are needed for any task.

## Constraints

- ADR-0016: provenance entities are keyed by `(doc_uri, sha)` — the same scheme as the Gold artifact S3 key. URNs must be stable across ingestion re-runs for the same `(doc_uri, sha)` pair.
- ADR-0012: `biz:gitCommitSHA`, `biz:gitPath`, `biz:gitRepo` on Bronze entity are required SHACL properties.
- spec-rdf-owl-ontology: `biz:Chunk` has `prov:wasDerivedFrom` as a required SHACL property — every chunk must link to its parent document.
- W3C PROV-O vocabulary only — no proprietary provenance extensions.
- `ProvenanceEmitter` must be importable without boto3 or botocore.
- Ruff + mypy CI gates must stay green.

## Construction tests

**T1 (ProvenanceEmitter):**
- `emit_provenance(doc_uri, sha, git_path, git_repo, extractor, started_at, ended_at)` returns an `rdflib.Graph`.
- SPARQL SELECT on the returned graph: Bronze entity URI `urn:entity:bronze:{encoded_git_repo}:{encoded_git_path}:{sha}` has `biz:gitCommitSHA`, `biz:gitPath`, `biz:gitRepo`.
- SPARQL SELECT confirms all 5 subject types: Bronze entity, extraction activity, Silver entity, Gold emission activity, document entity — each with correct `rdf:type`.
- `emit_chunk_provenance(chunk_uri, doc_uri, chunk_index=1)` returns an `rdflib.Graph` with `prov:wasDerivedFrom <doc_uri>` and `biz:chunkIndex 1`.
- Fixture with 3 chunks (indices 0, 1, 2): all three have correct `prov:wasDerivedFrom` and `biz:chunkIndex`.
- IRI safety: `emit_provenance(doc_uri, sha, git_path="docs/dir name/file #1.md", git_repo, ...)` returns a graph where the Bronze URN is a valid `rdflib.URIRef` (no parse error); the space and `#` are percent-encoded; the emitted Turtle round-trips through `rdflib.Graph.parse()` without error.

**T2 (CitationResolver):**
- `CitationResolver.resolve([doc_uri], neptune_client)` against a fixture rdflib store returns a list of one `Citation` with all fields populated: `uri`, `title`, `doc_type`, `partition`, `commit_sha`, `git_path`, `git_repo`, `extractor`, `excerpt`, `relevance`, `effective_date`.
- A URI with no provenance triples in the store returns `Citation(commit_sha=None, extractor=None, git_path=None)` without raising.
- `Citation.excerpt` = first 200 chars of `biz:chunkText` for a chunk URI.
- A body shorter than 200 chars returns the full body as `excerpt`.
- A document URI (not a chunk) returns `excerpt=None`.

**T3 (merge + integration):**
- `emit_provenance()` graph and the fixture document's own triple graph (`biz:Policy` with attributes) merged into a single `rdflib.Graph` — no blank-node collision; SPARQL SELECT returns both `?doc a biz:Policy` and `?doc prov:wasGeneratedBy ?act` in one query.
- `CitationResolver.resolve()` against the merged graph returns citations for both document and chunk URIs with the correct `partition` field.

## Design (LLD)

### Design decisions

- **URN encoding for doc_uri.** Bronze entity URNs are `urn:entity:bronze:{git_repo}:{git_path}:{sha}`. Git paths and repo names can contain characters illegal in IRI references (spaces, `#`, `<`, `>`, non-ASCII, non-BMP codepoints). The URN NSS (namespace-specific string) permits colons and slashes but not unescaped spaces or `#`. The emitter must percent-encode path/repo segment characters that are not URI-safe using `urllib.parse.quote(segment, safe="/:@.-_~")` before constructing the URN. This ensures the emitted Turtle parses as valid IRI syntax in rdflib and Neptune's SPARQL loader. A construction test with a path containing a space and a `#` character must confirm the emitted URN is a valid `rdflib.URIRef` (no parse error) and that round-tripping `doc_uri` through `urllib.parse.unquote` recovers the original path.
- **`ProvenanceEmitter` takes a single `rdflib.Graph` as output.** The emitter returns a new graph object; the caller merges it with the document triple graph using `rdflib.Graph.__iadd__()`. This separation allows the ingestion pipeline to emit provenance and document triples independently and then concatenate them into the Gold Turtle artifact.
- **`CitationResolver` accepts an injectable store.** `CitationResolver(store)` where `store` is the `NeptuneSparqlStore` (live) or an `rdflib.ConjunctiveGraph` (offline). The store is injected at construction — no boto3 in the constructor signature.
- **Graceful partial resolution: return `Citation` with `None` fields, never raise.** The SPARQL query uses `OPTIONAL {}` for every provenance property. A URI with no provenance triples returns a `Citation` where all optional fields are `None`. The `uri` field is always populated from the input list.
- **`biz:chunkText` SPARQL SELECT on the partition graph.** The `excerpt` field is resolved by a SPARQL `SELECT ?text WHERE { GRAPH ?g { <chunk_uri> biz:chunkText ?text } }` query. The partition graph (`?g`) is not hardcoded — the query uses an open variable to handle documents in either `normative` or `descriptive` partitions.
- **`effective_date` source predicate: `biz:effectiveDate` on the document URI.** `Citation.effective_date` is resolved by `SELECT ?d WHERE { GRAPH ?g { <doc_uri> biz:effectiveDate ?d } }`. For chunk URIs, the resolver follows `prov:wasDerivedFrom` to the parent document URI and queries `biz:effectiveDate` there. If absent (OPTIONAL), `effective_date` is `None`.

### Data & schema

```python
# graphrag/provenance/_types.py

from dataclasses import dataclass

@dataclass
class Citation:
    uri: str
    title: str | None
    doc_type: str | None
    partition: str | None            # urn:graph:normative or urn:graph:descriptive
    commit_sha: str | None
    git_path: str | None
    git_repo: str | None
    extractor: str | None
    excerpt: str | None              # first 200 chars of biz:chunkText; None for doc URIs
    relevance: float | None          # caller-provided; not from SPARQL
    effective_date: str | None       # ISO date string or None
```

**PROV-O URN patterns** (all path/repo/doc_uri segments are percent-encoded via `urllib.parse.quote(seg, safe="/:@.-_~")`):

| Entity | URN pattern |
|--------|------------|
| Bronze entity | `urn:entity:bronze:{encoded_git_repo}:{encoded_git_path}:{sha}` |
| Extraction activity | `urn:activity:extract:{encoded_doc_uri}:{sha}` |
| Silver entity | `urn:entity:silver:{encoded_doc_uri}:{sha}` |
| Gold emission activity | `urn:activity:emit:{encoded_doc_uri}:{sha}` |
| Extractor agent | `urn:agent:{extractor_name}` |
| RDF emitter agent | `urn:agent:rdf-emitter` |

`doc_uri` values (e.g. `urn:doc:my-repo:policies/x.md`) must also be encoded when embedded in a URN path segment: `urn:entity:silver:{quote(doc_uri, safe="/:@.-_~")}:{sha}`. The same round-trip construction test added for the Bronze URN applies to all doc_uri-bearing URNs.

### Component / module decomposition

```
packages/graphrag/src/graphrag/provenance/
├── __init__.py          # exports: ProvenanceEmitter, CitationResolver, Citation
├── _types.py            # Citation dataclass
├── _emitter.py          # ProvenanceEmitter — pure rdflib, no AWS
├── _resolver.py         # CitationResolver — SPARQL queries against injectable store
└── _sparql.py           # SPARQL query strings for provenance resolution

packages/graphrag/tests/provenance/
├── test_emitter.py
├── test_resolver.py
└── test_merge.py
```

### Failure cases & resilience

- **Missing `biz:chunkText`.** Some chunks may not have chunk text stored (e.g. binary chunks). SPARQL query uses `OPTIONAL { <chunk_uri> biz:chunkText ?text }` — `excerpt` is `None` for chunks without text; no exception.
- **`prov:startedAtTime` / `prov:endedAtTime` absent.** Optional provenance fields. CitationResolver does not require them for the `Citation` dataclass; they are stored in the graph for audit purposes but not surfaced in the `Citation` response.
- **Multiple `biz:gitCommitSHA` values on the same Bronze entity.** An incorrectly emitted graph could have two SHA values. SPARQL `SELECT` returns multiple rows; `CitationResolver` takes the first. A warning is logged at WARNING level.
- **Very large `biz:chunkText` value.** The excerpt is capped at 200 chars with `[:200]` slicing in Python after SPARQL retrieval; the full text is not buffered into the response.
- **`doc_uri` contains characters requiring IRI encoding.** `doc_uri` values (e.g. `urn:doc:my-repo:policies/x.md`) may contain spaces, `#`, or non-ASCII characters. When embedded in a URN path segment, `doc_uri` is percent-encoded via `urllib.parse.quote(doc_uri, safe="/:@.-_~")`. The URN NSS allows colons and slashes (in `safe`), so a `doc_uri` like `urn:doc:my-repo:policies/x.md` passes through unchanged; a path containing a space encodes to `%20`. All URNs using `doc_uri` in their pattern — `urn:entity:silver:{encoded_doc_uri}:{sha}`, `urn:activity:extract:{encoded_doc_uri}:{sha}`, `urn:activity:emit:{encoded_doc_uri}:{sha}` — use the encoded form.

### Quality attributes (NFRs)

- **Offline CI.** `ProvenanceEmitter` and `CitationResolver` are fully testable without AWS credentials; rdflib is the only dependency.
- **Mypy-clean.** Full type annotations on `Citation`, `ProvenanceEmitter`, `CitationResolver`, and all SPARQL helper functions.
- **No boto3 in the emitter.** `ProvenanceEmitter` imports only `rdflib`, `datetime`, and `graphrag.provenance._types`.

## Tasks

### T1: ProvenanceEmitter — PROV-O graph emission

**Depends on:** none

**Touches:**
- `packages/graphrag/src/graphrag/provenance/__init__.py`
- `packages/graphrag/src/graphrag/provenance/_types.py`
- `packages/graphrag/src/graphrag/provenance/_emitter.py`
- `packages/graphrag/tests/provenance/test_emitter.py`

**Tests (TDD):** Bronze entity URI + SHACL-required fields; full 5-entity PROV-O chain (SPARQL SELECT confirms all types); chunk provenance with `prov:wasDerivedFrom` and `biz:chunkIndex`; 3-chunk fixture (all 3 correct).

**Done when:** emitter tests pass; `python -c "import graphrag.provenance"` exits 0 without boto3; `ruff check` and `mypy` clean.

---

### T2: CitationResolver — SPARQL provenance resolution

**Depends on:** T1

**Touches:**
- `packages/graphrag/src/graphrag/provenance/_sparql.py`
- `packages/graphrag/src/graphrag/provenance/_resolver.py`
- `packages/graphrag/tests/provenance/test_resolver.py`

**Tests (TDD):** full `Citation` with all fields populated (fixture rdflib store); graceful partial resolution (missing provenance → `Citation` with `None` fields, no exception); `excerpt` = first 200 chars; body < 200 chars → full body; document URI → `excerpt=None`.

**Done when:** resolver tests pass; `ruff check` and `mypy` clean.

---

### T3: Merge test + integration

**Depends on:** T1, T2

**Touches:**
- `packages/graphrag/tests/provenance/test_merge.py`

**Tests (TDD):** provenance graph + document triple graph merge; no blank-node collision; SPARQL SELECT returns `?doc a biz:Policy` and `?doc prov:wasGeneratedBy ?act` from merged graph; resolver returns citations for both document and chunk URIs with correct `partition` field.

**Done when:** merge tests pass; full test suite green; `ruff check` and `mypy` clean.

## Rollout

- **Delivery:** no flag — `graphrag.provenance` is a new module; no callers exist until `spec-ingestion-extraction-cleanse` imports `ProvenanceEmitter` and `spec-mcp-tool-server` imports `CitationResolver`.
- **Infrastructure:** uses the `rdflib` library already in `pyproject.toml [ingest]`; PROV-O namespace is built into rdflib ≥ 6.0 (`rdflib.namespace.PROV`). No new infrastructure.
- **Deployment sequencing:** no AWS deployment required for the module itself; called by the ingestion pipeline task (needs `ingestion_task_role`) and the MCP Lambda (needs `mcp_lambda_role` for the SPARQL read in `CitationResolver`).

## Risks

- **PROV-O URN segment collision.** If `git_path` contains `:{sha}` literally (unlikely but possible), the Bronze entity URN parsing could ambiguate. Mitigated: the SHA is always a 40-char hex string — unambiguous in the rightmost position. Document in the module's `_emitter.py` as an invariant comment.
- **rdflib `FROM NAMED` vs. open-variable SPARQL in `CitationResolver`.** The resolver uses `GRAPH ?g { ... }` to handle both `normative` and `descriptive` partition documents. This is correct rdflib behaviour for `ConjunctiveGraph`, but if an adopter uses `Dataset` instead, the query semantics differ. The offline fixture uses `ConjunctiveGraph` — document in the `Assumptions` section.
- **`biz:chunkText` not in SHACL shapes.** The `Citation.excerpt` field depends on `biz:chunkText` being emitted by `ChunkEmbedder` (`spec-ingestion-extraction-cleanse`). If that spec ships without `biz:chunkText`, `excerpt` is always `None`. Track as a cross-spec dependency.

## Changelog

- 2026-07-23: initial plan
