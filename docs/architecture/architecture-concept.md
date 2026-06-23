# Concept —

## Problem & context
We need to demonstrate how vector search and graph databases complement each other using a relatable, non-code dataset (e.g., the GitLab public handbook). We are building a demo application to show audiences exactly how to architect and implement GraphRAG by querying organizational entities and their relations.

## Constraints
Must use a public open-source Git repository containing strictly Markdown files. The solution must be built and deployed entirely within the AWS ecosystem using Amazon OpenSearch and Amazon Neptune.

## Candidate shapes (1–2)
* **Shape A** — Corporate handbook (GitLab) processed into dual indices: vector chunks for semantic text, and an entity graph representing organizational structure and document links.

## Provider / provider-class
AWS (Amazon OpenSearch, Amazon Neptune). By relying on MANAGED AWS services, we meet availability and infrastructure-scaling concerns out of the box, meaning we only have to BUILD the custom Markdown parsing logic and the hybrid query orchestration layer ourselves.

## Top 2–3 prioritized quality attributes
1. **Explainability / Demo-ability:** (High business importance × High risk) - The architecture and data flow must be straightforward enough to explain live; if the implementation is a black box, the demo fails.
2. **Modularity:** (Medium importance × Low risk) - Because we are slicing delivery (Vector first, then Graph), the ingestion and query layers must cleanly decouple.

## Key tradeoff / open decision(s)
* **Hybrid Query Orchestration Pattern:** We need to decide exactly how the two databases inter-relate during a query. The primary tradeoff is deciding whether to use Vector search strictly as the entry point to identify a starting Graph node (Vector -> Graph hop), or whether to execute parallel searches and merge the context at the LLM synthesis layer. 

## Embedding Model
We will use Amazon Titan v2.

