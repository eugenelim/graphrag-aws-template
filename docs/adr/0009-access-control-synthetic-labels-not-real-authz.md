# ADR-0009: Access-control depth — synthetic visibility labels over real authorization

- **Status:** Accepted <!-- Proposed | Accepted | Rejected | Deprecated | Superseded by ADR-NNNN -->
- **Date:** 2026-06-30
- **Decision-makers:** eugenelim
- **Consulted:** security-reviewer (secure-design lens)
- **Supersedes:** none
- **Related:** charter (principle 5, principle 7, Scope "Production authorization" non-goal); spec `security-hardening-followups` (B5 default-deny mode); `docs/architecture/security.md` (slice-4 boundary table); ADR-0004 (read-only Neptune grant — a *real* control); ADR-0002 (no-NAT VPC topology — a *real* control); the "from synthetic labels to real ACLs" guide

<!-- Recording an established charter posture as a durable decision (a backfill of
principle 5 / the Scope non-goal into the ADR record), prompted by the B5
default-deny work raising "could we just make this real?". See References. -->

## Decision summary

- **Decision:** Data-level access control in this template stays a **synthetic teaching stand-in** (visibility tiers / persona clearance + the opt-in default-deny mode); real authorization is **out of scope** and is a fork-level change behind an RFC.
- **Because:** the charter's job is an architect's *comprehension and reproduction* of where authz rides a GraphRAG path — not a production multi-tenant authz system (principle 7).
- **Applies to:** the *data-level* authz layer only (`visibility.py` / `labels.py` / the persona clearance) — **not** the real infrastructure controls (IAM-auth Function URL, SigV4, least-privilege roles, read-only Neptune grant, no-NAT egress), which are production-grade and are hardened, not faked.
- **Tradeoff accepted:** the demo cannot be pointed at private data or a real end-user as the security principal without first crossing this boundary — and that limit must be stated loudly, repeatedly, at every label.
- **Revisit if:** the project's mission shifts from a teaching reference to a deployable multi-tenant product (an RFC-level scope change — likely a fork).

## Context

The template demonstrates **permission-filtered retrieval** (charter pattern 7): a query carries a security context that filters *both* the Neptune traversal and the OpenSearch vector search, so a forbidden item never enters an answer. The mechanism is real and rides the same query path as the three retrieval modes. The **labels it filters on are not**: `visibility.py` resolves a named `persona` to an ordered-tier `Clearance`, and `labels.py` stamps synthetic `visibility` tiers from a hand-authored `labels.yaml`. The module docstrings, `security.md`'s slice-4 boundary table, and charter principle 5 all already call this "a teaching stand-in for ACLs, not real authz."

Two forces make the boundary worth recording as a decision rather than leaving implicit:

1. **The labels look promotable.** It is mechanically small to rename tiers to `allowed_groups`, accept a caller-supplied group list, and call it multi-tenant authz. That change would *look* like enforceable data authz while leaving the trust model untouched — the security context would be spoofable, the filter theater. The seam's own `security.md` note warns against copying it "into a context where the persona is the security principal."
2. **The two layers get conflated.** The repo has genuinely production-grade *infrastructure* authn/authz — the IAM-auth, scoped-principal Function URL (SigV4), least-privilege task/Lambda roles, the ADR-0004 read-only Neptune grant, the no-NAT VPC. "We don't do real authz" must not be read as "the security is fake." The infrastructure controls are real and are *tightened*, not stubbed (see `security-hardening-followups`).

Constraints in force: the charter's teaching-over-production tie-breaker (principle 7); the cost/teardown posture (principle 4) that rules out standing multi-tenant infrastructure; and the corpus being **public Kubernetes docs**, so there is no private data whose exposure the synthetic labels would actually be guarding.

## Decision

**We keep data-level access control a synthetic teaching stand-in and do not implement real authorization in this template.**

Specifically:

