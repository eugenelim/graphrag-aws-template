# About the gap between synthetic visibility labels and real ACLs

> Why this template demonstrates permission-filtered retrieval with *synthetic*
> labels, where it deliberately stops short of real authorization, and exactly
> what you would build to cross that line. This page is for understanding the
> real/synthetic split; for the frozen team decision behind it, see
> [ADR-0009](../../adr/0009-access-control-synthetic-labels-not-real-authz.md).

## The question this page answers

You cloned the template, ran a query as `public-reader` and again as
`maintainer`, and watched a restricted KEP disappear from one answer and appear
in the other. The filtering is real and it rides the same query path as the
three retrieval modes. So a fair question follows: *is this access control I
could put in front of real tenants?* The short answer is no — and the reason is
not that the mechanism is weak. The mechanism is fine. What's synthetic is the
*trust* underneath it. This page draws the line between the two so you know
precisely what you're getting and what you'd still have to build.

## What the demo actually proves

The valuable, reusable thing here is a **seam**: one security context, derived
once, filters *both* retrieval layers at the point of retrieval. A persona
resolves to a `Clearance` (`visibility.py`); that clearance becomes a predicate
on the Neptune traversal — applied *during* the hop, on edges, so a forbidden
node never enters the frontier (`hybrid.py`, `store/neptune.py`) — and a `terms`
filter on the OpenSearch k-NN search, applied *during* the ANN scan on the Lucene
engine so a forbidden chunk never displaces a permitted one. The labels are
written to both stores from the same dual-write at ingest (`labels.py`), so the
two backends can't drift.

That architecture — filter at the point of retrieval, in every store, from a
single context — is exactly what a real system needs too. If you take one thing
from the permission slice, take the seam. It's correct, and it transfers.

## Where it deliberately stops

What does *not* transfer is the trust model. Three things make the current labels
a teaching stand-in rather than authorization:

- **The principal is asserted, not authenticated.** A `persona` is a query
  argument. Nothing proves the caller *is* that persona. In the demo that's safe
  only because the sole public ingress is an IAM-auth, scoped-principal Function
  URL, so the caller is the trusted operator role, not an end user (see the
  slice-4 boundary table in
  [security.md](../../architecture/security.md)). Point this at a real end user
  and the security context becomes spoofable — the filter turns into theater.
- **The labels are hand-authored fiction.** `labels.yaml` is a small map an
  author edits by hand. There is no source of truth tying a label to a real data
  classification or a real group.
- **The default is fail-open.** Omit the persona and you get *unrestricted*
  retrieval. That's the opposite of what authorization requires, and it's the
  next section's whole subject.

The mistake to avoid is the one that looks like progress: renaming tiers to
`allowed_groups` and accepting a caller-supplied group list. That makes the demo
*look* like multi-tenant authz while changing none of the above. A spoofable
group list filtered correctly is still spoofable.

## Two layers, and only one of them is synthetic

Keep these separate, because "we don't do real authz" is easy to misread as "the
security is fake," and that's wrong:

| Layer | In this template | Status |
| --- | --- | --- |
| **Infrastructure** authn/authz | IAM-auth Function URL + SigV4, least-privilege task/Lambda roles, the read-only Neptune grant ([ADR-0004](../../adr/0004-text2cypher-read-only-guard.md)), no-NAT VPC egress | **Real.** Production-grade, and *hardened* — not faked |
| **Data-level** authz | Visibility tiers / persona clearance / the synthetic label map | **Synthetic.** A teaching stand-in |

The infrastructure controls are genuine, and the
[`security-hardening-followups`](../../specs/security-hardening-followups/spec.md)
work tightens them further (uniform egress least-privilege, supply-chain scanning,
a `cdk-nag` gate). Only the data-level labels are the stand-in. The boundary
between the two is one-directional: hardening the real controls is always in
scope; promoting the synthetic labels to real authz is a scope change behind an
RFC.

## What a real system inserts at the boundary

Four controls turn the seam into authorization. The demo has the seam; a
production system adds these.

**1. An authenticated principal, resolved to groups you trust.** Replace the
asserted `persona` with a verified identity — Cognito, an OIDC provider, or IAM —
and resolve *that* identity to its groups server-side. The group list must come
from a source the caller can't forge, never from the request body. This is the
load-bearing change; without it, the other three are filtering on a lie.

**2. Default-deny by construction.** A real ACL denies unless something grants.
The demo's "no persona ⇒ see everything" is the textbook fail-open default a real
system inverts. The `security-hardening-followups` slice ships an **opt-in
default-deny mode** precisely so you can *see* the inversion: turn it on, supply
no principal, and retrieval returns nothing instead of everything. That mode is
still a teaching demonstration — it shows the right shape — but in production the
deny default is not optional, it's the only setting.

**3. Per-tenant isolation, not just per-row filtering.** Filtering rows by a
label is one layer. Multi-tenancy usually wants a harder boundary: separate
OpenSearch indexes or row-level security per tenant, and graph partitioning or
per-tenant query scoping, so a filter bug can't leak across a tenant line. The
demo is single-tenant by design, so it has nothing here to copy — you're building
it fresh.

**4. An authorization audit trail.** Who asked, as whom, and what the filter
admitted or denied — recorded durably, separately from the display trace. The
demo's filtered-out trace is the opposite of this: it *names* the items it hid, a
convenient teaching aid and a textbook enumeration oracle. A real system logs the
decision for audit and reveals nothing about what the requester may not see.

## The residuals you're trusting the ingress to contain

Two demo behaviors are safe *only* because that trusted, IAM-auth, scoped-principal
ingress stands in front of them. Name them, because they don't survive being
copied into a less-trusted context:

- the **fail-open default** (no persona ⇒ unrestricted), and
- the **enumeration-oracle** filtered-out trace (it reveals the identity of items
  it withheld).

Behind the operator-only Function URL, both are fine. In front of an end user,
both are bugs. If you move the persona to be the security principal, you have to
re-decide both before anything else.

## Why the template stops here

This isn't an oversight, it's the charter. The deliverable is an architect's
*comprehension and reproduction* of where authorization rides a GraphRAG path —
not a production multi-tenant authz system, which is a different product with an
identity provider and standing isolation infrastructure that would break the
ephemeral, teardown-first cost posture. When teaching clarity and production
completeness pull apart, the teaching posture wins and the production concern gets
named as a non-goal rather than half-built. [ADR-0009](../../adr/0009-access-control-synthetic-labels-not-real-authz.md)
records that decision and its tradeoff; the synthetic labels stay labeled
synthetic so nobody ships them mistaking the stand-in for the real thing.

So treat this guide as the map of the delta. The seam is yours to keep. The four
controls above are the work between it and production.

## See also

- [ADR-0009 — access-control depth: synthetic labels over real authz](../../adr/0009-access-control-synthetic-labels-not-real-authz.md) — the team's frozen decision and the boundary this page explains.
- [the security posture doc](../../architecture/security.md) — the slice-4 trust-boundary table, the real infrastructure controls, and the named residuals.
- [governed vs. risky graph queries](governed-vs-risky-graph-queries.md) — a sibling "where does the guardrail live" explanation, for the query-authoring boundary rather than the data-access one.
- the [`security-hardening-followups`](../../specs/security-hardening-followups/spec.md) spec — the real-control hardening and the opt-in default-deny demonstration referenced above.
