# ADR-0018: Vector store service shape — managed OpenSearch domain over AOSS VECTORSEARCH

- **Status:** Accepted
- **Date:** 2026-08-05
- **Decision-makers:** eugenelim
- **Supersedes:** none
- **Related:** ADR-0002 (ephemeral teardown-first topology), ADR-0011 (Neptune SPARQL engine), context-ontology gap inventory P1 #5 (2026-08-05), [aws/context-ontology-accelerator](https://github.com/aws/context-ontology-accelerator)

## Decision summary

- **Decision:** We will retain the single-node managed OpenSearch domain as the template's vector store and document AOSS VECTORSEARCH as the recommended enterprise adoption shape, not migrate the template to it.
- **Because:** The AOSS compute floor (≥2 OCU always-on, ~$350/mo dev, ~$700/mo with standby replicas) is 13–27× the domain's standing cost and would dominate the template's entire monthly budget.
- **Applies to:** The vector store service only. Client code, index mapping (`named_graph`, `doc_uri`, `rdf_type`), and IAM posture; Neptune and S3 are untouched.
- **Tradeoff accepted:** The template's vector store is not the reference accelerator's shape — enterprises adopting the pattern at scale must swap the service, and the swap is a resource replacement, not a config change.
- **Revisit if:** AOSS pricing gains a scale-to-zero or sub-OCU tier; OR the corpus outgrows a single `t3.small.search` node (~10 GB / degraded p99); OR the template drops its standing-cost ceiling posture (ADR-0002).

## Context

The gap analysis against the AWS `context-ontology-accelerator` (2026-08-05)
surfaced a structural divergence in the vector store: the reference uses
**Amazon OpenSearch Serverless (AOSS)**, collection type `VECTORSEARCH`,
generation `NEXTGEN`, min 2 OCU / max 96 OCU, standby replicas enabled. Our
Terraform provisions a **managed OpenSearch domain**: single `t3.small.search`
node, 10 GB gp3, no zone awareness, VPC-resident, ~$26/mo.

Constraints in force today:

- **Standing-cost ceiling.** The template's whole-stack standing floor is
  ~$226/mo (Neptune min-NCU + OpenSearch + interface endpoints) against a
  $250/mo Budgets alarm. AOSS OCUs bill continuously: 1 OCU ≈ $0.24/hr.
  The minimum viable AOSS deployment is 2 OCU (dev, no standby) ≈ $350/mo;
  the reference's production shape (standby replicas, required for NEXTGEN)
  is 4 OCU ≈ $700/mo. Either multiplies the template's total cost by 2.5–4×
  for the vector leg alone.
- **Teaching/template posture (ADR-0002).** The repo is a deployable pattern
  catalog: teardown-first, offline-first, cost-legible. Single-node OpenSearch
  is an accepted, documented risk (no HA; rebuild from Gold artifacts).
- **Migration is a replacement, not a tweak.** `aws_opensearch_domain` →
  `aws_opensearchserverless_collection` changes the Terraform resource, the
  IAM model (`es:ESHttp*` + domain resource policy → `aoss:APIAccessAll` +
  data-access policies), the SigV4 service name (`es` → `aoss`), and the VPC
  endpoint type (domain ENI → `aoss` interface endpoints).
- **Feature deltas that touch our design.** AOSS VECTORSEARCH collections have
  historically not supported client-supplied `_id` on index operations — our
  upsert-by-chunk-URI (`_id = urn:chunk:...`) and delete-by-`doc_uri` flows
  would need re-verification and likely a `delete_by_query` rewrite. AOSS also
  drops index-lifecycle features the domain offers (UltraWarm, ISM policies).

## Decision

We will retain the managed OpenSearch domain as the template's vector store,
and record AOSS VECTORSEARCH as the recommended shape for enterprise adopters
running this pattern at production scale.

Concretely:

1. `apps/infra-tf/opensearch.tf` keeps `aws_opensearch_domain` (single-node
   `t3.small.search`) as the deployed shape.
2. The design doc carries the divergence explicitly: the reference
   accelerator's AOSS shape is the *adoption* target, not the *template*
   target.
3. The abstraction seam is preserved so the swap stays mechanical: all vector
   access goes through the store layer (`store/` vector interface) and uses
   SigV4 signing with a parameterised service name; no code path may assume
   domain-only features (ISM, UltraWarm, custom `_id` semantics beyond
   upsert/delete flows that are already isolated in the store adapter).

### Enterprise adoption recommendation

What is realistic for an enterprise running this pattern in production:

