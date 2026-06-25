# Three-mode demo — presenter script

The payoff of the GraphRAG-on-AWS demo: on one question, show how **vector-only**,
**graph-only**, and **hybrid** retrieval diverge — and *why* the hybrid answer is
better when it is. This script walks a presenter through the consolidated showcase set
with the exact CLI commands and what to point at in each trace.

- **Queries:** `packages/graphrag/src/graphrag/showcase/queries.yaml` (loaded by
  `graphrag.showcase.load_showcase`). Each query is labeled with the mode it should
  *win*, its gold entity/chunk ids, and a one-line highlight.
- **Offline by default:** every command below runs against the bundled fixture corpus
  with the in-memory stores + the offline **non-semantic** embedder/synthesizer — so it
  is reproducible and credential-free. The CLI prints a `NON-SEMANTIC` banner so the
  audience is never misled: the structural graph/hybrid win is real offline; the
  *semantic* win is shown live (the `--function-url` / `--bedrock` path) and by the
  slice-2 frozen-vector eval.
- **The trace is the pedagogy** (charter principle 1): every verb prints an ordered
  **seeds-by-source → hops → citations → answer** trace. Point at it; there is no
  black-box hop.

Set the corpus paths once:

```bash
CORPUS=packages/graphrag/tests/fixtures/corpus
COMMUNITY=$CORPUS/community
ENHANCEMENTS=$CORPUS/enhancements
```

## Act 1 — vector wins (semantic, no entity to seed)

A paraphrased, prose-rich question with no named entity. Vector retrieves the right
KEP chunk by meaning; the graph has nothing to seed from.

```bash
graphrag compare --community "$COMMUNITY" --enhancements "$ENHANCEMENTS" \
  --q "How does service internal traffic policy keep traffic node-local?"
```

**Point at:** the `vector-only` block surfaces the KEP-2086 README chunk
(`enhancements/keps/sig-network/2086-service-internal-traffic-policy/README.md#0`); the
`graph-only` block has no question seed, so its expansion is empty. This is the honest
"vector is the right tool here" case (showcase id `vec-traffic-policy`).

## Act 2 — graph wins (entity-led, multi-hop structure)

The question names an entity and asks for a *relationship* across hops — exactly what
prose similarity cannot enumerate.

```bash
graphrag compare --community "$COMMUNITY" --enhancements "$ENHANCEMENTS" \
  --q "Which KEPs does the SIG @thockin tech-leads own?"
```

**Point at:** in `graph-only` and `hybrid`, the hop trace expands
`person:thockin -TECH_LEADS-> sig:sig-network -OWNS-> {kep-1880, kep-2086}` (a 2-hop
path, so `--max-hops 2`), and the result set **enumerates the owned KEPs**. The
`vector-only` block does **not** enumerate that owned set — it has no edges to follow.
This is the structural demonstration that graph augments vector (showcase id
`graph-thockin-owned-keps`).

Run `graphrag hybrid-query` on the same question to see the **dual-seed** split up
close:

```bash
graphrag hybrid-query --community "$COMMUNITY" --enhancements "$ENHANCEMENTS" \
  --q "Which KEPs does the SIG @thockin tech-leads own?"
```

**Point at:** `seeds: question: person:thockin` (the `@handle` linked from the
question — note it resolves to the **person**, not the SIG) alongside the
`seeds: vector:` owners of the top-k chunks; then the hops; then citations; then the
answer.

## Act 3 — hybrid wins (semantic question, graph join)

A semantic question whose *answer* needs the graph join: the question seeds an entity,
vector seeds the relevant prose, and the merge lands the precise result.

```bash
graphrag compare --community "$COMMUNITY" --enhancements "$ENHANCEMENTS" \
  --q "Of the KEPs the SIG @thockin tech-leads owns, which one keeps traffic node-local?"
```

**Point at:** `hybrid` seeds `person:thockin` from the question *and* the
traffic-policy chunk from vector, expands to the owned KEPs, and the merged context
lets the synthesizer land **KEP-2086** — neither pure mode gets there as cleanly
(showcase id `hybrid-thockin-traffic`).

## Live path (semantic, real Bedrock Claude)

The offline synthesizer is non-semantic by design. To show the real round trip,
target the deployed in-VPC query Lambda behind its **IAM-auth Function URL** (the CLI
signs the request SigV4, `service=lambda`, signature covering the body):

