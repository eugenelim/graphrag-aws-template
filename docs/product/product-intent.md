# Intent:
* Level: feature
* Maturity: greenfield

## Outcome
* **Input (steerable):** Document chunk size, embedding model selection, and the extraction rules for graph nodes/edges (e.g., parsing front-matter and relative Markdown links).
* **Outcome (lagging):** The audience clearly grasps the distinct value and retrieval patterns of Vector search, Graph search, and hybrid GraphRAG, supported by a clear understanding of the underlying architecture.
* **Guardrail:** The demonstration must strictly focus on Markdown files and natural language entities (like teams or processes); it must NOT focus on functional code.

## Opportunity
We need to demonstrate the distinct value of GraphRAG (hybrid vector + graph) over standalone vector search. To make this resonate, we need a relatable, non-code dataset—like a public knowledge garden or corporate handbook—where the entities (teams, roles, guides) and their relationships are intuitive to the audience without requiring deep technical domain knowledge.

## Assumptions
* We can cleanly parse the GitLab handbook (Markdown, front-matter, and relative links) into discrete graph nodes and vector chunks. **(Riskiest)**
* The audience can grasp the value proposition through technical commands and architectural explanations, without needing a polished, heavy graphical UI.

## Decomposition
* **Slice 1:** Vector ingestion and retrieval pipeline (chunking Markdown, embedding, and querying).
* **Slice 2:** Graph ingestion and traversal pipeline (extracting entities/links and executing structural queries).
* **Slice 3:** Hybrid GraphRAG orchestration (inter-relating the two systems).

