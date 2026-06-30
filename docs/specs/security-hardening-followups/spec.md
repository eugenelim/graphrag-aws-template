# Spec: security-hardening-followups

- **Status:** Draft <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** charter (principle 5, principle 7, principle 4; Scope "Production authorization" non-goal), ADR-0009, ADR-0002, ADR-0004, ADR-0003
- **Brief:** none
- **Contract:** none

> **Spec contract:** this document defines what "done" means. The implementing
> PR must match this spec, or update it. Verification must be derivable from it.

## Objective

An architect cloning this template gets a tighter, CI-gated security baseline,
and contributors get the supply-chain gates the charter named as standing
follow-ups. Three real, non-synthetic controls land together — uniform
least-privilege egress on every in-VPC compute security group; supply-chain
scanning (`pip-audit` + Dependabot) and a hard `cdk-nag` synth gate wired into
GitHub Actions; and a live deployed-config review that confirms the tightened
egress does not break the live ingest/query/smoke paths. Alongside them, the
synthetic visibility-label stand-in gains an **opt-in default-deny clearance
mode** so the demo can *show* the fail-open→fail-closed inversion a real ACL
requires — still labeled a teaching stand-in, never promoted to production
authorization. Success: every compute SG denies egress except to the specific
in-VPC stores and VPC endpoints it calls; CI fails on a known dependency vuln or
an unsuppressed infrastructure finding; and the default-deny posture is
demonstrable in code without changing any shipped mode's behavior.

## Boundaries

The three-tier guard that keeps an implementing agent inside the lines.
*Always do* applies without asking; *Ask first* requires human sign-off
before proceeding; *Never do* is a hard rule, even under time pressure.

### Always do

- Keep `ruff S` (flake8-bandit) on and every value parameterized — the existing
  injection-safe posture is preserved, not relaxed, by this work.
- Preserve the no-NAT, VPC-endpoint-only egress topology (ADR-0002): explicit
  egress rules point only at in-VPC stores (Neptune 8182 / OpenSearch 443) and
  the VPC interface/gateway endpoints (443), never at `0.0.0.0/0`.
- Keep teardown-first: any new tooling adds **no standing billable AWS
  resource**; the Budgets threshold stays at 150 (charter principle 4).
- Label the default-deny clearance mode, in code and docs, as still a *synthetic
  teaching stand-in* for an ACL (charter principle 5).

### Ask first

- Adding any new **runtime** dependency (dev/CI-only tooling like `pip-audit` /
  `cdk-nag` is fine; a new `[project.dependencies]` entry needs sign-off).
- Changing the existing fail-open `clearance=None` ⇒ unrestricted behavior of
  any **shipped** mode — the default-deny mode must be additive/opt-in; altering
  current slice-4 semantics needs sign-off.
- Each `cdk-nag` suppression — every accepted-residual suppression carries a
  human-signed reason (a suppression is a posture exception, not a default).

### Never do

- Never promote the synthetic visibility labels into real authorization,
  multi-tenancy, or data ACLs (charter Scope "Production authorization"
  non-goal). That is a fork-level scope change behind an RFC, out of bounds here.
- Never add a NAT gateway or any public egress path to satisfy an egress rule.
- Never introduce a new module boundary or top-level dependency for the SG/egress
  change — CDK `aws_ec2` primitives only (structural bound).
- Never weaken an existing control to make a gate pass (no relaxing the
  read-only Neptune grant per ADR-0004, no opening an SG ingress, no broadening
  an IAM `Resource`).

## Testing Strategy

- **A1 — SG egress least-privilege:** goal-based, exercised by CDK-synth
  assertions in `apps/infra/tests/test_stack.py` (each compute SG renders
  `allow_all_outbound=False`; no compute SG renders a `0.0.0.0/0` / protocol
  `-1` egress rule; each renders the explicit egress its live calls need). The
  existing `test_query_lambda_sg_allows_outbound` guard is *replaced* by the
  closed-egress assertion. The "does it still work live" half is A3.
- **A2 — supply-chain gates:** goal-based. `pip-audit` exits non-zero on a known
  vuln (documented exceptions in an ignore file with a reason); `cdk-nag` fails
  the synth/CI build on an unsuppressed finding — proven by a deliberate
  temporary violation; the Dependabot + workflow YAML validate.
