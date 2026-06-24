# Spec: infra-config-separation

- **Status:** Shipped
- **Shape:** infra (script refactor)
- **Mode:** full (risk triggers fired — see Assumptions)
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [ADR-0003](../../adr/0003-iac-tool-aws-cdk-python.md) (IaC is AWS CDK Python; these are the operational wrapper scripts around it), [ADR-0002](../../adr/0002-ephemeral-vpc-store-topology.md) (teardown-first; the scripts must keep removing every billable resource)
- **Contract:** none (operational shell scripts + a declarative config file; no published API surface)

> A maintenance refactor that touches **only** `apps/infra/scripts/` — it separates
> the *build-script logic* (the deploy/destroy/status mechanics) from the *external
> environment-configurable parameters and resource names* (stack name, region, tag
> defaults, operational knobs, per-deploy identity values), which today are scattered
> as inline `${VAR:-default}` literals across four shell files. The stack
> (`graphrag_stack.py`), the application/probe code, and the deploy *behavior* are
> unchanged. `Depends on:` nothing; it is a structural tidy of the existing slice-1–3
> deploy tooling, validated by a full live deploy of all three slices.

## Objective

A deployer (or CI) tuning the deploy — picking a region, a stack name, governance-tag
values, or supplying the required `BUDGET_EMAIL` — must today read four shell scripts to
find where each literal lives and how an override is spelled. This refactor gives the
infra tooling a **single declarative source of build parameters and resource names**
(`config.env`), leaving the scripts to hold **only logic** (credential caching, SLR
pre-creation, the `cdk` invocations, log-group sweeping). It follows the conventional
layered-config structure: a committed `config.env` of safe defaults, a gitignored
`config.local.env` for per-deployer values (e.g. the `BUDGET_EMAIL` PII), and a committed
`config.local.env.example` template — with a clear precedence (explicit env var >
`config.local.env` > committed `config.env`) that preserves the existing one-off
`FOO=bar scripts/deploy.sh` override workflow the README documents. The deploy *behavior*
is byte-for-byte unchanged; the win is legibility and a clean config/logic seam. Proven by
a full live deploy + three-slice verification + teardown on the refactored scripts.

## Boundaries

### Always do

- **Keep `config.env` declarative — no logic, no AWS calls.** It holds only
  `: "${VAR:=default}"` assignments and comments. Anything that calls `aws`, derives a
  value from caller identity, or branches stays in the scripts (it is logic, not config).
