# Spec: spec-provenance-citations

- **Status:** Approved <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [ADR-0012](../../adr/0012-owl-schema-only-and-named-graph-partition.md) (PROV-O is part of the W3C standard stack unlocked by SPARQL/RDF; `biz:gitCommitSHA` required by SHACL shapes); [ADR-0016](../../adr/0016-git-ingestion-commit-sha-delta-medallion.md) (Bronze/Silver/Gold artifact keying scheme; the commit SHA is the provenance anchor); [`spec-rdf-owl-ontology`](../rdf-owl-ontology/spec.md) (`biz:Chunk` has `prov:wasDerivedFrom` as a required SHACL property — this spec expands that to the full PROV-O model)
- **Brief:** none
- **Discovery:** none
- **Contract:** none
- **Shape:** data

> **Spec contract:** this document defines what "done" means. The implementing
> PR must match this spec, or update it. Verification must be derivable from it.

## Objective

The `graphrag.provenance` module defines and emits the PROV-O provenance graph for every document processed through the Bronze → Silver → Gold pipeline, and provides the `Citation` dataclass and `CitationResolver` used by MCP tools to surface source attribution alongside retrieval results.

It delivers two things:

1. **PROV-O RDF emission** (`ProvenanceEmitter`) — given a processed document's metadata (doc URI, commit SHA, git path, extractor used, activity timestamps), emits a standard W3C PROV-O subgraph alongside the document's own triples. This subgraph records: the Bronze entity (the git file at a commit), the extraction activity (Bronze → Silver), the Silver entity (extracted Markdown), the Gold emission activity (Silver → RDF triples), and the document entity (Gold) — plus chunk-level `prov:wasDerivedFrom` links. The emitted provenance graph is part of the Gold Turtle artifact (`.ttl`) and is loaded into the same named partition graph as the document triples.

2. **Citation resolution** (`CitationResolver`) — given a list of retrieval result URIs from Neptune (documents or chunks), issues SPARQL queries against the provenance triples to resolve `Citation` dataclasses. Each `Citation` carries the fields the MCP `ask`, `search`, and `get_policies` tools return alongside retrieved content: source URI, document title, type, partition, commit SHA, git path, git repository, extractor used, a text excerpt (first 200 chars of the matching chunk body), and relevance score.

This module owns provenance emission (data structure) and citation resolution (SPARQL read). Triple loading into Neptune and S3 artifact writes are the ingestion pipeline's scope.

## Boundaries

### Always do

- Use W3C PROV-O vocabulary (`prov:Entity`, `prov:Activity`, `prov:SoftwareAgent`, `prov:wasGeneratedBy`, `prov:wasDerivedFrom`, `prov:used`, `prov:wasAssociatedWith`, `prov:startedAtTime`, `prov:endedAtTime`) for all provenance assertions.
- Emit `prov:wasDerivedFrom` on every `biz:Chunk` instance pointing to its parent document URI (required by the SHACL shape from `spec-rdf-owl-ontology`).
- Key provenance entities by the combination of doc URI + commit SHA (`urn:entity:silver:{doc_uri}:{sha}`, `urn:activity:extract:{doc_uri}:{sha}`, etc.) so provenance triples are immutable per commit, consistent with the Gold artifact S3 keying scheme (ADR-0016).
- Include `biz:gitCommitSHA`, `biz:gitPath`, and `biz:gitRepo` on the Bronze entity as required compact provenance properties (also required by SHACL shapes on document classes per ADR-0012).
- Return a `Citation` dataclass from `CitationResolver.resolve()` — never return raw SPARQL result rows to callers.

### Ask first

- Changing the PROV-O URI scheme for activity or entity identifiers (`urn:entity:...`, `urn:activity:...`) — downstream SPARQL queries depend on these patterns.
- Adding PROV-O properties not in the set defined in this spec — each addition must be validated against the existing SHACL shapes (a new required property on a document class requires a SHACL shape update).
- Extending `Citation` with a new field — all MCP tool response formatters must be updated in sync.