- **B5 — default-deny clearance:** TDD. A strict resolution path returns the
  empty clearance (sees nothing) for an absent/empty principal, vs the existing
  fail-open `None`⇒unrestricted; the inversion is unit-tested in
  `tests/test_visibility.py` and the CLI wiring goal-checked. An invariant
  compressible to a test. (AC7)
- **AC8 — synthetic-stand-in labeling:** goal-based. A check that the
  `visibility.py`/`_clearance` docstrings and `security.md` label the mode a
  synthetic teaching stand-in (the T5 doc assertion); no logic to unit-test.
- **A3 — live IAM/SG evaluation:** manual QA / live-AC. Deploy on a clean
  account; confirm live ingest + hybrid Function-URL query + both smoke probes
  succeed under the tightened SGs (no silent egress block); capture the deployed
  SG-egress + IAM posture into `security.md`; then `cdk destroy`.

## Acceptance Criteria

- [ ] **AC1** Every in-VPC compute SG — `IngestionSg`, `SmokeSg`,
  `VectorSmokeSg`, `QuerySg` — renders `allow_all_outbound=False`; no compute SG
  renders a `0.0.0.0/0` protocol `-1` egress rule (synth assertion in
  `test_stack.py`).
- [ ] **AC2** Each compute SG's rendered egress **equals** (not merely contains)
  the exact peer/port set defined for it in the per-SG egress table in `plan.md`
  § Design decisions — Neptune `8182`, OpenSearch `443`, and the
  interface/gateway VPC endpoints `443` as that component requires, and nothing
  more. **Normalization (so set-equality is mechanically derivable against the
  synthesized template):** CDK renders a compute SG's egress in two shapes — the
  S3 prefix-list rule inline in the SG's `SecurityGroupEgress` array
  (`{DestinationPrefixListId: {Ref: S3PrefixListId}}`), and each SG-to-SG rule as
  a standalone `AWS::EC2::SecurityGroupEgress` resource keyed by `GroupId` Ref to
  the compute SG, carrying a `DestinationSecurityGroupId` Ref/GetAtt to the peer
  SG's logical id. The assertion, per compute SG: gather both shapes, resolve each
  `DestinationSecurityGroupId` Ref to its peer logical id and **classify** it
  against the known peer set (the Neptune SG, the OpenSearch SG, and the five
  named interface-endpoint SGs — matched by the endpoint name in the logical id),
  resolve the prefix-list rule to the `S3PrefixListId` parameter, then assert the
  resulting `{peer, FromPort}` set equals the table — exactly, nothing extra,
  nothing missing. The prior `test_query_lambda_sg_allows_outbound` allow-all
  guard is replaced by this closed-egress assertion. The table is the source of
  truth and its *completeness* is **provisional until AC9** (a closed-egress
  synth assertion cannot see a missing-but-needed target — that surfaces only as
  the documented silent live hang); if AC9 reveals a missing target, the table
  and the assertion are corrected together.
- [ ] **AC2b** The `S3PrefixListId` `CfnParameter` (the one parameter-derived
  egress target the closed-egress posture rests on) carries
  `allowed_pattern=r"^pl-[0-9a-f]+$"` so a CIDR / free-form / over-broad value is
  rejected at the CloudFormation boundary, and `apps/infra/scripts/deploy.sh`
  resolves it per-region from `describe-managed-prefix-lists` filtered to the
  AWS-managed `com.amazonaws.<region>.s3` list (the module default
  `pl-63a5400a` is us-east-1's and is a synth/us-east-1 convenience only, not the
  regional source of truth). Goal-checked: the param has the pattern; `deploy.sh`
  passes `S3PrefixListId`.
- [ ] **AC3** `pip-audit` runs in CI over the locked dependency set and the job
  fails on a known vulnerability; accepted exceptions live in a committed ignore
  file (`.pip-audit-ignore`, consumed by the CI command), each entry carrying a
  one-line reason **and** a review-by date or tracking issue (a suppression
  without an expiry rots — it silently masks a CVE after an upstream fix ships).
  The file may start empty-but-headered if the tree has no known vuln.
