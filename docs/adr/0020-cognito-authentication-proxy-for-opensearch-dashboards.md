# ADR-0020: Cognito is the authentication proxy for OpenSearch Dashboards

- **Status:** Accepted
- **Date:** 2026-09-11
- **Decision-makers:** eugenelim
- **Supersedes:** none
- **Related:** ADR-0018 (managed OpenSearch domain over AOSS); ADR-0002 (private VPC
  topology — the reason this trade-off is uncomfortable)

## Context

The `graphrag-vectors` domain runs with fine-grained access control and an IAM master
identity, is VPC-resident with no public endpoint, enforces HTTPS at TLS 1.2, and
encrypts at rest and node-to-node. Its `CognitoOptions`, however, were disabled.

Enterprise cloud-security baselines commonly require that OpenSearch Dashboards sit
behind a Cognito authentication proxy, and such controls typically assert on the domain's
`CognitoOptions` field specifically. FGAC does not satisfy that assertion even though it
is the stronger control — the check is looking for Cognito, not for "some
authentication".

That leaves a genuine choice rather than an obvious fix, because the domain has no public
endpoint today and a Cognito user-pool hosted UI is internet-facing by construction.

## Decision

> We will enable Cognito as the Dashboards authentication proxy: a user pool
> (administrator-invite only, MFA required, 14-character password floor, advanced
> security enforced), an identity pool that refuses unauthenticated identities, and two
> scoped roles — one assumed by the OpenSearch service, one by the authenticated human.
> Cognito and FGAC stack: Cognito authenticates at the door, FGAC authorizes every
> request after it.

Workload access is untouched. All four callers (ingestion, vector-probe, query,
mcp-lambda) use SigV4 IAM auth, which Cognito does not mediate.

## Decision drivers

- **A control that is implemented does not expire.** A risk exception has to be
  re-justified every cycle, and someone has to remember to do it.
- **There is no human path to Dashboards today.** Debugging index mappings, shard health,
  or an empty query result is materially harder without one, and that need is real
  independent of any control.
- **Enterprise identity.** A user pool can federate to a corporate IdP, which gets
  centrally enforced MFA and automatic offboarding — better than the single IAM master
  role, where access is opaque and non-attributable.
- **Per-user attribution.** With audit logging, actions become traceable to a person.

## Consequences

**Positive:**
- Dashboards gains an authenticated human path that did not exist before.
- Access becomes attributable per user rather than pooled behind one master role.
- Federation to a corporate IdP is now a configuration change, not a redesign.

**Negative:**
- **This adds a public authentication surface to a domain that had none.** The Cognito
  hosted-UI domain is reachable from the internet even though the search domain is not.
  That is a real increase in attack surface and the main argument against this decision;
  it was accepted knowingly rather than overlooked.
- More moving parts to own: user pool, pool domain, identity pool, two roles, and the
  service-linked role — plus ongoing user lifecycle (provisioning, MFA resets,
  offboarding).
- Reaching Dashboards still needs VPC connectivity (VPN or bastion) *in addition to*
  Cognito login, because the domain endpoint itself remains private. Cognito supplies
  identity, not a network path — a common source of confusion when this is first used.

**Neutral / to revisit:**
- If the control that motivated this is ever satisfied by FGAC directly, the Cognito
  layer becomes optional and could be removed to shrink the public surface.
- Domain audit logging is not enabled. It is what makes per-user attribution actually
  observable, and should follow.

## Confirmation

- Plan assertions pin the shape: `cognito_options.enabled` true, no unauthenticated
  identities, MFA `ON` with a 14-character floor, both role trusts scoped (identity-pool
  `aud` plus authenticated-only `amr` on one, `SourceAccount`/`SourceArn` on the other),
  and no wildcard resource on the Dashboards policy.
- Enabling Cognito is a domain configuration update; the FGAC backend-role mappings
  described in `opensearch.tf` must still resolve afterwards or all four workload callers
  get 403.

## Alternatives considered

- **File a risk exception instead.** The domain is VPC-only with FGAC, IAM master, TLS
  1.2 and a two-principal resource policy — a stronger posture than a Cognito-fronted
  public domain. *Rejected:* exceptions expire and must be re-argued, and the human
  Dashboards path was wanted regardless. This was the recommended option and was
  deliberately overridden on effort-over-time grounds.
- **Native OpenSearch SAML for Dashboards, no Cognito.** Fewer AWS moving parts and no
  hosted UI. *Rejected:* controls of this kind assert on `CognitoOptions`, so SAML would
  leave the finding open while still adding an identity integration.
- **Leave Dashboards with no human path at all.** Zero new surface. *Rejected:* it leaves
  the control unsatisfied and the debugging gap unaddressed.

## References

- `apps/infra-tf/cognito.tf` — the implementation and its inline trade-off note
- `docs/architecture/security.md` — resulting posture
