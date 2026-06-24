# Plan: infra-config-separation

- **Spec:** [`spec.md`](spec.md)
- **Mode:** full (structural + infra/deploy)

## Files touched

- `apps/infra/scripts/config.env` — **new.** Declarative build parameters + resource
  names (the single config source).
- `apps/infra/scripts/config.local.env.example` — **new.** Committed template for the
  per-deployer values (`BUDGET_EMAIL`, `INVOKER_ROLE_ARN`, etc.).
- `apps/infra/scripts/_aws-env.sh` — sources the config files (local then committed)
  before the cred logic; keeps only logic; literals removed.
- `apps/infra/scripts/deploy.sh` — inline tag/SLR/outputs-path defaults removed; references
  config-provided vars; keeps caller-derived `DEPLOY_USER`/`INVOKER_ROLE_ARN` logic and the
  `BUDGET_EMAIL` required-guard.
- `apps/infra/scripts/destroy.sh`, `status.sh` — inherit config via `_aws-env.sh`; comment
  refresh only (already reference `$STACK`).
- `.gitignore` — add the anchored path `apps/infra/scripts/config.local.env`.
- `tools/hooks/pre-pr.py` — add a cheap secret guard over the tracked `config*.env` files
  (AC7).
- `apps/infra/README.md`, `docs/architecture/deployment-and-verification.md` — point the
  runbook at `config.env` / `config.local.env`; record the live three-slice re-verification.
- `docs/backlog.md` — record the deferred `infra-secret-scan-ci` (full gitleaks +
  `shellcheck` CI) item.

## What is NOT changing

`app.py`, `stacks/graphrag_stack.py`, every probe/Lambda, the application package, and the
deploy *behavior* (same `cdk bootstrap`/`deploy`/`destroy` calls, same parameters, same
defaults). `cdk synth` output is byte-identical (AC5).

## Declined-pattern register

- **Tempted to add a `config.local.env` auto-discovery loop over multiple env files
  (`config.<stage>.env`); declining** — there is one stack and one stage here; a
  multi-stage config matrix is configurability for a hypothetical future (ADR-0002 is
  explicitly single ephemeral stack). One committed default + one local override is enough.
- **Tempted to switch the scripts to a `.env`+`dotenv`/`direnv` loader; declining** — adds
  a tool dependency for what `source` already does; the scripts are pure bash by design.
- **Tempted to also refactor the cred-caching logic / extract a `lib.sh`; declining** —
  out of scope (the task is config/logic *separation*, not a logic rewrite); would inflate
  the diff and the live-deploy blast radius. Logic stays where it is.
- **Tempted to make the budget amount / VPC AZ count configurable; declining** — those are
  stack-construct values in `graphrag_stack.py`, not script parameters; moving them would
  cross the "stack unchanged" boundary.
- **Export decision (recorded per reviewer B1/B8):** subprocess-consumed vars
  (`AWS_REGION`, `CDK_DEFAULT_REGION`, `CDK_DEFAULT_ACCOUNT`, `JSII_…`) keep their `export`
  in `_aws-env.sh` **after** sourcing `config.env` — chosen over `export VAR="${VAR:=…}"`
  in the config so the config file stays purely declarative (`:=`-only) and the
  export-as-side-effect lives in the logic layer where it belongs.
- **Tempted to add a repo-wide gitleaks + `shellcheck` CI pipeline (reviewer flagged
  `degraded: no scanner`); declining for this PR, deferring to backlog** — the repo has no
  CI at all, so standing up a pipeline is a separate concern; the proportionate in-scope
  control is a targeted `pre-pr.py` guard over exactly the config files this PR introduces
  (T0b), with the full scanner deferred as `infra-secret-scan-ci`.

## Tasks

### T0a — Capture the pre-refactor synth baseline (mechanism for AC5)
- **Verification:** goal-based. The baseline template is captured **before** any script
  edit, on the current scripts, to a named artifact.
- **Done when:** `cdk synth GraphragSlice1` (current scripts) is saved to
  `/tmp/graphrag-baseline.template.yaml`; T5 diffs the post-refactor synth against it.
- **Status:** DONE (captured this session — 2192 lines; `pytest apps/infra/tests` = 31
  passed).

### T0b — `pre-pr.py` committed-config secret guard (AC7)
- **Verification:** goal-based + a unit test. A function scans tracked `config.env` /
  `config.local.env.example` for an email-shaped `BUDGET_EMAIL=` or `arn:aws:iam::<digits>:role/`
  literal and fails; obviously-fake `.example` placeholders pass.
- **Done when:** the guard runs in `pre-pr.py` and a `pytest` test pins both the
  fail-on-real and pass-on-placeholder cases. (AC7)

### T1 — Create `config.env` + `config.local.env.example`; gitignore the local file
- **Verification:** goal-based. `shellcheck -x` clean; `git check-ignore
  apps/infra/scripts/config.local.env` exits 0; `git status` shows the `.example` tracked.