- The data-authz layer is `visibility.py` (ordered tiers + persona `Clearance`, including the `security-hardening-followups` opt-in **default-deny** mode that *demonstrates* the fail-open→fail-closed inversion a real ACL requires) and `labels.py` (synthetic label assignment). Every such construct is labeled, in code and docs, a teaching stand-in (principle 5).
- **Real authorization is out of scope**: authenticated-principal→groups resolution (Cognito/OIDC/IAM, not a client-supplied persona), default-deny *by construction*, per-tenant index/row isolation, and an authz audit trail. Adding any of these makes the persona the security principal — a mission/scope change that goes through an **RFC** (and is most likely a fork), never a quiet PR.
- The boundary is **one-directional**: hardening the *real* infrastructure controls (egress least-privilege, SCA gates, IAM scoping) is always in scope and needs no RFC; promoting the *synthetic* data labels to real authz always does.

## Decision drivers

- **Teaching posture wins ties (principle 7).** The deliverable is comprehension + reproduction of the pattern, not a production authz system.
- **Honest labeling (principle 5).** A synthetic construct dressed as real is the one thing the charter forbids outright.
- **Trust model, not mechanism, is the gap.** Query-time filtering is necessary but not sufficient; without a trusted principal→groups source it is spoofable — so "make the filter multi-valued" does not get you authz.
- **Cost/teardown (principle 4).** Real multi-tenant isolation implies standing infrastructure that breaks the ephemeral, scale-to-zero posture.

## Consequences

**Positive:**

- The pattern stays narratable and reproducible on any corpus without an identity provider or tenant model.
- The real/synthetic split is explicit, so infrastructure hardening proceeds freely while the data-authz line is protected from accidental erosion.
- The "what real looks like" delta becomes a *teaching asset* (the companion guide enumerates each missing control) rather than an undocumented hole.

**Negative:**

- The template cannot be deployed against private data or a real end-user principal as-is; an adopter who needs that must cross the boundary themselves.
- Two named residuals persist, safe **only** behind the trusted-caller (IAM-auth, scoped-principal) ingress: the **fail-open default** at the query layer (`clearance=None` ⇒ unrestricted — the textbook default a real ACL inverts), and the **enumeration-oracle** filtered-out trace that a real ACL would never reveal. Both are documented as such in `security.md`.
- Readers may mistake "synthetic data authz" for "no real security" — mitigated only by the repeated labeling this ADR mandates.

**Revisit if:** the mission shifts from a clone-and-learn reference to a deployable multi-tenant product, or the template is asked to ingest private/customer data — either is an RFC-level scope change (see `CHARTER.md` § When to revise), likely a fork.

## Confirmation

- **Mode:** reviewer-checked
- **Signal:** every visibility/clearance construct (code + docs) carries the "synthetic teaching stand-in, not real authz" label, and no change wires an authenticated principal→groups source, per-tenant isolation, or an authz audit trail into the data-filter path without a preceding RFC. The `security-reviewer` secure-design pass flags any PR that crosses the line.
- **Owner:** maintainer (eugenelim) + the `security-reviewer` on security-boundary diffs.

## Alternatives considered

- **Implement real ACLs now (`allowed_groups` + authenticated principal→groups + per-tenant isolation).** Rejected against *teaching posture wins* and *cost/teardown*: it is a different product (multi-tenant authz service), needs an identity provider and standing isolation infrastructure, and the public corpus gives it nothing real to guard. Belongs behind an RFC / in a fork.
- **Rename tiers to `allowed_groups` and accept a caller-supplied group list (the "looks real" middle ground).** Rejected against *honest labeling* and *trust model is the gap*: it dresses the synthetic stand-in as authz while the security context stays spoofable — exactly the principle-5 violation the charter forbids.
- **Drop the synthetic labels entirely (no permission-filtered retrieval demo).** Rejected: pattern 7 ("where does authorization ride your retrieval path?") is a first-class part of the deliverable; removing it would leave the enterprise concern that blocks real RAG undemonstrated.

## References

- `docs/CHARTER.md` — principle 5 (synthetic stays labeled), principle 7 (teaching posture wins), Scope "does not: Production authorization".
- `docs/architecture/security.md` — slice-4 trust-boundary table (the fail-open default + enumeration-oracle residuals and the trusted-caller containment).
- spec `docs/specs/security-hardening-followups/` — the real-control hardening (A1/A2/A3) and the B5 default-deny demonstration this ADR's boundary governs.
- the "from synthetic visibility labels to real ACLs" guide — enumerates each control a real system inserts at the boundary this ADR records.
