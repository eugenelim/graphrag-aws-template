# ADR-0012: OWL schema-only + named-graph partition: typed RDF corpus with asymmetric retrieval semantics

- **Status:** Accepted
- **Date:** 2026-07-23
- **Decision-makers:** eugenelim
- **Supersedes:** none
- **Related:** [RFC-0004 §D3, §D4, §Honesty constraint](../rfc/0004-biz-ops-kg-pivot.md); [ADR-0011](0011-neptune-sparql-rdf-engine-and-text2sparql-guard.md) (SPARQL/RDF engine — required for named-graph primitive); [ADR-0013](0013-multi-strategy-server-side-routing.md) (routing uses the partition); `spec-rdf-owl-ontology`; `spec-normative-partition`; `spec-shacl-validation`; `spec-ingestion-extraction-cleanse`

## Decision summary

- **Decision:** We will type all corpus knowledge with an OWL ontology used as a vocabulary/schema only (no runtime reasoner), validated by SHACL before Neptune LOAD, anchored to Schema.org + SKOS; and partition the graph into named graphs with asymmetric retrieval semantics — exhaustive recall for normative, best-match for descriptive, quarantine for validation failures.
- **Because:** Schema-only OWL + SHACL is sufficient to type, validate, and interoperate with the W3C stack without reasoner operational overhead; named-graph partitioning with asymmetric semantics is the structural guarantee against silent compliance gaps.
- **Applies to:** The ontology design (classes and properties), SHACL shape library, ingestion RDF emission, and retrieval partition model.
- **Tradeoff accepted:** No materialised inference; ingest-time classification accuracy is the load-bearing correctness gate — a mis-typed document lands in the wrong partition and no retrieval path recovers it silently.
- **Revisit if:** An adopter requires materialised inference (cross-class transitivity, property chains) as a retrieval feature; re-open as a follow-on ADR.

## Context

RFC-0004 introduces two structural decisions this ADR records together because they are inseparable: **D4** (how knowledge is typed and validated) and **D3** (how the named-graph partition model is structured with asymmetric failure semantics). They are coupled: the partition's correctness depends on the ontology classification being correct, and the ontology's validation contract (SHACL) is what makes mis-classification inspectable rather than silent.

**The knowledge-typing problem (D4).** The platform holds two structurally different knowledge types: normative (exhaustive recall — a missed policy is a compliance gap) and descriptive (best-match precision — a miss is "I don't know"). Every document entering the platform must be assigned an `rdf:type` that determines which partition it lands in. That assignment needs a machine-readable contract (what constitutes a valid RDF emission) and a stable, domain-agnostic vocabulary.

**The partition problem (D3).** Retrieval correctness — guaranteeing exhaustive recall for normative knowledge — requires a hard isolation boundary expressible only in named graphs (available because ADR-0011 chose SPARQL/RDF). But the partition is only as good as the classification that fills it: the **honesty constraint** (documented below) bounds what the partition actually guarantees.

**Why schema-only (D4).** OWL reasoning materialises inferred triples — if `biz:Policy rdfs:subClassOf schema:DigitalDocument`, a reasoner infers every policy is a `DigitalDocument` without explicit assertion. For this platform, no retrieval query depends on materialised inferences; `rdf:type` is asserted explicitly during ingestion. A reasoner adds operational latency at ingest time, a runtime dependency, and a potential source of unexpected triple materialisation, for zero retrieval benefit. The W3C OWL Reference acknowledges non-reasoning tools as valid; SHACL (closed-world, explicit shapes) is the actual contract enforcer and does not require OWL reasoning to run.

## Decision

> We will use an OWL ontology **as a vocabulary/schema only** — no runtime reasoner at ingest or query time. All knowledge types are asserted explicitly during ingestion, not inferred. The data contract (what a valid RDF triple emission must contain per class) is enforced by SHACL shapes run in-process with `pyshacl` (`inference="none"`) before every Neptune LOAD. Base classes anchor to Schema.org (`CreativeWork`, `DigitalDocument`) and SKOS (W3C standard for concept hierarchies). The corpus is partitioned into five named graphs with asymmetric retrieval semantics.

**Ontology (OWL schema-only):**

```turtle
schema:DigitalDocument
    biz:Policy          ← compliance rules, regulations, guidelines
        biz:Standard
        biz:Guideline

schema:CreativeWork
    biz:SOP             ← Standard Operating Procedure
    biz:JobAid
    biz:Transcript
    biz:Chunk           ← retrieval unit (child of a document)

skos:ConceptScheme
    biz:BusinessDomain  ← e.g. "Finance", "HR", "Ops"

skos:Concept
    biz:Journey         ← e.g. "Onboarding", "Incident Response"
```

