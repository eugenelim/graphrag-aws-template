> **Partially superseded — ini-002 in progress.**
> The offline-first posture (no AWS credentials, in-memory stores, deterministic stand-ins)
> remains the correct model. Implementation references are changing: `RuleText2CypherGenerator`
> becomes `RuleText2SPARQLGenerator`; the offline Neptune store (`store/memory.py`) becomes
> an rdflib-backed SPARQL memory store. See [`biz-ops-knowledge-graph/design.md`](biz-ops-knowledge-graph/design.md)
> for the current retrieval architecture.

# Developing and testing offline — and the text2cypher offline-execution decision

> How to build and exercise this template **without a deployed AWS stack**, and the
> decision record for *why offline execution of model-authored openCypher works the way
> it does*. Read this before touching the `text2opencypher-guarded` slice or wondering
> why there is no local Neptune.

## The offline-first posture

Every retrieval path in this repo runs **offline by default** — no AWS credentials, no
deployed stack — over the bundled fixture corpus
(`packages/graphrag/tests/fixtures/corpus/`). That is what makes the gate suite
(`ruff` / `mypy` / `pytest`) credential-free and the demo laptop-runnable. Offline uses:

- an **in-memory `GraphStore`** (`store/memory.py`) and vector store, populated by
  ingesting the fixture corpus;
- **non-semantic, deterministic** stand-ins for the model calls — `HashEmbedder`,
  `TemplateSynthesizer`, `RuleTemplateSelector`, and (this slice)
  `RuleText2CypherGenerator` — each labeled *non-semantic* in its output so a reader is
  never misled. The honest semantic behaviour is the **live** path (Bedrock + Neptune),
  proven by each slice's live acceptance criterion.

Run any path offline by omitting `--bedrock` / `--neptune-endpoint` / `--function-url`:

```bash
graphrag text2cypher-query \
  --community packages/graphrag/tests/fixtures/corpus/community \
  --enhancements packages/graphrag/tests/fixtures/corpus/enhancements \
  --q "Which KEPs did @aojea author?"
```

`--bedrock` swaps in real Bedrock Claude (needs creds); `--function-url <url>` drives the
deployed in-VPC query Lambda. Same for `governed-query`, `hybrid-query`, `vector-query`.

## The text2cypher problem: executing model-authored openCypher offline

The governed (`opencypher-templates`) path executes offline trivially: each template ships
a paired app-layer `evaluate` over the `GraphStore` seam, so the in-memory backend runs the
*same* query the live Neptune backend does (the dual-form invariant). But text2cypher is
different — the LLM **writes arbitrary openCypher**, so there is no paired evaluator to fall
back to. Something has to *run* that query offline, or the offline demo can only validate and
refuse, never return rows.

### What we looked at (and why we didn't use it)

There is **no high-fidelity, low-weight way to run Neptune openCypher locally.** Surveyed
2026-06-25 (AWS openCypher docs + the upstream project states):

| Option | Neptune-dialect fidelity | Weight | Verdict |
| --- | --- | --- | --- |
| **An official local Neptune emulator** | — | — | **Does not exist.** AWS ships none; the documented pre-deploy path is a static compatibility checker + a live cluster. |
| **TinkerPop / Gremlin-local + `cypher-for-gremlin`** | very low | JVM + Docker | Rejected — `cypher-for-gremlin` is **unmaintained since 2019**; TinkerPop speaks Gremlin, not openCypher. |
| **Neo4j / Memgraph (Docker)** | **low–moderate** (string-vs-int ids, no `shortestPath`, missing funcs — *false* passes/failures) | **heavy** (Docker/JVM) | Rejected — low fidelity *and* breaks the pure-Python, laptop-runnable, PyYAML-free-Lambda-bundle posture. |
| **Kùzu (embedded, pip-installable)** | unknown (no Neptune mapping) | light | Rejected — project **archived Oct 2025**. |

The decisive point: every external engine is **simultaneously low-fidelity to Neptune's
dialect and heavyweight**, the worst combination — it would mislead *and* break the repo's
posture. This is recorded as
[ADR-0004](../adr/0004-text2cypher-read-only-guard.md) (Alternatives) and the
[`text2opencypher-guarded` spec](../specs/text2opencypher-guarded/spec.md) (AC12).

### What we do instead

**A pure-Python bounded read-subset evaluator** (`cypher_eval.py`, `eval_read_query`) runs
the model-authored query offline over the in-memory `GraphStore`, for a **deliberately small
read grammar** — and **live Neptune is the execution-fidelity oracle** (the slice's live AC
proves genuinely arbitrary generation against the real engine). The evaluator is *labeled a
subset* everywhere it surfaces; it never claims Neptune fidelity. It supports exactly:

- node by id — `MATCH (n:Entity {id: 'X'}) RETURN n`
- nodes by kind — `MATCH (n:Entity) WHERE n.kind = 'K' RETURN n`
- one hop (out / in) — `MATCH (a:Entity {id: 'X'})-[:REL {kind: 'EK'}]->(n:Entity) RETURN n`

with `ORDER BY` (ignored — results sort by node id) and a trailing `LIMIT k` honored.
Anything outside the subset raises `UnsupportedOfflineQuery`, which the orchestrator surfaces
as a refusal reading *"runs live on Neptune, not in the offline subset"* — **never false
rows**. The offline `RuleText2CypherGenerator` emits only within this grammar, so the offline
default path returns real rows over the fixture corpus.

This mirrors AWS's own posture (no local executor; reserve a live cluster for real execution)
while keeping the repo dependency-clean. A future enhancement — wiring AWS's openCypher
**Compatibility Checker** as an additional static-lint step — is noted but not adopted (it is
a linter, not a runtime).

## What is proven where

| Property | Offline (CI / laptop) | Live (the slice's live AC) |
| --- | --- | --- |
| Read-only **validation** + bounded self-heal + refusal | ✓ (full) | ✓ |
| Audit-trace ordering / narratability | ✓ (full) | ✓ |
| Query **execution returning rows** | ✓ for the bounded subset (labeled) | ✓ genuinely arbitrary openCypher |
| The **IAM read-only write backstop** (ADR-0004) | synth-asserted (`cdk synth`) | proven (out-of-band IAM-deny) |
| Neptune-dialect fidelity | ✗ (subset, by design) | ✓ (the oracle) |

So: trust the offline path for the **guard, orchestration, and trace**; trust the **live
path** for *semantic generation* and *Neptune-dialect execution*.