- **Done when:** `config.env` holds every current effective default as `: "${VAR:=…}"` with
  no `aws` call / no logic; `BUDGET_EMAIL`/`INVOKER_ROLE_ARN` appear **only as comments**
  (never live `:=`, so the `:?` guard stays the enforcement point); `CREDS_CACHE` stays
  `${TMPDIR:-/tmp}/…` and `DEPLOY_OUTPUTS_FILE` stays under `cdk.out/`; `.example` documents
  the per-deploy values with obviously-fake placeholders; `.gitignore` ignores the anchored
  `apps/infra/scripts/config.local.env` while `config.env` + `.example` stay tracked
  (`git check-ignore` three-way). (AC1, AC2)
- **Approach:** extract the literals enumerated in the spec; group by Resource names /
  Region / Tag defaults / Operational knobs / Per-deploy (commented placeholders).

### T2 — Refactor `_aws-env.sh` to source the config and hold only logic
- **Verification:** goal-based. `shellcheck -x` clean; source it in a subshell and `echo`
  `$STACK`, `$AWS_REGION`, `$CREDS_CACHE`, `$CDK_APP` — each equals the prior default.
- **Done when:** `_aws-env.sh` computes `SCRIPT_DIR`/`INFRA_DIR`, sources `config.local.env`
  via `[ -f "$SCRIPT_DIR/config.local.env" ] && . "$SCRIPT_DIR/config.local.env"` then
  `. "$SCRIPT_DIR/config.env"`, then runs the cred-cache logic with the `find` age threshold
  consuming the config knob (`-mmin "+${CREDS_MAX_AGE_MIN}"`, and the stale "50 min" comment
  refreshed to name the knob), and **plain-re-`export`s the subprocess-consumed vars**
  (`export AWS_REGION` — value+fallback owned by `config.env`, not re-derived here — plus
  `CDK_DEFAULT_REGION`/`CDK_DEFAULT_ACCOUNT`/`JSII_…`); no parameter/resource literals remain
  in it (only the exempt `${REFRESH_CREDS:-0}` / `${CDK_DEFAULT_ACCOUNT:-$(aws…)}` logic). (AC3)
- **Depends on:** T1.

### T3 — Refactor `deploy.sh` (and comment-refresh `destroy.sh`/`status.sh`)
- **Verification:** goal-based. `shellcheck -x` clean; `grep -nE '\$\{[A-Z_]+:-' deploy.sh
  destroy.sh status.sh` returns no resource-name/tag-default literal; a `cdk synth`-only dry
  path resolves all referenced vars (no unbound-variable error under `set -u`).
- **Done when:** `deploy.sh` references `$DEPLOY_ENV`/`$DEPLOY_DEPARTMENT`/
  `$DEPLOY_APPLICATION`/`$OPENSEARCH_SLR_SERVICES`/`$DEPLOY_OUTPUTS_FILE`/`$STACK` from the
  config; the caller-derived `DEPLOY_USER`/`INVOKER_ROLE_ARN` logic and the `BUDGET_EMAIL`
  guard remain; `destroy.sh`/`status.sh` unchanged but for comments. (AC3)
- **Depends on:** T1, T2.

### T4 — Precedence + `shellcheck` validation
- **Verification:** goal-based. Cases prove env > local > committed for a representative
  var (`STACK`, `DEPLOY_DEPARTMENT`) and for an **exported** subprocess-consumed var
  (`AWS_REGION` asserted via `bash -c 'echo $AWS_REGION'`); the committed-default case is
  run with `config.local.env` **absent**; `shellcheck -x` clean across all four scripts +
  both config files.
- **Done when:** all precedence cases resolve as documented (incl. the child-process export
  check); `shellcheck` is clean. (AC4)
- **Depends on:** T1, T2, T3.

### T5 — Confirm the synthesized template is unchanged
- **Verification:** goal-based (`cdk synth`). `pytest apps/infra/tests` passes; `cdk synth
  GraphragSlice1` succeeds through the refactored `CDK_APP`; diff of the synthesized
  template against a pre-refactor synth is empty.
- **Done when:** synth assertions green and the template is byte-identical. (AC5)
- **Depends on:** T2 (the `CDK_APP` resolution path).

### T6 — Live deploy: verify all three slices, then teardown
- **Verification:** infra/deploy (active end-to-end). Layered: static (`shellcheck`/synth,
  T1–T5) < plan (`cdk diff` empty / bootstrap idempotent) < convergent apply (`deploy.sh`
  → `CREATE_COMPLETE`, re-runnable) < active smoke (slice-1 + slice-2 probes `ok:true`;
  slice-3 dual-write + SigV4 hybrid query real answer) < rollback (`destroy.sh` →
  `DOES_NOT_EXIST`, the known-good teardown named before apply).
- **Done when:** `deploy.sh` deploys; `status.sh` = `CREATE_COMPLETE`; both probes
  `ok:true`; the hybrid query returns a Claude answer + dual-seed trace; `destroy.sh`
  leaves `DOES_NOT_EXIST`. Record the observed outputs in
  `deployment-and-verification.md`. (AC6)
- **Depends on:** T1–T5 + clean post-EXECUTE review.

## Rollout

The refactor ships as one PR. Live verification is deploy-then-teardown (ADR-0002
teardown-first); the known-good rollback is `scripts/destroy.sh` plus, if a half-applied
stack is stuck, `cdk destroy` directly — both already exercised by slices 1–3. No
migration, no data, no standing resource after the run.