### Never do

- Run OWL reasoning or SPARQL Update from this module — it owns schema definitions and SPARQL reads only; all writes use the ingestion pipeline's `ingestion_task_role`.
- Import boto3 or botocore in `ProvenanceEmitter` — the emitter must be usable in offline CI with no AWS credentials.
- Return raw SPARQL result rows, raw Neptune error text, or internal entity URIs as part of the `Citation` response — the citation surface is the typed `Citation` dataclass only.
- Emit provenance triples for a document that failed the SHACL gate — `ProvenanceEmitter` is called only after a successful gate pass; the caller (the ingestion pipeline) is responsible for this ordering.

## Testing Strategy

- **TDD** — `ProvenanceEmitter` Bronze entity (AC1): `emit_provenance(doc_uri, sha, git_path, git_repo, extractor, timestamps)` returns an `rdflib.Graph`; SPARQL SELECT confirms the Bronze entity URI follows the `urn:entity:bronze:{repo}:{path}:{sha}` pattern; `biz:gitCommitSHA`, `biz:gitPath`, `biz:gitRepo` are present.
- **TDD** — full PROV-O chain (AC2): SPARQL on the emitted graph confirms all 5 subject types present: Bronze entity, extraction activity, Silver entity, Gold emission activity, document entity; each with the expected `rdf:type` and provenance properties.
- **TDD** — chunk provenance (AC3): `emit_chunk_provenance(chunk_uri, doc_uri, chunk_index)` adds a triple with `prov:wasDerivedFrom <doc_uri>` and `biz:chunkIndex <i>`; SPARQL SELECT on the graph confirms both. Fixture with 3 chunks: all 3 have correct parent references.
- **TDD** — `CitationResolver.resolve(result_uris, neptune_client)` (AC4–AC5): fixture `rdflib` ConjunctiveGraph with provenance triples; assert each resolved `Citation` carries `uri`, `title`, `doc_type`, `partition`, `commit_sha`, `git_path`, `git_repo`, `extractor`; assert a URI with no provenance triples produces `Citation.commit_sha=None` (graceful partial resolution, not an exception).
- **TDD** — excerpt extraction (AC5): fixture chunk body stored as `biz:chunkText`; `resolve()` returns `Citation.excerpt` = first 200 chars of the body; a body shorter than 200 chars returns the full body.
- **Goal-based check** — import isolation (AC6): `python -c "import graphrag.provenance"` exits 0 without boto3 or botocore installed.

## Acceptance Criteria

- [ ] `ProvenanceEmitter.emit_provenance(doc_uri, sha, git_path, git_repo, extractor, started_at, ended_at)` returns an `rdflib.Graph` containing:
  - `<urn:entity:bronze:{git_repo}:{git_path}:{sha}> a prov:Entity` with `biz:gitCommitSHA`, `biz:gitPath`, `biz:gitRepo` asserted.
  - `<urn:activity:extract:{doc_uri}:{sha}> a prov:Activity` with `prov:used <bronze_uri>`, `prov:wasAssociatedWith <urn:agent:{extractor}>`, `prov:startedAtTime`, `prov:endedAtTime`.
  - `<urn:entity:silver:{doc_uri}:{sha}> a prov:Entity` with `prov:wasGeneratedBy <extract_activity>` and `prov:wasDerivedFrom <bronze_uri>`.
  - `<urn:activity:emit:{doc_uri}:{sha}> a prov:Activity` with `prov:used <silver_uri>`, `prov:wasAssociatedWith <urn:agent:rdf-emitter>`.
  - `<{doc_uri}> prov:wasGeneratedBy <emit_activity>` and `prov:wasDerivedFrom <silver_uri>`.
  Confirmed by SPARQL SELECT on the returned graph.