```bash
graphrag hybrid-query --community "$COMMUNITY" --enhancements "$ENHANCEMENTS" \
  --function-url "$(aws cloudformation describe-stacks --stack-name GraphragSlice1 \
      --query "Stacks[0].Outputs[?OutputKey=='QueryFunctionUrl'].OutputValue" --output text)" \
  --q "Which KEPs does the SIG @thockin tech-leads own?"
```

The deploy + live smoke is recorded in
[`docs/architecture/deployment-and-verification.md`](../../architecture/deployment-and-verification.md).

## Applying this to your own corpus

Once the divergence above makes sense, the next question is how to slice and route
*your* data so the graph earns its keep — what to embed, what to extract as nodes and
edges, and how entity-ID stability decides whether graph mode pays off. That's a
design model, not a demo step, so it lives next door:
[**Choosing what to ingest, and how to slice your corpus**](../explanation/choosing-what-to-ingest.md).

## The showcase set at a glance

| Mode | Showcase ids |
| --- | --- |
| vector | `vec-traffic-policy`, `vec-multiple-cidrs`, `vec-in-place-resize`, `vec-node-allocatable`, `vec-network-charter`, `vec-node-charter` |
| graph | `graph-thockin-owned-keps`, `graph-network-owns`, `graph-node-owns`, `graph-kep-1287-approvers`, `graph-network-leaders`, `graph-network-subprojects` |
| hybrid | `hybrid-thockin-traffic`, `hybrid-network-cidr-detail`, `hybrid-node-resize-owner`, `hybrid-network-charter-keps`, `hybrid-thockin-role`, `hybrid-node-allocatable-owner` |

Each row's `query`, `gold`, and `highlight` live in `queries.yaml`; a test
(`test_showcase.py`) asserts every gold id resolves in the fixture corpus, so the
curation stays honest.

## Permission-filtered retrieval — the two-persona contrast (slice 4)

> **These visibility labels are a *synthetic teaching stand-in* for access control — not
> real authorization.** They show *where* permission filtering rides the retrieval path;
> they are never production IAM, multi-tenancy, or data authz (charter principle 5).

The same three-mode query takes an optional `--persona`. Two synthetic labels are applied
to the corpus at ingest (`packages/graphrag/src/graphrag/labels.yaml`): **KEP-1287** is
`restricted`, **KEP-1880** is `internal`. Three personas have ascending clearance:
`public-reader` (sees `public`), `member` (`public` + `internal`), `maintainer` (all).

Run the same question as two personas and watch the result diverge:

```bash
# A public reader: the restricted KEP-1287 is filtered out
graphrag compare --community <c> --enhancements <e> \
  --q "What KEPs does SIG Node own?" --persona public-reader
# -> sees kep-9; KEP-1287 is absent (and its owning OWNS edge is never traversed)

# A maintainer: the same question now surfaces the restricted KEP
graphrag compare --community <c> --enhancements <e> \
  --q "What KEPs does SIG Node own?" --persona maintainer
# -> sees kep-9 AND kep-1287
```

**What to point at in the trace:** the `persona:`/`clearance:` line names the active
clearance, and the `filtered (visibility; …)` line names what was removed. The teaching
point is *where* the filter runs: the graph filter applies **during traversal, on edges**
— the `OWNS` edge into a restricted KEP is never followed, so a forbidden node never
enters the frontier and cannot leak via a reachability path (it is not merely dropped from
the final result). The vector mode applies the same clearance as an OpenSearch metadata
filter during the k-NN search; the hybrid mode applies both. Omit `--persona` and the
output is identical to the unfiltered runs above.

> The `filtered (…)` line names the *identity* of items the persona can't see — a teaching
> observability aid. A real ACL system would **not** reveal that to the requester; it is
> safe here only because the labels are non-authz and the live query ingress is the
> IAM-auth, scoped-principal Function URL (the caller is the trusted operator, not an
> end-user). See [`security.md`](../../architecture/security.md).

### Permission showcase queries

| Persona | Question | Sees | Filtered |
| --- | --- | --- | --- |
| `public-reader` | What KEPs does SIG Node own? | `kep-9` | `kep-1287` (restricted) |
| `maintainer` | What KEPs does SIG Node own? | `kep-9`, `kep-1287` | — |
| `public-reader` | What KEPs does sig-network own? | `kep-2086` | `kep-1880` (internal) |
| `member` | What KEPs does sig-network own? | `kep-1880`, `kep-2086` | — |

These live in `queries.yaml` under `permission_queries`; `test_showcase.py` asserts each
`visible`/`filtered` id resolves in the fixture corpus **and** is consistent with the
labels + the persona's clearance, so the curation stays honest.