| Corpus / load | Recommended shape | Why |
|---|---|---|
| Pilot ≤ ~50k chunks, single team, tolerant of rebuild-from-Gold | Managed domain, 3× `r7g.large.search` data nodes across 3 AZ, gp3, fine-grained access control (~$400–450/mo) | Cost-predictable, full feature set (ISM, hybrid BM25 later), same client code as the template |
| Production, spiky ingestion, ops-lean team | **AOSS VECTORSEARCH, min 2 / max 8+ OCU, standby replicas** (~$700/mo floor) — the reference accelerator's shape | No shard/version/node management, scales indexing OCUs through embedding bursts, sub-100 ms p99 on NEXTGEN; matches the AWS reference so accelerator docs apply directly |
| Enterprise with existing OpenSearch platform team and clusters | Tenant index on the existing managed cluster | Marginal cost near zero; platform team owns HA/upgrades; only the index mapping and IAM grants from this template are needed |

The default enterprise recommendation is the middle row — **AOSS
VECTORSEARCH** — because the teams adopting a context-ontology accelerator are
typically not OpenSearch operators, vector ingestion load is bursty
(re-embedding on corpus changes), and alignment with the AWS reference keeps
the accelerator's documentation and support path applicable. The domain rows
above and below it are the deliberate exceptions: cost-pinned pilots and
organisations where OpenSearch is already a platform service.

## Decision drivers

- **D1 — Standing cost:** the template must stay legible against a ~$250/mo
  alarm; the vector leg cannot dominate the floor.
- **D2 — Teaching fidelity:** the deployed template should demonstrate the
  retrieval pattern (typed chunks, named-graph filter, hybrid GraphRAG), which
  is service-shape-independent.
- **D3 — Enterprise realism:** adopters need a documented, defensible
  production shape — "what the reference does" beats "what the demo does".
- **D4 — Swap cost containment:** whatever we deploy must keep the store-layer
  seam clean enough that the AOSS swap is an infra + adapter change, not a
  redesign.

## Consequences

**Positive:**

- Standing cost stays at ~$26/mo for the vector leg (D1); the Budgets alarm
  remains meaningful.
- The template continues to deploy/teardown cleanly for live acceptance runs.
- Adopters get an explicit, reference-aligned production recommendation
  instead of inheriting the demo shape by default (D3).

**Negative:**

- Template and reference diverge on a load-bearing service; adopters who skip
  this ADR may productionise the single-node domain as-is.
- The AOSS swap is unexercised in this repo — the custom-`_id` / upsert
  semantics difference is a known-unknown until someone runs the migration.
- Single-node domain remains a data-availability risk (accepted since
  ADR-0002; recovery is re-ingest from Gold artifacts).

**Revisit if:** AOSS pricing gains a scale-to-zero or sub-OCU tier; OR the
corpus outgrows a single `t3.small.search` node (~10 GB / degraded p99); OR
the template drops its standing-cost ceiling posture (ADR-0002).

## Confirmation

- **Mode:** reviewer-checked
- **Signal:** `apps/infra-tf/opensearch.tf` contains `aws_opensearch_domain`
  and no `aws_opensearchserverless_*` resources; the store layer keeps vector
  access behind the adapter interface with no domain-only feature leakage
  (checked at review of any vector-store or ingestion change; `reconcile-iac`
  runs confirm no drift).
- **Owner:** eugenelim

## Alternatives considered

- **Migrate the template to AOSS VECTORSEARCH now (reference parity).**
  Rejected on D1: a 2-OCU dev collection (~$350/mo) more than doubles the
  standing floor, and the reference's standby-replica shape (~$700/mo) nearly
  triples it — for a template whose corpus fits in one `t3.small` node.
  Reference parity is achieved by documentation (this ADR) instead.
- **Harden the domain in-template (3 data nodes, multi-AZ, zone awareness).**
  Rejected on D1/D2: triples domain cost (~$80+/mo at t3, ~$400/mo at r7g)
  to buy HA the teaching template explicitly does not promise (ADR-0002); the
  retrieval pattern being taught is unchanged by node count.
- **pgvector on Aurora Serverless v2.** Rejected on D2/D3: departs from both
  our deployed shape and the AWS reference; loses the OpenSearch k-NN +
  metadata-filter combination the design's `named_graph` mandatory filter is
  built on; retrieval code rewrite with no offsetting benefit at this corpus
  scale.
- **Neptune Analytics as a combined graph+vector store.** Rejected: distinct
  service from our Neptune DB cluster with its own standing cost and no
  scale-to-near-zero; would collapse the two-store teaching architecture
  (vector vs graph legs) the routing matrix depends on; SPARQL named-graph
  partitioning (ADR-0011/0012) has no direct equivalent there.

## References

- Context-ontology gap inventory (2026-08-05), gap #5 — this branch's
  originating analysis.
- [aws/context-ontology-accelerator `storage-stack.ts`](https://github.com/aws/context-ontology-accelerator) —
  AOSS `VECTORSEARCH` collection, `NEXTGEN` generation, min 2 / max 96 OCU,
  standby replicas enabled.
- ADR-0002 — ephemeral, teardown-first topology (cost/teaching posture this
  decision inherits).
- AWS pricing: OCU ≈ $0.24/hr (indexing + search billed separately);
  `t3.small.search` ≈ $0.036/hr.