- [ ] `ProvenanceEmitter.emit_chunk_provenance(chunk_uri, doc_uri, chunk_index)` returns an `rdflib.Graph` with `<chunk_uri> prov:wasDerivedFrom <doc_uri>` and `<chunk_uri> biz:chunkIndex <chunk_index>`. For a fixture document with 3 chunks (indices 0, 1, 2), all three have the correct `prov:wasDerivedFrom` and `biz:chunkIndex` assertions.
- [ ] The provenance graph returned by `emit_provenance()` merges cleanly with the document's own RDF triple graph (no blank-node collision, no namespace conflicts) when parsed together as a single `rdflib.Graph`. Confirmed by parsing the combined Turtle string and running a SPARQL SELECT that returns both a document-class triple (`?doc a biz:Policy`) and a provenance triple (`?doc prov:wasGeneratedBy ?act`).
- [ ] `CitationResolver.resolve(result_uris, neptune_client)` returns a list of `Citation` dataclasses with fields: `uri` (str), `title` (str), `doc_type` (str), `partition` (str: `urn:graph:normative` or `urn:graph:descriptive`), `commit_sha` (str | None), `git_path` (str | None), `git_repo` (str | None), `extractor` (str | None), `excerpt` (str | None), `relevance` (float | None), `effective_date` (str | None). All fields are populated from fixture provenance triples in the `rdflib` store.
- [ ] A result URI whose provenance triples are absent from Neptune (e.g. a freshly added document whose provenance emission failed) produces a `Citation` with `commit_sha=None` and `extractor=None` — graceful partial resolution; no exception raised.
- [ ] `Citation.excerpt` is the first 200 characters of the `biz:chunkText` property on the matching chunk URI, resolved by SPARQL. For a chunk whose body is < 200 chars, `excerpt` is the full body. For a document URI (not a chunk URI), `excerpt` is `None`.
- [ ] `python -c "import graphrag.provenance"` exits 0 in an environment where boto3 and botocore are not installed. `ProvenanceEmitter` uses only `rdflib` and `datetime`; it is importable and usable without any AWS SDK.
- [ ] `ruff check` and `mypy` pass on `packages/graphrag/src/graphrag/provenance/` with zero errors.

## Assumptions

- Technical: `graphrag.provenance` lives in `packages/graphrag/src/graphrag/provenance/`; tests in `packages/graphrag/tests/provenance/`.
- Technical: The offline Neptune substitute is `rdflib` `ConjunctiveGraph` with named-graph support — the same substitute used throughout the platform. `CitationResolver` accepts an injectable store client for testability.
- Technical: `biz:chunkText` is the RDF property used to store the raw chunk body as a plain string literal. This is emitted by `ChunkEmbedder` in `spec-ingestion-extraction-cleanse` alongside the embedding vectors. The `CitationResolver` resolves it with a SPARQL SELECT on the partition named graph.
- Technical: `prov:startedAtTime` and `prov:endedAtTime` are `xsd:dateTime` literals in UTC ISO-8601 format. The `ProvenanceEmitter` receives Python `datetime` objects and formats them.
- Technical: `urn:agent:{extractor}` patterns are: `urn:agent:pandoc`, `urn:agent:docling`, `urn:agent:markitdown`, `urn:agent:textract`, `urn:agent:passthrough`. These are stable identifiers; an unknown extractor name is passed through (no validation in the emitter — the caller's `FormatRouter` is the source of truth).
- Technical: Rdflib and PROV-O namespace are already available (`rdflib.namespace.PROV` is built into rdflib ≥ 6.0).
- Product: Provenance triples are emitted as part of the Gold artifact Turtle file (`.ttl`) and loaded into the same partition named graph as the document's own triples. They are not in a separate provenance named graph. A SPARQL query scoped to `FROM NAMED <urn:graph:normative>` will also see the provenance triples for normative documents — this is intentional: the citation resolution queries run against the partition graph.
- Product: The `Citation` dataclass is the MCP tool response's citation field. MCP tools return `list[Citation]` alongside the primary result. The format is not the MCP protocol citation format — it is the application-level citation format serialized to JSON by the MCP tool handler.