Key properties: `biz:inDomain`, `biz:inJourney`, `biz:hasChunk`, `biz:scope`, `biz:effectiveDate`, `biz:visibility`, `biz:hasPII`, `biz:gitCommitSHA`. Domain/journey taxonomy instances are `skos:Concept` instances added at runtime without schema change.

**SHACL shapes (data contract):**

One shape per document class, colocated with the OWL ontology in `packages/graphrag/ontology/`. Representative shapes:

```turtle
biz:PolicyShape a sh:NodeShape ; sh:targetClass biz:Policy ;
    sh:property [ sh:path schema:name ;       sh:minCount 1 ; sh:datatype xsd:string ] ;
    sh:property [ sh:path biz:effectiveDate ; sh:minCount 1 ; sh:maxCount 1 ; sh:datatype xsd:date ] ;
    sh:property [ sh:path biz:scope ;         sh:minCount 1 ] ;
    sh:property [ sh:path biz:hasPII ;        sh:minCount 1 ; sh:maxCount 1 ; sh:datatype xsd:boolean ] ;
    sh:property [ sh:path biz:gitCommitSHA ;  sh:minCount 1 ; sh:datatype xsd:string ] .

biz:ChunkShape a sh:NodeShape ; sh:targetClass biz:Chunk ;
    sh:property [ sh:path prov:wasDerivedFrom ; sh:minCount 1 ; sh:maxCount 1 ] ;
    sh:property [ sh:path biz:chunkIndex ;      sh:minCount 1 ; sh:datatype xsd:integer ] ;
    sh:property [ sh:path biz:embeddingModel ;  sh:minCount 1 ; sh:datatype xsd:string ] .
```

On SHACL violation: the document is routed to `urn:graph:quarantine` with a structured violation report (`biz:quarantineReason`); the Gold S3 artifact is not written; Neptune and OpenSearch are not updated.

**Named-graph partition model:**

| Named graph | Contents | Retrieval semantics | On unavailability |
|---|---|---|---|
| `urn:graph:normative` | Policies, standards, guidelines + their chunks | Exhaustive SPARQL `SELECT` (all matching) UNION vector-threshold leg | Hard fail — a partial result is worse than none |
| `urn:graph:descriptive` | SOPs, job aids, transcripts + their chunks | Top-k vector k-NN + SPARQL graph expand | Graceful degrade — miss = "I don't know" |
| `urn:graph:taxonomy` | SKOS domain/journey hierarchy + doc→partition index | SPARQL lookup only | Degrade: partition lookup returns miss |
| `urn:graph:ontology` | OWL schema (the ontology file) | Read-only at query time | No live effect — shape library is file-based |
| `urn:graph:quarantine` | Documents failing Silver quality or SHACL gates | Review workflow only | Never silently dropped |

Document triples live **inside partition graphs** (e.g. `urn:graph:normative`), not in per-document graphs. The document URI (`urn:doc:{repo}:{path}`) is an RDF subject within the partition graph. This is the mechanism that makes `FROM NAMED urn:graph:normative` actually retrieve anything.