- [ ] **AC4** `cdk-nag` runs at synth time as a **hard gate**: an unsuppressed
  finding fails `cdk synth` / CI. Verified two ways: (a) a **durable, committed**
  synth assertion in `test_stack.py` applies `AwsSolutionsChecks` and asserts the
  stack carries **no unsuppressed `AwsSolutions-*` error annotation** — this fails
  offline if the aspect is later dropped from `app.py` or a violating resource is
  added, so the gate has an artifact that regresses, not just a one-time check;
  and (b) a deliberate temporary violation that fails the build (proven once, then
  removed). Every `NagSuppressions` entry carries a non-empty `reason` that cites
  its sign-off (the approving PR/issue) — the reviewed sign-off itself is the
  Ask-first boundary below.
- [ ] **AC5** `.github/dependabot.yml` covers the `pip` and `github-actions`
  ecosystems and validates.
- [ ] **AC6** `.github/workflows/ci.yml` runs, on push and PR, the full gate set
  with pinned commands — `ruff check packages apps` + `ruff format --check
  packages apps` (scoped to the project's own Python, matching `[tool.ruff].src`;
  `.claude/` bundled agent assets and `tools/`/`scripts/` dev tooling are
  deliberately out of the strict gate), `mypy`, `pytest`, `pip-audit`, and `cdk
  synth` (with `cdk-nag`) — and a green run is the documented merge gate; the same
  commands replace the unfilled `<…>` template in `AGENTS.md` § Commands so the
  workflow and the doc agree. **Toolchain pinned:** `ruff`/`mypy` are pinned to
  exact versions (a floating `ruff>=0.5` is itself a non-deterministic gate);
  because the repo has never had a CI surface, a green `ruff format --check`
  requires a **one-time `ruff format` of `packages apps` under the pin**
  (pre-existing drift, ~18 files) — a declared precondition for this gate to
  exist, landed as its own labeled commit so the security diff stays legible.
- [ ] **AC6b** The workflow hardens its own (net-new) trust surface: a top-level
  least-privilege `permissions: contents: read` (elevated per-job only where a
  step demonstrably needs it); it triggers on `pull_request` (never
  `pull_request_target`, which would run untrusted-PR code with a write-scoped
  token); and **every `uses:` action is pinned to a full 40-char commit SHA**
  (with a `# vX.Y` comment), not a mutable tag — Dependabot's `github-actions`
  ecosystem (AC5) keeps the SHAs current. This workflow is the CI surface the
  deferred `infra-secret-scan-ci` backlog item was blocked on; adding the
  gitleaks/`shellcheck` jobs to it stays **out of scope** here and remains that
  item's open follow-on (now unblocked).
- [ ] **AC7** An opt-in default-deny clearance mode exists and is **observable**,
  not just unit-asserted, with this exact input→outcome contract (unit-tested in
  `tests/test_visibility.py`, the resolver in `visibility.py`):
  - default-deny ON, **no principal** (`None`/absent or empty string `""`) ⇒ the
    empty `Clearance` — exactly `Clearance(persona="default-deny",
    allowed=frozenset())` (the `persona` field is required and default-less, so
    the sentinel value is pinned; it also makes `_print_persona` render a legible
    `persona: default-deny  clearance allows: []` banner) — sees nothing, the
    fail-closed inversion;
  - default-deny ON, **unrecognized non-empty** persona ⇒ still raises
    `ValueError` (the existing fail-closed raise, unchanged);
  - default-deny ON, **known** persona ⇒ that persona's normal `Clearance`.
  **Flag precedence (so the inversion is unambiguous):** `--default-deny` governs
  **only the absent-principal cell** — it must not short-circuit before persona
  resolution. When a `--persona` is present the three persona rows above hold
  *regardless* of the flag (unknown ⇒ raise, known ⇒ normal clearance); the flag
  changes behavior only when no persona is given. The mode is wired through the
  CLI (`cli.py:_clearance`, a `--default-deny` store-true flag) so "no principal
  ⇒ sees nothing" is demonstrable at the command line (a goal-checked CLI run). It
  is **additive and opt-in**: default-deny OFF keeps `clearance=None` ⇒
  unrestricted for every shipped mode, byte-identical. The query layer is
  unchanged — an empty `Clearance` already means "sees nothing" today.