- **Treat both config files as executed shell, not data.** `_aws-env.sh` *sources*
  `config.local.env` then `config.env`, so their contents run with the caller's creds
  already exported (a tampering boundary, not a sandboxed read). `config.env` review must
  reject any line that is not a comment or a `: "${VAR:=…}"` assignment; `config.local.env`
  is trusted input equal in privilege to the scripts themselves (it is gitignored, so it
  is the local operator's own file). Source `config.local.env` only via the absence-guarded
  form (`[ -f … ] && . …`) so a missing local file does not abort under `set -e`.
- **Preserve `export` for every variable a subprocess reads.** `AWS_REGION` (and the
  derived `CDK_DEFAULT_REGION` / `CDK_DEFAULT_ACCOUNT` / `JSII_…`) are consumed by the
  `aws`/`cdk` child processes, so they must remain **exported** — a bare `: "${VAR:=…}"`
  in a sourced file assigns without exporting. `config.env` sets the *default value*; the
  `export` of subprocess-consumed vars stays in `_aws-env.sh` after sourcing.
- **Preserve override precedence: explicit env var > `config.local.env` > `config.env`.**
  Both config files use the `: "${VAR:=…}"` (assign-if-unset) form and `config.local.env`
  is sourced *before* `config.env`, so an exported env var (CI, or a one-off
  `BUDGET_EMAIL=… scripts/deploy.sh`) still wins over both files. This keeps the deploy
  workflow the README already documents working unchanged.
- **Keep teardown a feature (ADR-0002 / charter principle 4).** `destroy.sh` must still
  remove every billable resource and sweep the auto-created Lambda log groups; the
  refactor must not drop the log-group sweep or the SLR pre-creation.
- **Keep the credential-cache + no-poll operational lessons intact** (`_aws-env.sh`): one
  mode-600 session cache, refreshed only past the age threshold; no status polling.
- **Gitignore `config.local.env`; commit `config.env` and `config.local.env.example`.**
  Per-deployer identity values (the `BUDGET_EMAIL` subscriber address — PII; an
  account-specific `INVOKER_ROLE_ARN`) must never be committed.

### Ask first

- **Adding a new operational knob that changes deploy behavior** (e.g. a new `cdk` flag,
  a new resource). This refactor moves *existing* parameters; it introduces no new
  deploy behavior.
- **Changing a default value** (region `us-east-1`, stack name `GraphragSlice1`, the tag
  defaults, the `$50` budget). Extraction must preserve every current default verbatim.

### Never do

- **Never commit a real `BUDGET_EMAIL`, account id, or role ARN** in `config.env` or the
  `.example` — defaults there are placeholders only.
- **Never change the deployed topology, the stack name, or the deploy behavior.** A `cdk
  diff` against an unchanged stack would be empty; only the *wrapper scripts* change.
- **Never add a new top-level directory or a new tool dependency.** The config file lives
  beside the scripts it serves under `apps/infra/scripts/`.
- **Never put a secret or credential in any committed config file**, and never weaken the
  mode-600 posture of the credential session cache.

## Testing Strategy

This is a shell-script refactor with an infra/deploy verification mode; there is no unit
test harness for shell. Verification per criterion:

- **AC1–AC4 — goal-based checks.** `shellcheck -x` clean on every script + the config
  files; a source-and-echo sanity that each tunable resolves to its documented default with
  no override (and `config.local.env` absent), to its `config.local.env` value when one is
  set, and to an explicit env var when one is exported (the three precedence cases) —
  asserted in a **child process** for subprocess-consumed vars so the `export` contract is
  checked; `git check-ignore` confirms `config.local.env` is ignored **and** both
  `config.env` and the `.example` are tracked; `grep` confirms no inline
  `${VAR:-<resource/tag default>}` literals remain (the caller-derived `${DEPLOY_USER:-…}`
  / `${INVOKER_ROLE_ARN:-}` logic is exempt).
- **AC7 — goal-based check.** `pytest` over a small unit test for the `pre-pr.py` secret
  guard: an email-shaped `BUDGET_EMAIL=` or `arn:aws:iam::<digits>:role/` literal in a
  tracked `config*.env` fails; the obviously-fake `.example` placeholders pass.
- **AC5 — goal-based check (`cdk synth`).** `cdk synth` (and `pytest apps/infra/tests`,
  the in-process synth assertions) still produce the identical template through the
  refactored `CDK_APP` resolution — the stack is byte-unchanged.
- **AC6 — infra/deploy live verification (active end-to-end).** The refactored
  `scripts/deploy.sh` stands up `GraphragSlice1` to `CREATE_COMPLETE`; `scripts/status.sh`
  reports it; the slice-1 Neptune probe and slice-2 vector probe both return `{"ok": true}`;
  the corpus is dual-written by the Fargate task and the slice-3 SigV4 hybrid query returns
  a real Bedrock Claude answer + citations + a seed/hop trace; then `scripts/destroy.sh`
  tears it down and `scripts/status.sh` reports `DOES_NOT_EXIST`. This proves the
  refactored scripts drive the full three-slice deploy unchanged.

Gates: `shellcheck` (scripts), `ruff` + `mypy` + `pytest` (Python, unaffected but run),
`cdk synth` (template unchanged).

## Acceptance Criteria

- [x] **AC1 — A declarative `config.env` is the single source of build parameters and
  resource names.** `apps/infra/scripts/config.env` defines, as `: "${VAR:=…}"`
  assignments with explanatory comments and **no logic / no `aws` calls**: the stack name
  (`STACK=GraphragSlice1`), the region default (`config.env` **owns the full chain** —
  `: "${AWS_REGION:=${AWS_DEFAULT_REGION:-us-east-1}}"` — so the fallback lives in exactly
  one place; `_aws-env.sh` only re-`export`s `AWS_REGION`, it does not re-derive it), the
  governance-tag defaults (`DEPLOY_ENV=demo`,
  `DEPLOY_DEPARTMENT=unspecified`, `DEPLOY_APPLICATION=graphrag`), and the operational
  knobs (`CREDS_CACHE`, the cred-cache max-age `CREDS_MAX_AGE_MIN=50`, `VENV`, `CDK_APP`,
  `DEPLOY_OUTPUTS_FILE`, the OpenSearch service-linked-role service list
  `OPENSEARCH_SLR_SERVICES`). Each value **preserves the current effective default** — the
  literals that are inline today move verbatim, and the three that are *inlined constants
  rather than variables today* (`OPENSEARCH_SLR_SERVICES` from the `for svc in … es …`
  loop, `DEPLOY_OUTPUTS_FILE` from the `$HERE/../cdk.out/deploy-outputs.json` literal,
  `CREDS_MAX_AGE_MIN` from the `+50` mmin literal) become named knobs whose default equals
  the prior constant, with no behavior change. `OPENSEARCH_SLR_SERVICES` is consumed by an
  intentional word-split (`for svc in $OPENSEARCH_SLR_SERVICES`) — that split is asserted
  by the live SLR step still creating both roles. *(goal-based)*
- [x] **AC2 — Per-deployer values are externalized, never committed; secret-bearing paths
  stay out of the tree.** `config.env` documents the per-deploy values that have no safe
  default — the required `BUDGET_EMAIL` and the optional `INVOKER_ROLE_ARN` — **as comments
  only, never as live `: "${VAR:=…}"` assignments**, so `deploy.sh`'s `: "${BUDGET_EMAIL:?}"`
  guard remains the enforcement point and no placeholder email/ARN can flow into a real
  deploy. A committed `config.local.env.example` shows how to set them with obviously-fake
  placeholders. `config.local.env` is **gitignored via an anchored path**
  (`apps/infra/scripts/config.local.env`), verified three ways: `git check-ignore` on it
  exits 0, and on `config.env` **and** `config.local.env.example` exits 1 (both tracked) —
  so an over-broad ignore glob is caught. `CREDS_CACHE` keeps its `${TMPDIR:-/tmp}/…`
  default (outside the repo tree, mode-600 via the preserved `umask 077`); `DEPLOY_OUTPUTS_FILE`
  keeps its default under the already-gitignored `apps/infra/cdk.out/`. *(goal-based)*
- [x] **AC3 — The scripts hold only logic and source the config.** `_aws-env.sh` sources
  `config.local.env` (absence-guarded) then `config.env` **before** the credential-cache
  logic, and contains only logic (cache freshness, region/account export). `deploy.sh`,
  `destroy.sh`, `status.sh` carry **no inline resource-name or tag-parameter defaults** —
  they reference the config-provided variables. The grep gate
  (`grep -nE '\$\{[A-Z_]+:-' deploy.sh destroy.sh status.sh _aws-env.sh`) returns **no
  resource-name/tag-default literal**; the **derivation/logic vars are exempt by name** and
  are the *only* matches allowed — in `deploy.sh`: `${DEPLOY_USER:-$(aws …)}` and
  `${INVOKER_ROLE_ARN:-}`; in `_aws-env.sh`: `${REFRESH_CREDS:-0}` and
  `${CDK_DEFAULT_ACCOUNT:-$(aws …)}` (runtime/caller-derived logic, not config defaults). Any
  *other* `${VAR:-…}` match is a finding. *(goal-based — `grep`)*
- [x] **AC4 — Precedence is env > local > committed (export-preserving), and the scripts
  are `shellcheck` clean.** With no override and `config.local.env` **absent**, each tunable
  resolves to its `config.env` default; with a `config.local.env` present, its value wins
  over the committed default; with an env var exported, it wins over both (the documented
  one-off override). The precedence check asserts the resolved value **in a child process's
  environment** (`bash -c 'echo $AWS_REGION'`), not merely echoed in the sourcing shell, so
  the `export` contract for subprocess-consumed vars is verified, not just the value.
  `shellcheck -x` passes on all four scripts and both config files. *(goal-based)*
- [x] **AC5 — The synthesized template is unchanged.** `cdk synth` through the refactored
  `CDK_APP` and `pytest apps/infra/tests` both pass and produce the same `GraphragSlice1`
  template as before the refactor (the stack code is untouched). *(goal-based synth)*
- [x] **AC6 — Live deploy verifies all three slices on the refactored scripts, then tears
  down.** **Verified live (2026-06-24, account `752989493306`/`us-east-1`, `config.local.env`
  absent so the committed-defaults path was exercised).** The refactored `scripts/deploy.sh`
  → `CREATE_COMPLETE` (18 min); `scripts/status.sh` → `CREATE_COMPLETE`; slice-1 Neptune
  smoke probe `{"ok": true, "retrieved_node": "person:smoke-…", "neighbors": […]}`; slice-2
  vector smoke probe `{"ok": true, "retrieved_id": "smoke-…", "dims": 256}`; the slice-3
  SigV4 hybrid query via the IAM-auth Function URL returned (exit 0) a real Bedrock **Claude**
  answer naming **KEP-1880 / KEP-2086** as @thockin/SIG-Network-owned, with citations and the
  dual-seed trace (`question: person:thockin` + a 2-hop live Neptune `TECH_LEADS`/`OWNS`
  expansion); `scripts/destroy.sh` → `DOES_NOT_EXIST`, no billable resource left. The
  refactored scripts drove the full three-slice deploy unchanged. **Caveat (not a refactor
  regression):** the Fargate *vector* dual-write hit a pre-existing `opensearch.create_index`
  idempotency bug (surfaced by running the slice-2 probe before ingestion, which leaves the
  index), so the hybrid query's vector-seed half was empty — graph + synthesis + Function-URL
  path fully proven, and the vector store itself proven by the slice-2 probe; tracked in
  `docs/backlog.md` → `opensearch-create-index-idempotency`. *(live infra/deploy)*
- [x] **AC7 — A committed-config secret guard fails closed on the newly-introduced files.**
  Because this PR *introduces* the committed `config*.env` files that make a PII/credential
  commit possible, `tools/hooks/pre-pr.py` gains a cheap guard: it scans **every tracked file
  whose name starts with `config` under `apps/infra/scripts/`** (`config.env`,
  `config.local.env.example`, and any future `config.<stage>.env` — listed via `git
  ls-files`, so the set is covered by construction; the gitignored `config.local.env` is
  never tracked, so it is out of scope) for an email address or an `arn:aws:iam::<acct>:role/`
  literal and **fails** if one is found. **Placeholder predicate** (what passes): email
  addresses whose domain is `example.com` / `example.org` / `example.net` (RFC 2606 reserved),
  and ARNs whose account field is all-zero (`000000000000`) or a `<…>` angle-bracket token,
  are treated as placeholders and pass; anything else fails. (So the `.example`'s
  `you@example.com` and a `000000000000` ARN pass; a real address or real-account ARN fails.)
  This is a targeted guard,
  not a repo-wide scanner; wiring a full secret scanner (gitleaks/detect-secrets) and
  `shellcheck` into CI is **deferred** (the repo has no CI yet) — see
  `(deferred: infra-secret-scan-ci)`. *(goal-based)*

## Assumptions

- Technical: the only files changed are under `apps/infra/scripts/` plus a one-line
  `.gitignore` addition; `app.py`, `stacks/graphrag_stack.py`, the probes, and the
  application code are untouched, so the deployed topology is byte-identical (source:
  current scripts; `cdk synth` is the AC5 guard).
- Technical: the existing override mechanism is shell env vars (`${VAR:-default}`); the
  refactor preserves that semantics via assign-if-unset (`:=`) so CI and one-off
  `FOO=bar scripts/deploy.sh` invocations are unaffected (source: README runbook;
  `deploy.sh`).
- Process: this is full work-loop mode — **structural** (new config files + a new
  config/logic seam in the deploy tooling) and **infra/deploy** (a live deploy of a
  security-boundary stack: IAM, credentials, a public IAM-auth Function URL), so the
  spec-stage + diff-stage `security-reviewer` pass is mandatory and the
  `quality-engineer` operational-safety lens applies (source: `docs/CONVENTIONS.md` risk
  triggers; work-loop infra doctrine).
- Process: validated live in this environment (AWS account reachable, `cdk`/`docker`/
  `shellcheck` present) by a full deploy → three-slice verify → teardown on the refactored
  scripts (source: live deploy, this PR).

## Changelog

- 2026-06-24 — Spec authored. Extract environment-configurable parameters and resource
  names from the four `apps/infra/scripts/` shell files into a declarative `config.env`
  (+ gitignored `config.local.env` + committed `.example`), preserving every default and
  the env-override precedence; scripts keep only logic; validated by a full live
  three-slice deploy + teardown.
- 2026-06-24 — Shipped. Implemented `config.env` + `config.local.env.example` + the
  `_aws-env.sh`/`deploy.sh` refactor + the `pre-pr.py` committed-config secret guard.
  Gates green (shellcheck `-x`, ruff, mypy, pytest), `cdk synth` byte-identical to the
  pre-refactor baseline, precedence (env > local > committed, export-preserving) proven in
  a child process. Pre- and post-EXECUTE adversarial + security + quality/operational
  reviews iterated to clean. AC6 verified live (deploy → status → slice-1/2 probes →
  slice-3 SigV4 hybrid query with a real Claude answer → teardown). The live run surfaced
  one pre-existing, out-of-scope bug (`opensearch-create-index-idempotency`, backlog).
