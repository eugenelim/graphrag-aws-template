# Plan: security-hardening-followups

- **Spec:** [`spec.md`](spec.md)
- **Status:** Drafting <!-- Drafting | Executing | Done -->

> **Plan contract:** this is the implementation strategy. Unlike the spec, this
> document is allowed to change as you learn. When it changes substantially
> (a different approach, not just a re-ordering), note why in the changelog
> at the bottom.

## Approach

Four independent strands, deliberately decoupled so the riskiest one (A1) can't
hold up the cheap ones. **A1** (SG egress) is the load-bearing change and the
only one with a live failure mode: closing `allow_all_outbound` on a compute SG
*without* adding back the exact egress it needs silently breaks the live path
(the documented Bedrock-hang regression). So A1 is done as "close + add explicit
egress per SG, derived from each component's real call set," with CDK-synth
assertions standing in for live behavior until AC9. **A2** splits into config
(`pip-audit` + Dependabot + the CI workflow, T3) and the `cdk-nag` synth gate
(T4) — T4 trails T1 because nag should run against the *tightened* stack and may
itself surface findings to suppress-with-reason. **B5** (T2) is a small,
self-contained, additive `visibility.py` change with a pure unit test, no infra.
Docs (T5) consolidate the security.md posture update. A3 (T6) is the live gate.

Riskiest part: the per-SG egress target set. Each compute SG talks to a
*different* subset of {Neptune 8182, OpenSearch 443, Bedrock/ECR/Logs/STS
interface endpoints 443, S3 gateway endpoint 443}; over-tightening one breaks a
live call that synth can't catch, which is exactly why AC9 is mandatory.

## Constraints

- **ADR-0002** (ephemeral, VPC-resident, no-NAT topology) — egress rules must
  preserve "no internet path"; they target VPC endpoints + in-VPC stores only.
- **ADR-0004** (read-only Neptune grant on the query Lambda) — must not be
  weakened to make any gate pass; `cdk-nag` suppressions must not paper over it.
- **ADR-0003** (CDK Python as the IaC tool) — the SG change uses `aws_ec2`
  primitives; no new IaC mechanism.
- **ADR-0009** (access-control depth — synthetic labels over real authz) — B5's
  default-deny mode stays a labeled stand-in; the boundary this ADR records is
  the one B5 must not cross.
- **charter** principle 5 (synthetic stays labeled), principle 7 (teaching
  posture wins; production concern named as non-goal), principle 4 (teardown),
  Scope "Production authorization" non-goal — B5 stays a stand-in.
- `docs/architecture/security.md` "Out of scope this slice" — A1/A2/A3 are the
  named follow-ups this spec discharges; update that section as they land.

## Construction tests

Most construction tests live per-task below. Cross-cutting:

**Integration tests:** none beyond per-task synth assertions and the live AC9.
**Manual verification:** AC9 — the live deploy/ingest/query/smoke/teardown run,
captured in `security.md`.

## Design (LLD)

Shape: **mixed** (infra CDK + CI config + library logic + docs + live verify).
Stack: AWS CDK (Python) over `aws_ec2`/`aws_opensearchservice`/`aws_neptune`
(detected from `apps/infra/stacks/graphrag_stack.py`); `cdk-nag` as the synth
aspect; `pip-audit` + GitHub Actions + Dependabot for supply chain; the
`graphrag` library's pure `visibility.py` for B5.

### Design decisions

- **Egress via CDK `connections`/`add_egress_rule`, not hand-built CIDR rules** —
  set `allow_all_outbound=False` on each compute SG, then express each needed
  path with CDK-modelled peers. **Refined during grounding (2026-06-30):**
  - *Interface endpoints* (Bedrock/ECR-api/ECR-dkr/Logs/STS): captured from the
    `add_interface_endpoint` loop into `self._interface_endpoints`, wired with
    `compute_sg.connections.allow_to(endpoint, ec2.Port.tcp(443))` —
    `InterfaceVpcEndpoint` is `IConnectable` (verified against the installed
    aws-cdk-lib).
  - *Store SGs* (Neptune 8182 / OpenSearch 443): the **ingress** rule already
    exists on the store SG (`neptune_sg.add_ingress_rule(compute_sg, …)`) and its
    description is asserted by existing tests — so add the **egress-only** rule on
    the compute SG via `compute_sg.add_egress_rule(<store_sg>, port, desc)`
    (peer is the store SG, an `IPeer`). Using `connections.allow_to` here would
    add a *duplicate* ingress rule and break the description-pinned ingress tests.
  - *S3 gateway endpoint* (corpus read, `IngestionSg` only): a **gateway**
    endpoint exposes **no** `.connections` / prefix-list handle (verified), and
    `PrefixList.from_lookup` needs a live account/region context that the offline
    `Template.from_stack` tests don't have. So the S3 egress targets the
    AWS-managed S3 prefix list via `ec2.Peer.prefix_list(<id>)`, where `<id>`
    comes from a new `CfnParameter` `S3PrefixListId` (default the us-east-1
    `pl-63a5400a`, verified live; `deploy.sh` fills it per-region from
    `describe-managed-prefix-lists`). This stays inside the `aws_ec2`-only
    structural bound (no custom resource), synths offline (renders as a `Ref`
    token), and AC9 confirms the live S3 read works. Traces to: AC1, AC2, AC9.
