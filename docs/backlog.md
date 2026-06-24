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

### hybrid-orchestration-synthesis-edges

**Quality follow-up (AC9 surfaced it; AC9 itself is met).** The live hybrid query works
end-to-end (verified 2026-06-24, 22.7 s), and the seed-and-expand trace *reaches* the owned KEPs
correctly — but the merged context handed to the Bedrock Claude synthesizer lists the graph
**nodes** without their **typed edges** (`OWNS`, `TECH_LEADS`, …). So Claude hedges ("the graph
facts do not include explicit owns edges connecting @thockin to KEPs") instead of stating the
ownership chain, even though the chain is in the trace. The structural win + trace are correct;
this is purely the synthesis-context richness. **Fix:** include the reached edges (src → kind →
dst) in the context `synthesize()` builds — `hybrid_query` already has the hop trace with edge
kinds — so the model can ground the relationship, not just the node set. Verify by re-running the
live entity-led query and confirming the answer names KEP-1880/2086 as `sig-network`-owned.

<!-- Add one section per spec with open work, e.g.:

## <spec-name>

- **AC<N> (deferred: <anchor>):** <what's open> — blocked on <X>; unblocked by <Y>.

-->
