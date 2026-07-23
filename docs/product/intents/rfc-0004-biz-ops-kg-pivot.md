# Intent: Pivot to a generic business-operations knowledge platform with typed, partition-aware retrieval

- **Slug:** `rfc-0004-biz-ops-kg-pivot`
- **Level:** `product-strategy`
- **Scale:** `app`
- **Maturity:** `brownfield`
- **Parent intent:** `graphrag-aws-demo`

## Outcome

- **Input (steerable):** Two knobs. (1) Generality of the ingestible corpus — how many organizations' business operations document types (policies, standards, SOPs, job aids, transcripts) the platform handles without corpus-specific customization. (2) Correctness of normative retrieval — whether a policy query returns _all_ applicable documents, not just the highest-scoring ones.
- **Outcome (lagging):** An adopter can deploy the platform on their own business operations corpus and trust that policy and standards queries are exhaustive — no silent gaps — while SOP and job-aid queries are returned by best-fit precision. An architect watching the demo can reproduce this on Markdown corpora of their own, name the retrieval strategy used for each document type, and explain why vector similarity alone is unsafe for compliance queries.
- **Guardrail:** The platform stays deployable in a single AWS account without custom tooling; every retrieval path remains explainable live (no black-box hop); scope stays on business operations documents — never grows into a general-purpose enterprise content management system.

## Opportunity

Organizations that want to ground an LLM on their business operations knowledge face a retrieval correctness problem that vector similarity cannot solve: compliance-sensitive queries require exhaustive recall, but vector search optimises for nearest-match and silently drops documents worded differently from the query.

- **Functional job:** Ingest, govern, and query a typed corpus of enterprise standards, policies, SOPs, job aids, and transcripts — then retrieve documents with semantics matched to the knowledge type (exhaustive recall for normative, precision for descriptive) — through a single interface usable by both AI agents and humans in AI IDEs, without the caller knowing which retrieval path was used.
- **Emotional job:** Trust that when an agent or human asks "what policies apply here?", the answer is complete — no silent gaps that create a compliance risk the platform never surfaced.
- **Social job:** For the adopting team: be seen as treating organizational knowledge with the rigor it deserves — typed, partitioned, and traceable — rather than feeding an undifferentiated text blob to a vector index.
- **Struggling moment:** When a compliance officer or LLM agent queries a policy repository using vector similarity, policies worded differently from the query silently score below the retrieval threshold and are dropped. The system returns a result and the caller has no signal that it is incomplete. A missed policy is a compliance gap with no warning.

## Product-strategy fields

- **Central challenge (diagnosis):** A single retrieval semantic cannot serve both compliance and operational queries. Vector similarity is right for "find the best SOP for this task" and wrong for "find all policies that apply to this situation" — mixing them either silences compliance gaps or buries operational results in policy noise. The platform must route by knowledge type, not by query shape alone.
- **Guiding policy:** Replace the corpus-specific OpenCypher graph with a typed RDF/OWL knowledge graph (SPARQL) partitioned by retrieval semantics — normative vs. descriptive — and expose both partitions through a single MCP interface that routes internally to the right strategy.
- **Coherent actions:**
  1. Replace Neptune OpenCypher with Neptune SPARQL + an OWL schema-only ontology anchored to Schema.org and SKOS — no reasoning engine, just a typed schema
  2. Partition all knowledge into named graphs by retrieval semantics (`normative`, `descriptive`, `taxonomy`, `quarantine`) — hard isolation enforced at query time, not a routing hint
  3. Build a server-side multi-strategy router that selects exhaustive SPARQL or top-k vector + SPARQL expand based on the named graph the document belongs to, with a visible trace in every response
  4. Expose the platform through a typed MCP tool server (e.g. `ask`, `get_policies`, `get_sop`) usable by AI agents and humans in AI IDEs; retain a Function URL for automation and AgentCore
  5. Replace S3-hash delta ingestion with git commit-SHA delta so any git-tracked business ops document corpus can be ingested without bespoke tooling
- **Problem / segment sequence:** (1) Compliance-sensitive teams who need exhaustive policy recall and cannot trust vector-only retrieval to surface all applicable standards — the compliance gap is the forcing function. (2) Engineering teams evaluating graph-augmented retrieval who need a referenceable, reproducible platform they can test against their own corpus.
- **Horizon:** M2 — RDF/OWL knowledge platform (ontology, SPARQL, MCP tool server, git ingestion, OTEL observability)

## Assumptions

- Neptune SPARQL supports the named-graph partitioning model and query throughput required for exhaustive normative recall within acceptable latency for an interactive MCP call
- An OWL schema-only ontology (no runtime reasoning engine) is sufficient to type business operations documents without inference overhead
- MCP is an acceptable primary query interface for both AI agent workflows and humans in AI IDEs — raw SPARQL access is not required by either consumer
- Git-tracked document corpora are the dominant ingestion pattern for enterprise business operations content in the demo target segments
- The platform remains a demo-first artifact — adoptable and reproducible, but not a production-grade multi-tenant SaaS
- **Knowledge surface:** none detected

## Decomposition

-

### Decomposition decisions

-