- **Per-SG egress is the minimal real set, not a uniform block** — this table is
  the **source of truth** for AC2's set-equality assertion (corrected with the
  assertion together if AC9 reveals a missing target). Over-broad egress defeats
  the point; under-broad breaks live (the silent-hang regression, AC9):

  | Compute SG | Egress targets (peer · port) |
  | --- | --- |
  | `IngestionSg` (Fargate ingest) | Neptune SG `8182`; OpenSearch SG `443`; Bedrock-runtime EP `443`; ECR-api EP `443`; ECR-dkr EP `443`; CloudWatch-Logs EP `443`; STS EP `443`; S3 gateway EP (prefix list) `443` |
  | `SmokeSg` (Neptune smoke Lambda) | Neptune SG `8182`; CloudWatch-Logs EP `443`; STS EP `443` |
  | `VectorSmokeSg` (OpenSearch+Bedrock smoke) | OpenSearch SG `443`; Bedrock-runtime EP `443`; CloudWatch-Logs EP `443`; STS EP `443` |
  | `QuerySg` (query Lambda) | Neptune SG `8182`; OpenSearch SG `443`; Bedrock-runtime EP `443`; CloudWatch-Logs EP `443`; STS EP `443` |

  (The interface-endpoint set is `BedrockRuntime`/`EcrApi`/`EcrDocker`/
  `CloudWatchLogs`/`Sts` from `graphrag_stack.py:58-65`; S3 is the gateway
  endpoint at `:264`.) Traces to: AC2, AC9.
- **B5 = a strict resolution entry point + a CLI flag, not a changed default** —
  add a `resolve_clearance(persona, *, default_deny=...)` path (or sibling
  resolver) and wire it through `cli.py:_clearance` behind a `--default-deny`
  flag, so the inversion is *observable* (no principal ⇒ sees nothing), not a
  unit-only invariant. Exact contract: `default_deny` ON + no principal
  (`None`/`""`) ⇒ `Clearance(allowed=frozenset())`; ON + unrecognized non-empty
  persona ⇒ `ValueError` (unchanged); ON + known persona ⇒ normal clearance; OFF
  ⇒ today's `_clearance` (no persona ⇒ `None` ⇒ unrestricted), byte-identical.
  The query layer is untouched — an empty `Clearance` already means "sees
  nothing." Traces to: AC7, AC8.
- **cdk-nag as a hard gate** — apply `AwsSolutionsChecks` as an `Aspects` add in
  `app.py`; CI runs `cdk synth` and fails on any error annotation. Suppressions
  via `NagSuppressions` with a `reason` string, reviewed. Traces to: AC4, AC6.

### Data & schema

No persisted-data or store-schema change. B5 touches only the in-memory
`Clearance` value object (`visibility.py`); no label format change, so no index
remap or re-ingest. Traces to: AC7.

### Failure, edge cases & resilience

- A1's failure mode is *silent* (a blocked egress hangs to timeout, not an
  error) — caught only live, hence AC9 is non-optional and T1's synth assertions
  are necessary-but-insufficient. The S3 gateway-endpoint egress (prefix list) is
  the easiest to miss for `IngestionSg` (corpus read).
- B5 edge: empty-string persona vs `None` persona vs unknown persona — strict
  mode must distinguish "no principal ⇒ deny" from "unknown principal ⇒ raise"
  (the existing fail-closed raise stays). Traces to: AC7.

## Tasks

### T1: Compute SGs deny egress except their explicit call set

