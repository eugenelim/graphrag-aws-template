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

**Text2openCypher — the flexible path** (the `text2opencypher-guarded` slice). The
LLM **writes** the openCypher itself, from the question and a schema description.
This answers questions no one wrote a template for — but the executable surface is
now whatever the model emits, so it needs a different, run-time guardrail. That
guard is **layered** ([ADR-0004](../../adr/0004-text2cypher-read-only-guard.md)): a
read-only **static validator** rejects any mutating clause / `CALL` / multi-statement
/ unbounded traversal and bounds the `LIMIT`; a **bounded self-heal** retries once on
a rejection; and — the load-bearing backstop — the query Lambda's Neptune grant is
**IAM read-only** (`ReadDataViaQuery` + `connect` only), so a write the validator
missed is denied by AWS *before the engine runs it*, plus a Neptune engine query
timeout caps a runaway read.

## Why the governed path needs no run-time guard

The single most important contrast: **the governed path is injection-safe and
read-only *by construction*, so it does not need the run-time guard the text2cypher
path relies on.**

- The executable surface is a **fixed, reviewed library**. A reviewer reads every
  query that can ever run, once, at PR time. Nothing the model says changes which
  query executes — only *which of the vetted ones*.
- Read-only is guaranteed by **review plus a lint** (every template is checked to
  contain no mutating clause), not by where the query is sent or what the role can do.
- Parameters are **bound, not interpolated**, and **validated** before binding, so
  the classic injection vector (user text becoming query structure) is closed
  whatever the model returns.

The text2cypher path can make none of those guarantees — the query text is
model-authored and unbounded — so it *must* lean on a run-time guard: the read-only
validator **plus** the IAM read-only data-action scope and the engine query timeout
(the backstops that hold even when the validator misses something).

> **A note on the read-replica.** The [RFC-0001 feasibility
> note](../../rfc/0001-notes/aws-feasibility.md) §2 originally flagged Neptune's
> read-only *reader endpoint* as the text2cypher guardrail. This template guards with
> **IAM read-only data-action scoping** instead, because the demo runs a **single**
> Neptune Serverless instance — a reader endpoint that enforces read-only needs a
> standing read **replica**, which would double the idle cost and break the
> teardown-first posture (ADR-0002). IAM scoping gives the same "writes are impossible"
> guarantee with no extra instance, enforced one layer lower (at the AWS auth layer,
> before the engine). The full rationale is [ADR-0004](../../adr/0004-text2cypher-read-only-guard.md).

## When to choose which

| If you need… | Reach for |
| --- | --- |
| Auditability — every possible query reviewed in advance | **Governed templates** |
| A bounded, predictable cost/latency profile | **Governed templates** |
| Answers only to known, recurring question shapes | **Governed templates** |
| Open-ended questions no one templated | **Text2openCypher (guarded)** |
| Exploration where coverage matters more than control | **Text2openCypher (guarded)** |

The rule of thumb: **start governed, escalate to text2cypher only for the
questions templates can't cover** — and when you do, treat the read-only validator,
the IAM read-only scope, and the engine query timeout as non-negotiable, because
you've traded the construction-time guarantee for run-time enforcement.

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

## See the text2cypher path run

The flexible path runs the same way — offline by default over the bundled corpus:

```bash
graphrag text2cypher-query \
  --community packages/graphrag/tests/fixtures/corpus/community \
  --enhancements packages/graphrag/tests/fixtures/corpus/enhancements \
  --q "Which KEPs did @aojea author?"
```

Read its **audit trace** top to bottom — it is the whole point of the flexible path,
and the contrast with the governed trace is the lesson:

- `schema:` — exactly what the model was told the graph looks like (no hidden context).
- `generated attempts:` — **the query the model wrote**, with the validator's
  `verdict: valid` (or `rejected: <rule>` + a self-heal retry). This is the line that
  doesn't exist on the governed path — here the model authored the query *structure*.
- `executed query:` — the query that actually ran (read-only-checked) — then `rows:`
  and `answer:`.

Offline, the model-authored query runs against a **bounded read-subset evaluator**
(there is no local Neptune; see
[develop-and-test-offline](../../architecture/develop-and-test-offline.md)); `--bedrock`
uses real Claude to write the query, and `--function-url <url>` drives the deployed
Lambda (the live path sends `mode: "text2cypher"`, executes genuinely arbitrary
openCypher on Neptune under the read-only-scoped role). The flexible-path demo
questions live alongside the governed ones (`queries.yaml`, `text2cypher_queries`).

**The head-to-head.** Ask the *same* question both ways —
`"Which KEPs does SIG Network own?"` is in both showcase sets:

```bash
graphrag governed-query     --community … --enhancements … --q "Which KEPs does SIG Network own?"
graphrag text2cypher-query  --community … --enhancements … --q "Which KEPs does SIG Network own?"
```

Both return `kep-1880, kep-2086`. The governed trace shows a *vetted template selected*
and a *parameter bound*; the text2cypher trace shows a *query the model wrote* and a
*validation verdict*. Same answer, two different trust stories — that is the choice this
page is about.
