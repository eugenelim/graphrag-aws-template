# Backlog — open items by spec

Single index of **open** work across every spec in `docs/specs/`. Each item
names the spec, the Acceptance Criterion (where one applies), what's blocking
it, and how it gets unblocked. Closed/shipped work is **not** kept here — see
each spec's Changelog and [`product/changelog.md`](product/changelog.md).

This is the tactical **backlog**: per-instance, no pack-side source after first
install — it's yours to curate. It is distinct from the **product roadmap**
(strategy, not a work index) at [`product/roadmap.md`](product/roadmap.md).
"Roadmap" = direction; "backlog" = the work/deferral index.

Deferred acceptance criteria point here by **anchor**: a spec criterion written
`- [ ] <outcome> (deferred: <anchor>)` means `<anchor>` resolves to a heading in
this file (GitHub heading-slug rules — lowercase, spaces become hyphens). The
deferral lives here, version-controlled and greppable, not in a PR comment that
rots. See `CONVENTIONS.md` § 4 (Spec metadata contract).

## How this file is maintained

- Every spec records its own `Status:` field and `Acceptance Criteria`
  checkboxes. This file aggregates the **open** items so they're visible in one
  place — it is not the source of truth.
- When an AC closes or a spec ships, update the spec first, then **remove** the
  now-closed item here in the same change (closed work lives in the spec
  Changelog / `product/changelog.md`, not here).
- When a new spec lands with open ACs, add a section here.
- If an item here is no longer accurate against the underlying spec, trust the
  spec and fix this file.

---

## graph-ingestion-resolution

<!-- Deferral anchors are `###` headings whose GitHub slug equals the
     `(deferred: <anchor>)` token in the spec — the convention this file's header
     describes, and what `lint-spec-status.py` invariant (iv) resolves against. -->

### graph-ingestion-resolution-live-deploy

**AC9 (deferred).** Live-AWS verification of one-command `deploy`/`destroy` — that
`cdk deploy` provisions the slice-1 stack, uploads the corpus snapshot, and runs the
ingestion task once; that `cdk destroy` leaves **no billable resource**; and that the
Budgets alarm actually fires. Blocked on a live AWS account (this PR ships and
synth-tests the IaC, but cannot deploy from CI). Unblocked by a maintainer running
the documented deploy/destroy on a clean account and recording the teardown
smoke-check result.

### graph-ingestion-resolution-full-corpus-eval

**AC5 follow-on (deferred).** Run the resolver eval over a *full* clone of
`kubernetes/community` + `kubernetes/enhancements` (not the pinned real-excerpt
sample committed for CI) and record the precision/recall on the complete
shared-entity set. The committed CI eval (AC5) already runs over real, pinned repo
excerpts — this extends the open confirmation to the whole corpus. Unblocked by
`graphrag resolve-eval --corpus <full-clone>` with a labeled sample of the full
handle/slug set.

## hybrid-orchestration

### hybrid-orchestration-live-deploy

**AC9 (deferred) — blocked on a live perf fix, not on infra.** The stack was deployed live
(2026-06-24, `CREATE_COMPLETE`) and the **Fargate dual-write passed end-to-end** — graph
(22 nodes / 28 edges / 6 cross-source merges incl. `person:thockin`, `sig:sig-network`) + vector
(13 chunks via live Bedrock Titan); the slice-1 Neptune smoke probe returned `{"ok": true}`; and
`cdk synth` validated the `AWS_IAM` Function URL + named-principal invoke grant + the Bedrock
grant scoped to the `inference-profile` + `foundation-model` ARNs. **What's left:** the live
hybrid query itself — the query Lambda **times out at its 120s budget** (CloudWatch `Duration`
max = 120000 ms) running *successful* work, because `query.expand_neighborhood` makes
`O(frontier × 6 edge-kinds × 2 directions)` **sequential** `neighbors()` openCypher round-trips
per hop — instant in-memory, but hundreds of slow sequential queries against **Neptune Serverless
at minimum NCU**. Unblocked by the perf fix below, then a redeploy + the SigV4 Function-URL
invocation recorded in `deployment-and-verification.md`. (The `deploy.sh` `InvokerRoleArn`
parameter gap found during this run is **already fixed** in the slice-3 PR.)

**Fix — batch the neighbor fetch (the unblocker).** Add a batched neighbor method to `GraphStore`
(one openCypher query per hop for the whole frontier — the `all_edges()` MATCH shape with a
parameterized `$ids` list, both directions), with a **default app-layer fan-out over `neighbors()`**
so `MemoryGraphStore` and the per-hop trace stay byte-identical; refactor `expand_neighborhood` to
consume it; give the CLI's `--function-url` client a longer, configurable read timeout (it
currently inherits the Neptune adapter's hard-coded 30s, too short for a multi-step hybrid query).
This is a structural change to the store seam (Neptune openCypher = injection surface) and warrants
its own adversarial + security review before merge. Then redeploy, run the dual-write, SigV4-POST
*"Which KEPs does the SIG @thockin tech-leads own?"* to the `QueryFunctionUrl`, confirm an answer +
citations + a dual-seed trace whose `question` seeds include `person:thockin`, and `cdk destroy`.

<!-- Add one section per spec with open work, e.g.:

## <spec-name>

- **AC<N> (deferred: <anchor>):** <what's open> — blocked on <X>; unblocked by <Y>.

-->