**Honesty constraint (written into the RFC at the Approver's direction).** The partition guarantees exactly two things — no more: (1) **partition isolation** — a normative SPARQL query never returns descriptive results and vice versa; (2) **no top-k drop within the partition scan** — `SELECT` over the normative partition returns every triple matching its filter, not a ranked top-k. Two residuals remain:

- **Ingest-time classification accuracy.** A policy mis-typed as `biz:SOP` lands in the descriptive partition; no normative query recovers it. SHACL validates the emission contract (required fields), not the semantic correctness of the `rdf:type` assignment. Classification quality is a named acceptance criterion of the ingestion spec.
- **Intra-partition attribute mismatch.** `get_policies` narrows by domain + effective-date filter. A correctly-partitioned policy tagged with the wrong domain falls through the SPARQL leg to the vector-threshold leg — which retains ANN recall limits. This reproduces, at the attribute-tag layer, the same silent-gap failure mode the RFC condemns in pure vector search.

The platform **reduces** silent-gap risk and moves the residual from an untyped similarity threshold to an inspectable, structured classification + filter that a trace can expose.

## Decision drivers

- **No reasoner operational overhead.** Materialised inference is not needed — `rdf:type` is asserted at ingest. A runtime reasoner adds latency and a failure mode with zero retrieval benefit.
- **SKOS extensibility without schema change.** Domain/journey taxonomy is expressed as `skos:Concept` instances; an adopter adds a new domain at runtime without modifying the ontology file.
- **Schema.org + SKOS stability.** Anchoring to well-known, W3C-stable base classes makes the ontology legible and interoperable without domain-specific context.
- **SHACL as the data contract.** Closed-world, explicit shapes are machine-verifiable and CI-runnable (`pyshacl` with `inference="none"`, no AWS needed); validates what retrieval cares about — required fields and types — not inferred superclass membership.
- **Asymmetric failure semantics reduce compliance risk.** Guaranteeing structurally that normative queries are exhaustive (hard-fail if unavailable) and descriptive queries degrade gracefully separates the two failure modes the RFC names as fundamentally different.

## Consequences

**Positive:**
- OWL + SHACL + PROV-O + SKOS are the W3C standard stack; adopters can extend with standard tooling.
- SHACL validation gate runs in CI against `rdflib` without AWS credentials — malformed triples are caught before Neptune.
- Quarantine graph records every validation failure with a structured violation report — nothing is silently dropped.
- SKOS taxonomy instances are addable at runtime without schema migration.
- The honesty constraint is documented explicitly — adopters cannot over-claim the correctness guarantee.

**Negative:**
- No materialised inference — adopters who need OWL-inferred entailments must add a reasoner pass as a separate step.
- Ingest-time classification accuracy is the load-bearing gate; a mis-typed document is not recoverable without re-ingestion.
- The intra-partition attribute-mismatch residual (wrong domain tag) is a genuine compliance gap that the partition design does not eliminate.
- SHACL shapes must be kept in sync with the OWL ontology; drift between the two is a latent correctness issue.

**Revisit if:** An adopter requires OWL materialised inference (cross-class transitivity, property chains) as a retrieval feature — re-open as a follow-on ADR to add a reasoner pass at ingest.

## Confirmation

- **Mode:** lint/CI + periodic audit
- **Signal (SHACL offline gate):** `pyshacl` with `inference="none"` runs against the fixture corpus in the offline CI suite; a fixture with a missing required property (e.g. `biz:effectiveDate` absent on a `biz:Policy`) is confirmed to route to quarantine. No AWS credentials needed.
- **Signal (shape colocated with ontology):** OWL ontology and SHACL shapes colocated at `packages/graphrag/ontology/`; a PR that adds a new class without an accompanying shape fails the linter.
- **Signal (partition isolation gate):** see ADR-0011 Confirmation — normative-scoped SPARQL query returns zero descriptive results in `rdflib`; part of the offline CI suite.
- **Owner:** eugenelim (ontology changes); `spec-shacl-validation` gate owner (ingestion spec)

## Alternatives considered

- **OWL + runtime reasoner.** Full OWL inference at ingest — materialises subclass entailments. *Rejected against the no-reasoner-overhead driver:* `rdf:type` is asserted explicitly; inference adds latency, a runtime dependency, and unexpected triple materialisation with zero retrieval benefit. The [SHACL spec](https://www.w3.org/TR/shacl/) explicitly supports `inference="none"`.
- **No formal ontology (ad-hoc types).** Arbitrary `rdf:type` values, no schema contract. *Rejected against the data-contract driver:* no SHACL validation; no machine-verifiable contract; no interoperability; mis-typed documents are undetectable until retrieval fails.
- **Query-shape routing only (no hard partition).** One graph; route normative vs descriptive at query time based on query shape. *Rejected against the retrieval-correctness driver:* a misroute leaks silently — the routing logic is a hint, not a structural guarantee. A bug or injection in the router breaks isolation without a detectable trace.
- **Per-document named graphs.** One named graph per document URI. *Rejected:* `FROM NAMED urn:graph:normative` cannot retrieve anything if document triples live in per-document graphs — the partition mechanism only works if document triples are loaded *into* the partition graph. Document URIs are RDF *subjects* within partition graphs, not graph names.

## References

- [RFC-0004 §D3, §D4, §Honesty constraint](../rfc/0004-biz-ops-kg-pivot.md)
- [W3C SHACL — supports `inference="none"`](https://www.w3.org/TR/shacl/)
- [W3C SKOS Reference](https://www.w3.org/TR/skos-reference/)
- [W3C PROV-O](https://www.w3.org/TR/prov-o/)
- [Schema.org DigitalDocument](https://schema.org/DigitalDocument)
- ADR-0011 (SPARQL/RDF engine — required for named-graph primitive); ADR-0013 (routing uses the partition)
