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
  --function-url "$(aws cloudformation describe-stacks --stack-name GraphragStack \
      --query "Stacks[0].Outputs[?OutputKey=='QueryFunctionUrl'].OutputValue" --output text)" \
  --q "Which KEPs does the SIG @thockin tech-leads own?"
```

The deploy + live smoke is recorded in
[`docs/architecture/deployment-and-verification.md`](../../architecture/deployment-and-verification.md).

## The showcase set at a glance

| Mode | Showcase ids |
| --- | --- |
| vector | `vec-traffic-policy`, `vec-multiple-cidrs`, `vec-in-place-resize`, `vec-node-allocatable`, `vec-network-charter`, `vec-node-charter` |
| graph | `graph-thockin-owned-keps`, `graph-network-owns`, `graph-node-owns`, `graph-kep-1287-approvers`, `graph-network-leaders`, `graph-network-subprojects` |
| hybrid | `hybrid-thockin-traffic`, `hybrid-network-cidr-detail`, `hybrid-node-resize-owner`, `hybrid-network-charter-keps`, `hybrid-thockin-role`, `hybrid-node-allocatable-owner` |

Each row's `query`, `gold`, and `highlight` live in `queries.yaml`; a test
(`test_showcase.py`) asserts every gold id resolves in the fixture corpus, so the
curation stays honest.