**Depends on:** none
**Touches:** apps/infra/stacks/graphrag_stack.py, apps/infra/tests/test_stack.py, apps/infra/scripts/deploy.sh (S3PrefixListId param resolved per-region), apps/infra/scripts/config.env (doc the new param)

**Tests:**
- Synth assertion: each of `IngestionSg`/`SmokeSg`/`VectorSmokeSg`/`QuerySg`
  renders `allow_all_outbound=False` and **no** `0.0.0.0/0` protocol `-1` egress
  rule (AC1).
- Synth assertion per SG: set-equality of the resolved `{peer, port}` egress set
  against the table, per AC2's normalization (resolve `DestinationSecurityGroupId`
  Refs to peer logical ids + classify; resolve the inline prefix-list rule to
  `S3PrefixListId`); `test_query_lambda_sg_allows_outbound` is replaced by this
  closed-egress assertion (AC2).
- Synth assertion: the `S3PrefixListId` CfnParameter carries
  `allowed_pattern=^pl-[0-9a-f]+$` (AC2b); goal-check: `deploy.sh` passes
  `S3PrefixListId` resolved per-region.
- Existing ingress + no-public-ingress + description-charset tests stay green
  (no regression).

**Approach:**
- Capture the interface endpoints from the `add_interface_endpoint` loop
  (`graphrag_stack.py:266`) and the S3 gateway endpoint (`:264`) into handles.
- For each compute SG, set `allow_all_outbound=False` and add
  `connections.allow_to(...)` for exactly its call set (see Design decisions).
- Delete the `allow_all_outbound=True` rationale comment block at `:593-599` and
  replace with a one-line "closed egress, explicit per-call rules" note.
- Invert/replace `test_query_lambda_sg_allows_outbound`.

**Done when:** `pytest apps/infra/tests/test_stack.py` green with the new
closed-egress assertions; `cdk synth` succeeds.

### T2: Opt-in, observable default-deny clearance mode

**Depends on:** none
**Touches:** packages/graphrag/src/graphrag/visibility.py, packages/graphrag/tests/test_visibility.py, packages/graphrag/src/graphrag/cli.py, packages/graphrag/tests/ (CLI test)

