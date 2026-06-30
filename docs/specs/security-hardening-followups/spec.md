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
  more. Asserted per-SG as a set-equality; the prior
  `test_query_lambda_sg_allows_outbound` allow-all guard is replaced by this
  closed-egress assertion. (The table is the source of truth; if AC9's live run
  reveals a missing target, the table and the assertion are corrected together.)
- [ ] **AC3** `pip-audit` runs in CI over the locked dependency set and the job
  fails on a known vulnerability; accepted exceptions live in a committed ignore
  file, each with a one-line reason.
- [ ] **AC4** `cdk-nag` runs at synth time as a **hard gate**: an unsuppressed
  finding fails `cdk synth` / CI. Verified by a deliberate temporary violation
  that fails the build. Every `NagSuppressions` entry carries a non-empty
  `reason` that cites its sign-off (the approving PR/issue) — the reviewed
  sign-off itself is the Ask-first boundary below.
- [ ] **AC5** `.github/dependabot.yml` covers the `pip` and `github-actions`
  ecosystems and validates.
- [ ] **AC6** `.github/workflows/ci.yml` runs, on push and PR, the full gate set
  with pinned commands — `ruff check` + `ruff format --check`, `mypy`, `pytest`,
  `pip-audit`, and `cdk synth` (with `cdk-nag`) — and a green run is the
  documented merge gate; the same commands replace the unfilled `<…>` template in
  `AGENTS.md` § Commands so the workflow and the doc agree. This workflow is the
  CI surface the deferred `infra-secret-scan-ci` backlog item was blocked on;
  adding the gitleaks/`shellcheck` jobs to it stays **out of scope** here and
  remains that item's open follow-on (now unblocked).
- [ ] **AC7** An opt-in default-deny clearance mode exists and is **observable**,
  not just unit-asserted, with this exact input→outcome contract (unit-tested in
  `tests/test_visibility.py`, the resolver in `visibility.py`):
  - default-deny ON, **no principal** (`None`/absent or empty string `""`) ⇒ the
    empty `Clearance` (`allowed=frozenset()`, sees nothing) — the fail-closed
    inversion;
  - default-deny ON, **unrecognized non-empty** persona ⇒ still raises
    `ValueError` (the existing fail-closed raise, unchanged);
  - default-deny ON, **known** persona ⇒ that persona's normal `Clearance`.
  The mode is wired through the CLI (`cli.py:_clearance`, e.g. a `--default-deny`
  flag) so "no principal ⇒ sees nothing" is demonstrable at the command line. It
  is **additive and opt-in**: default-deny OFF keeps `clearance=None` ⇒
  unrestricted for every shipped mode, byte-identical. The query layer is
  unchanged — an empty `Clearance` already means "sees nothing" today.
- [ ] **AC8** The default-deny mode is documented in-code and in `security.md`
  as still a synthetic teaching stand-in (charter principle 5), demonstrating the
  fail-open→fail-closed inversion named in `security.md` (slice-4 boundary table)
  — explicitly *not* real authz.
- [ ] **AC9** (live) Deployed on a clean account, the tightened SGs pass live:
  ingest, hybrid Function-URL query, and both smoke probes succeed with no silent
  egress block; the deployed SG-egress + IAM posture is captured into
  `security.md`; then `cdk destroy` removes every billable resource (Budgets held
  at 150). (run-or-defer per live-deploy availability; deferred:
  security-hardening-followups-live-eval if unavailable)

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
