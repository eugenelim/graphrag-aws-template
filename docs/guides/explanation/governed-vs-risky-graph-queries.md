# About governed templates vs. risky text-to-query — two ways to ask a graph

> Why this template gives a graph-backed LLM **two** ways to turn a natural
> question into a Neptune openCypher query — a *governed* path (expert-authored
> parameterized templates the model only *selects*) and a *flexible* path (the
> model *writes* the query, executed read-only) — and how to decide which one a
> given workload should use. This page is for understanding the trade-off; for the
> commands, see the governed path in action below.

## The question this page answers

Once you have a knowledge graph, you have to let people *ask* it — and almost
nobody asks in openCypher. Something has to turn "which KEPs does SIG Network
own?" into a graph query. There are two honest ways to do that, and they sit at
opposite ends of a control-vs-flexibility trade-off. A team adopting GraphRAG has
to pick one (or offer both and route between them), and the choice is a
governance decision as much as a technical one. This page is the map.

## The two paths

**Cypher Templates — the governed path** (this slice, `opencypher-templates`).
A human expert authors a small, fixed library of **parameterized** openCypher
queries and reviews them like any other code. At query time the LLM's *only* job
is to **select** one template by id from that library; the parameters are extracted
from the question and **validated deterministically** (an entity slot is resolved
through the same normalizers the ingest path uses and confirmed against the graph;
an enum is checked against a declared set; an integer is parsed and bounded). The
query that runs is always one of the vetted strings, and every value is bound
through the openCypher parameter map — never a string the model wrote, never a
value spliced into the query text.

**Text2openCypher — the flexible path** (a separate slice,
`text2opencypher-guarded`). The LLM **writes** the openCypher itself, from the
question and a schema description. This answers questions no one wrote a template
for — but the executable surface is now whatever the model emits, so it needs a
different guardrail: the query runs against a **read-only** endpoint (Neptune's
reader is read-only-enforced), plus validation, so a generated mutation or a
runaway traversal can't do damage.

## Why the governed path needs no read-replica

The single most important contrast: **the governed path is injection-safe and
read-only *by construction*, so it does not need the read-replica enforcement the
text2cypher path relies on.**

- The executable surface is a **fixed, reviewed library**. A reviewer reads every
  query that can ever run, once, at PR time. Nothing the model says changes which
  query executes — only *which of the vetted ones*.
- Read-only is guaranteed by **review plus a lint** (every template is checked to
  contain no mutating clause), not by where the query is sent. The query could run
  against the primary endpoint and still be safe.
- Parameters are **bound, not interpolated**, and **validated** before binding, so
  the classic injection vector (user text becoming query structure) is closed
  whatever the model returns.

The text2cypher path can make none of those guarantees — the query text is
model-authored and unbounded — so it *must* lean on endpoint-level read-only
enforcement and query validation. That's the same reason the
[RFC-0001 feasibility note](../../rfc/0001-notes/aws-feasibility.md) flags the
read-only reader endpoint as the *text2cypher* guardrail specifically.

## When to choose which

| If you need… | Reach for |
| --- | --- |
| Auditability — every possible query reviewed in advance | **Governed templates** |
| A bounded, predictable cost/latency profile | **Governed templates** |
| Answers only to known, recurring question shapes | **Governed templates** |
| Open-ended questions no one templated | **Text2openCypher (guarded)** |
| Exploration where coverage matters more than control | **Text2openCypher (guarded)** |

The rule of thumb: **start governed, escalate to text2cypher only for the
questions templates can't cover** — and when you do, treat the read-only endpoint
and query validation as non-negotiable, because you've traded the construction-time
guarantee for run-time enforcement.

## See the governed path run

The governed path runs offline (no AWS credentials) over the bundled fixture
corpus:

```bash
graphrag governed-query \
  --community packages/graphrag/tests/fixtures/corpus/community \
  --enhancements packages/graphrag/tests/fixtures/corpus/enhancements \
  --q "Which KEPs does SIG Network own?"
```

Read the printed **audit trace** top to bottom — it is the whole point of the
governed path:

- `template: sig_owned_keps` — which vetted query was selected, and why.
- `bound params: sig = sig:sig-network (via link:slug)` — the value, and *how* it
  was extracted and confirmed.
- `cypher:` and `param map:` shown **separately** — you can see the parameterized
  query and the values it was bound with, never spliced together.
- `rows:` and `answer:` — the result and the grounded summary over it.

Add `--bedrock` to use real Bedrock Claude for selection and synthesis, or
`--function-url <url>` to drive the deployed in-VPC query Lambda (the live path
sends `mode: "governed"`). The curated demo questions live in the showcase set
(`packages/graphrag/src/graphrag/showcase/queries.yaml`, `governed_queries`).