**Tests:**
- (TDD) `default_deny` ON + no principal (`None` and `""`) ⇒
  `Clearance(persona="default-deny", allowed=frozenset())` (the `persona` field is
  required — the literal `Clearance(allowed=frozenset())` won't construct),
  `allows(x)` False for every tier (AC7).
- (TDD) precedence: `default_deny` ON + a **present** persona resolves exactly as
  default-deny OFF would (unknown ⇒ raise, known ⇒ normal) — the flag governs only
  the absent-principal cell (AC7).
- (TDD) `default_deny` ON + unrecognized non-empty persona ⇒ `ValueError`
  (fail-closed raise preserved, not silent-deny).
- (TDD) `default_deny` ON + known persona ⇒ that persona's normal clearance.
- (goal-based, observability) `_clearance` with `--default-deny` and no
  `--persona` returns the empty `Clearance` (not `None`), so a CLI query sees
  nothing — the demonstrable inversion (AC7).
- (regression) `default_deny` OFF / today's `resolve_clearance` and
  `clearance=None` unrestricted semantics are byte-unchanged.

**Approach:**
- Add the `default_deny` resolver path in `visibility.py`, keeping the empty-set
  fail-closed `Clearance` invariant the module already documents.
- Wire `cli.py:_clearance` to construct the empty `Clearance` when
  `--default-deny` is set and no persona is given (query layer untouched —
  empty `Clearance` already means "sees nothing").
- Update the module + `_clearance` docstrings **and the `--default-deny` flag's
  `--help` string** to name the inversion as a teaching demonstration, still a
  synthetic stand-in, not real authz (feeds AC8).

**Done when:** `pytest packages/graphrag/tests/test_visibility.py` + the CLI test
green; `--default-deny` with no persona observably filters to nothing; no
shipped-mode (default-deny OFF) behavior change.

### T3: pip-audit + Dependabot + CI workflow (pinned commands)

**Depends on:** none
**Touches:** .github/workflows/ci.yml, .github/dependabot.yml, pyproject.toml, AGENTS.md, .pip-audit-ignore

**Tests:** (goal-based)
- `pip-audit` runs locally and in CI over the locked set and exits non-zero on a
  seeded/known vuln; the ignore file's documented exceptions are honored (AC3).
- `.github/dependabot.yml` and `.github/workflows/ci.yml` are valid YAML and the
  workflow runs the pinned gate set on push/PR (AC5, AC6 minus the cdk-nag step
  added in T4).
- `AGENTS.md` § Commands no longer contains the `<…>` placeholders — its
  commands match the workflow's (AC6).

**Task-zero (grounding 2026-06-30):** the repo has **no CI**, so `ruff format`
has never been enforced — `ruff format --check` shows ~18 files / ~276 lines of
pre-existing drift under *every* ruff 0.6–0.15 (the formatter style drifted
across versions). A green `ruff format --check` gate (AC6) therefore requires
**pinning the toolchain** (`ruff==0.15.17`, `mypy==2.1.0` — both already green
for `ruff check`/`mypy`) and a one-time `ruff format` of the project code under
that pin. This is the precondition for the AC6 mechanism to exist, not optional
polish. **Scope:** CI lint/format runs over `packages apps` (the project's own
Python, matching `[tool.ruff].src`); `.claude/` (bundled agent assets) and
`tools/`/`scripts/` (dev tooling) are deliberately **out** of the strict gate to
bound blast radius. The one-time reformat lands as its own labeled commit so the
security diff stays legible.

**Approach:**
- Add `pip-audit` to a dev/CI dependency group; **pin** `ruff`/`mypy`; author the
  workflow with the **pinned** commands: `ruff check packages apps`,
  `ruff format --check packages apps`, `mypy`, `pytest`, `pip-audit` (the
  `cdk synth` + cdk-nag step lands in T4).
- Fill `AGENTS.md` § Commands (`128-138`) with those same commands so doc and CI
  agree (closes the unfilled-template gap).
- Author `dependabot.yml` for the `pip` + `github-actions` ecosystems.
- **CI self-hardening (AC6b):** top-level `permissions: contents: read`; trigger
  on `push` + `pull_request` (never `pull_request_target`); pin every `uses:` to a
  40-char commit SHA with a `# vX.Y` comment (resolve current SHAs via
  `gh api repos/<owner>/<repo>/git/refs/tags/<tag>` at EXECUTE).
- **`.pip-audit-ignore`:** start empty-but-headered (the tree has no known vuln at
  baseline); each future entry = vuln id + one-line reason + review-by date; the CI
  command consumes it (`pip-audit ... $(grep -v '^#' .pip-audit-ignore | ...)` →
  `--ignore-vuln` args, or `--ignore-vuln` inline if empty).
- This workflow is the CI surface `infra-secret-scan-ci` was blocked on; **do
  not** add gitleaks/`shellcheck` here (that stays the other item's scope) — just
  unblock it (the backlog entry is updated in T5).

**Done when:** the workflow runs the pinned gate set; `pip-audit` fails on a
known vuln in a scratch test; `AGENTS.md` commands match; YAML validates.

### T4: cdk-nag hard synth gate

**Depends on:** T1
**Touches:** apps/infra/app.py, apps/infra/tests/test_stack.py, .github/workflows/ci.yml, pyproject.toml, (suppressions)

**Tests:** (goal-based + one durable synth assertion)
- **Durable (AC4a):** a committed `test_stack.py` assertion applies
  `AwsSolutionsChecks` to the stack and asserts **no unsuppressed `AwsSolutions-*`
  error annotation** remains (`Annotations.from_stack`); regresses offline if the
  aspect is dropped or a violating resource is added.
- `cdk synth` fails on a **deliberate temporary** nag violation (proves the gate
  bites), passes once removed (AC4b).
- CI's `cdk synth` step is wired and gates the merge (AC6, cdk-nag step).
- Every `NagSuppressions` entry carries a non-empty `reason` citing sign-off.

**Task-zero (grounding 2026-06-30):** `cdk-nag` is not installed — `pip install
cdk-nag` (it is pip-installable) and add it to the `infra` extra so `cdk synth`
can apply the aspect locally and in CI.

**Approach:**
- Add `cdk-nag` to the IaC dev deps; apply `AwsSolutionsChecks` via `Aspects` in
  `app.py`.
- Triage the findings the tightened stack raises; suppress only with a
  human-signed reason (Ask-first boundary); fix the rest. Must not weaken
  ADR-0004's read-only grant to clear a finding.
- Add the `cdk synth` (nag) step to the CI workflow.

**Done when:** an unsuppressed finding fails the build; the clean stack synths
green; suppressions all carry reasons.

### T5: Document the tightened posture + the synthetic default-deny stand-in

**Depends on:** T1, T2, T4
**Touches:** docs/architecture/security.md, docs/backlog.md, docs/specs/security-hardening-followups/spec.md (status), packages/graphrag/src/graphrag/visibility.py (docstring)

**Tests:** (goal-based)
- `security.md` describes: the closed-egress posture (retiring the
  "allow-all egress, defence-in-depth debt" note at `security.md:213`), the
  `pip-audit`/Dependabot/`cdk-nag` gates (retiring the `:205` note), and the
  default-deny synthetic stand-in labeled as teaching, not authz (AC8).
- The B5 docstring labels the mode a synthetic stand-in (AC8).

**Approach:**
- Edit the "Out of scope this slice (named, not forgotten)" section to reflect
  what this spec discharged; add a slice-4 boundary note for the default-deny
  demonstration.
- Update `docs/backlog.md`'s `infra-secret-scan-ci` entry: its "blocked on the
  repo gaining a CI surface" condition is now met by AC6's workflow; the
  gitleaks/`shellcheck` jobs remain its open (now-unblocked) follow-on.

**Done when:** `security.md` reflects the new posture; the named follow-ups it
discharged are marked done.

### T6: Live IAM/SG evaluation (AC9)

**Depends on:** T1, T2, T3, T4, T5
**Touches:** docs/architecture/security.md (live findings), docs/specs/security-hardening-followups/spec.md (AC9 check)

**Tests:** (live / manual QA)
- Deploy on a clean account; confirm live ingest + hybrid Function-URL query +
  both smoke probes succeed under the tightened SGs (no silent egress block).
- Capture the deployed SG-egress + IAM posture into `security.md`.
- `cdk destroy` removes every billable resource; Budgets held at 150.

**Approach:**
- Follow the live-deploy env workarounds (memory); re-issue teardown + sweep log
  groups if the destroy client stalls.

**Done when:** AC9 checked with the live evidence recorded, or marked deferred to
the backlog anchor if live deploy is unavailable.

## Rollout

- **Delivery:** big-bang within the repo; all reversible (config + library +
  CI). The one live action is AC9's ephemeral deploy, torn down immediately.
- **Infrastructure:** no new resource — only SG egress *tightening* on existing
  SGs and a synth-time aspect. Budgets unchanged (150).
- **External-system integration:** GitHub Actions + Dependabot (repo-level
  config); no AWS-side standing change.
- **Deployment sequencing:** T1 before T4 (nag runs against the tightened
  stack); T1–T5 before the T6 live run.

## Risks

- **A1 over-tightening breaks a live call synth can't see** (Bedrock/ECR/S3/Logs
  egress) — the documented hang regression. Mitigated by per-component egress
  sets + the mandatory AC9 live run.
- **cdk-nag surfaces findings on the existing stack** that are accepted residuals
  (e.g. the one legitimate `ecr:GetAuthorizationToken` `*`). Risk of either a
  noisy gate or an over-broad suppression — each suppression is reason-signed and
  reviewed; ADR-0004's grant is never relaxed to clear a finding.
- **B5 scope creep** into changing shipped fail-open defaults — bounded by the
  additive/opt-in AC7 and the Ask-first boundary.

## Changelog

- 2026-06-30: initial plan. Four strands (A1 SG egress, A2 supply-chain gates,
  B5 default-deny clearance, A3 live eval) from the session's non-RFC hardening
  work; T1/T2/T3 `Depends on: none` (parallelizable), T4←T1, T5←T1/T2/T4,
  T6 last.
- 2026-06-30: pre-EXECUTE grounding refinements (no spec/AC change). (1) S3
  gateway-endpoint egress uses a `CfnParameter` + `ec2.Peer.prefix_list`, not
  `connections.allow_to` — a gateway endpoint exposes no connectable/prefix-list
  handle and `from_lookup` can't run in the offline synth test. Store-SG egress
  uses egress-only `add_egress_rule` (the ingress already exists, description-
  pinned). (2) AC6's `ruff format --check` requires pinning ruff/mypy + a
  one-time `ruff format` of `packages apps` (pre-existing drift; no CI ever
  enforced format) — task-zero, scoped to bound blast radius. (3) `cdk-nag`
  install is task-zero for T4.
