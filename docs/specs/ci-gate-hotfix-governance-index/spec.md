# Spec: CI gate hotfix + governance-index bootstrap

- **Status:** Shipped <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Constrained by:** ADR-0002, ADR-0010 (referenced by the index), PR #101 follow-on

Mode: light (judgment call: the trivyignore change moves an existing suppression
to the file CI actually reads — no new risk accepted; the `mcp<2` pin narrows an
existing dependency to the API the code targets; the governance index is a
non-load-bearing docs manifest. No content-level security or governance change.)

## Objective

Both CI checks that failed on PR #101's merge run pass on main, and
`docs/governance-index.yaml` exists so `generate-iac`/`new-adr` Stage-0 routing
is mechanical:

1. **`gates` (pytest)**: `mcp 2.0` broke `mcp.server.fastmcp` imports — the
   root `pyproject.toml` pins `mcp>=1.0,<2` (matching the 1.x FastMCP API the
   tool server targets; the Lambda zip is built from this same `[server]`
   extra, so the ceiling covers it too); the 2.x migration is a backlog entry.
2. **`tf-gates` (trivy)**: the AVD-AWS-0095 SNS suppression lives in
   `apps/infra-tf/.trivyignore` (CI runs `working-directory: apps/infra-tf`);
   the repo-root `.trivyignore` from PR #101 is deleted (it was never read by
   CI, and its AVD-AWS-0031 entry duplicated the in-dir register). Stale
   references and the factually-wrong `pre-existing-trivy-high-findings`
   backlog entry (those IDs were already suppressed in-dir) are corrected.
3. **Governance index**: `docs/governance-index.yaml` maps the seven canonical
   IaC domains (state/layout/iam/tagging/networking/pipeline_auth/remediation)
   to this repo's ADRs, with explicit `adrs: []` gap rows where no ADR exists.

## Acceptance Criteria

- [x] AC1: `python -c "import mcp.server.fastmcp"` succeeds in a clean venv
  installing the pinned constraint; `pyproject.toml` carries `mcp>=1.0,<2`
  with a rationale comment; backlog entry `mcp-2x-migration` exists.
- [x] AC2: `trivy config --exit-code 1 --severity HIGH,CRITICAL --skip-dirs
  tests .` run from `apps/infra-tf/` (the exact CI invocation) exits 0; no
  repo-root `.trivyignore` remains; plan.md/backlog references corrected.
- [x] AC3: `docs/governance-index.yaml` exists with the seven domain rows,
  each `adrs:` list resolving to real ADR files; gaps are explicit.
- [x] AC4: CI green on the PR (both `gates` and `tf-gates`).