- [ ] **AC8** The default-deny mode is documented as still a synthetic teaching
  stand-in (charter principle 5), demonstrating the fail-open→fail-closed
  inversion named in `security.md` (slice-4 boundary table) — explicitly *not*
  real authz — in **all three** surfaces a reader meets it: the `visibility.py`
  resolver + `_clearance` docstrings, `security.md`, **and the `--default-deny`
  CLI `--help` string itself** (the most likely place a copy-paster first meets
  the flag).
- [ ] **AC9** (live) Deployed on a clean account, the tightened SGs pass live:
  ingest, hybrid Function-URL query, and both smoke probes succeed with no silent
  egress block; the deployed SG-egress + IAM posture is captured into
  `security.md`; then `cdk destroy` removes every billable resource (Budgets held
  at 150). (run-or-defer per live-deploy availability; deferred:
  security-hardening-followups-live-eval if unavailable) **Terminal status:** T5
  sets the spec `Status:` to `Shipped` **iff** AC9 runs and passes; if AC9 is
  deferred (live deploy unavailable), `Status:` is `Implementing` and AC9 carries
  `(deferred: security-hardening-followups-live-eval)`. Live deploy is available
  in this environment (Assumptions), so the expected path is `Shipped`.

## Assumptions

- Technical: no CI exists today — no `.github/workflows/` and no
  `.github/dependabot.yml` (source: repo probe, both absent), so A2 stands up CI
  from scratch.
- Technical: SCA tooling is absent — only `ruff S`/flake8-bandit lint rules are
  present, no `cdk-nag` / `pip-audit` / standalone `bandit` dependency (source:
  `pyproject.toml:59` `select = [..., "S"]` + grep).
- Technical: the four compute SGs default to `allow_all_outbound=True` —
  `IngestionSg` (`graphrag_stack.py:399`), `SmokeSg` (`:415`), `VectorSmokeSg`
  (`:555`), `QuerySg` (`:602`); only the Neptune (`:296`) and OpenSearch (`:508`)
  SGs already set `False`.
- Technical: A1 must add explicit egress and *invert* the
  `test_query_lambda_sg_allows_outbound` guard (`test_stack.py:432`) — a bare
  `allow_all_outbound=False` flip regresses (a prior live-deploy finding: the
  first Bedrock call is silently blocked and the function hangs to its 120s
  timeout — documented at `graphrag_stack.py:593-599`).
- Technical: interface VPC endpoints are created in a loop via
  `vpc.add_interface_endpoint` (`graphrag_stack.py:266`) and S3 via a gateway
  endpoint (`:264`); explicit egress targets these endpoints' SGs / the S3
  prefix list on 443 (source: `graphrag_stack.py:58-65,247-266`).
- Process: live deploy is available in this environment, so AC9 runs rather than
  defers (source: user confirmation 2026-06-30; memory live-deploy-available).
- Process: no RFC is required — A1/A2/A3 are follow-ups already named in
  `docs/architecture/security.md` (the "Out of scope this slice" section, lines
  205/209/213), not charter or convention edits (source: `security.md:192-219`).
- Product: A2's CI platform is GitHub Actions (source: user confirmation
  2026-06-30; repo is on GitHub, PRs via `gh`).
- Process: `cdk-nag` is a **hard** synth-time gate that fails the build, not
  advisory (source: user confirmation 2026-06-30).
- Product: B5 ships as **code** — an opt-in default-deny clearance mode in
  `visibility.py`, not doc-only (source: user confirmation 2026-06-30).
- Process: AC9's live-eval shape is deploy → capture findings into `security.md`
  → teardown, incurring deploy cost now, and is acceptable (source: user
  confirmation 2026-06-30).
- Process: A2's CI workflow is the surface the deferred `infra-secret-scan-ci`
  item was blocked on; this spec **unblocks** it but does **not** ship its
  gitleaks/`shellcheck` jobs (they stay that item's scope), so the two specs'
  boundaries don't collide (source: `docs/backlog.md:126-136`).
- Technical: the gate commands are `ruff check` / `ruff format --check`, `mypy`,
  `pytest` (no command pinned in `AGENTS.md` § Commands yet — it's the unfilled
  template), `pip-audit`, and `cdk synth`; this spec pins them in the workflow
  and fills the `AGENTS.md` block in the same PR (source: `pyproject.toml:32-34`
  ruff/mypy/pytest deps; `AGENTS.md:128-138` unfilled template).
