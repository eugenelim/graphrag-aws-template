# ADR-0021: Git repository acquisition: mechanism for delivering repo content and a correct delta to the VPC-private ingestion task

- **Status:** Proposed <!-- candidates listed; selection pending RFC-0005 review -->
- **Date:** 2026-09-21
- **Decision-makers:** eugenelim
- **Supersedes:** [ADR-0016](0016-git-ingestion-commit-sha-delta-medallion.md) **§2 only** (git remote egress: CodePipeline/S3-mirror source). ADR-0016 §§1, 3–8, 10–12 carry forward unchanged. If candidate B is selected, §1's *diff-mechanism* sentence is superseded as well; §1's choice of the commit SHA as the change signal stands in every candidate.
- **Related:** [RFC-0005](../rfc/0005-git-repository-acquisition-mechanism.md) (the review this records); [ADR-0002](0002-ephemeral-vpc-store-topology.md) (no-NAT egress posture); [ADR-0011](0011-neptune-sparql-rdf-engine-and-text2sparql-guard.md) (`ingestion_task_role` write grant); [RFC-0004 §D6](../rfc/0004-biz-ops-kg-pivot.md); [`spec-git-ingestion`](../specs/spec-git-ingestion/spec.md); [`architecture/biz-ops-knowledge-graph/ingestion.md`](../architecture/biz-ops-knowledge-graph/ingestion.md)

## Decision summary

- **Decision:** **PENDING.** Four acquisition candidates (A–D) are on the table, plus a separate working-storage candidate (E) on an orthogonal axis; RFC-0005 carries the review that selects them. This ADR is logged as `Proposed` so the decision has a home and a number before the review, not after it.
- **Because:** ADR-0016 §2's mechanism was never implemented, the mechanism that shipped instead cannot compute the delta the rest of the pipeline depends on, and the requirement has since widened to any git service including internal self-managed hosts.
- **Applies to:** How repository content reaches the Fargate ingestion task, how the add/modify/delete/rename set is computed, and — on a second, orthogonal axis — whether the working copy persists between runs. It does not touch the medallion layers, artifact keying, or store coordination.
- **Tradeoff accepted:** To be recorded with the selection. Each candidate's principal cost is named in Alternatives considered.
- **Revisit if:** The corpus outgrows the selected candidate's storage profile, the adopting organisation's git host changes class, or the delta-correctness confirmation below starts failing.

## Context

ADR-0016 §2 specified that the Fargate task would clone from S3 "using the AWS CLI `s3 cp` + `git bundle` pattern." Nothing in the repository produces a bundle. What shipped instead is a CodePipeline `CodeStarSourceConnection` source with `OutputArtifactFormat = "CODE_ZIP"`, archived to `latest/repo.zip`.

Per the AWS action reference, `CODE_ZIP` is "a ZIP file with a shallow copy of your commit" — the tree at that commit, with no `.git` directory and no history. `GitDeltaReader.read_delta()` runs `git diff <last_sha>..HEAD --name-status`, which needs history reachable from `last_sha`, and `MedallionOrchestrator._read_file()` reads Bronze bytes with `s3.get_object(Key=<repo-relative path>)`, which needs extracted per-file objects. Three artifacts, three incompatible mechanisms, none of which agrees with ADR-0016's own text.

The AWS-native escape hatch does not close this: `CODEBUILD_CLONE_REF`, the only source format carrying git metadata, "can only be used by CodeBuild downstream actions" and errors elsewhere.

Two requirements have also hardened since ADR-0016 was accepted. The platform must ingest from **any git service** — GitHub, GitHub Enterprise Server, GitLab.com, internal self-managed GitLab, or another host. And any credential involved is **provided by the adopting organisation**; the platform consumes a reference and owns no part of its lifecycle.

Full evidence, including the as-built divergence register, is in [`architecture/biz-ops-knowledge-graph/ingestion.md`](../architecture/biz-ops-knowledge-graph/ingestion.md).

## Decision

> **Pending RFC-0005.** On selection, this section states the chosen mechanism and this ADR moves to `Accepted`.

Whichever candidate is selected, these constraints bind it. They are decided, not pending:

1. **The repository stays bare where one exists on disk.** Object store and refs, no checked-out worktree. Verified against a real repository: `git --git-dir=<dir> diff <sha>..HEAD --name-status -M` returns the `R<pct>\told\tnew` form `GitDeltaReader.parse_name_status()` already parses, and `git show <sha>:<path>` reads back a path deleted at HEAD. A worktree is a second full copy of the corpus for no benefit.

2. **A delta that cannot be established correctly fails the run.** Silent truncation, an unreachable `last_sha`, or an empty result that cannot be distinguished from a quiet corpus must produce a failed run, not a successful one. The empty-tree full-rescan fallback (ADR-0016 §12) is the recovery path.

3. **"No changes" and "could not determine changes" are distinct outcomes** in the ingestion status registry. An expired organisation credential must not read as a quiet corpus.

4. **Credential lifecycle stays with the adopting organisation.** The platform consumes a reference to an organisation-managed secret and must not assume it can rotate, replace, or inspect it.

5. **No NAT gateway on the ingestion subnet**, carried forward from ADR-0002 and RFC-0004 § Security posture.

## Decision drivers

Ranked by business importance × architectural risk, as agreed in RFC-0005:

1. **Delta correctness, or loud failure.** A wrong delta silently corrupts the graph with no operational signal — the worst failure available to this system.
2. **No NAT gateway on the ingestion subnet.** The Text2SPARQL and kNN guards depend on the no-egress posture.
3. **Any git service, including internal self-managed.** Eliminates anything bound to a fixed provider list.
4. **Fully provisionable by IaC.** The charter promises a reproducible clone-and-deploy demo.
5. **Organisation-owned credential.** The adopter's secret management is authoritative.
6. **Fits the working-storage budget available to it.** 20 GiB ephemeral default minus both container image forms, unmeasured today. Candidate E changes which budget applies — a provisioned volume is sized independently — but not the requirement to measure before sizing.

## Consequences

**On acceptance:**

- ADR-0016's status gains a scoped note naming §2 as superseded. The precedent is ADR-0011 and ADR-0014, both of which scope supersession in prose rather than with a `§n` marker; this ADR uses the marker because ADR-0016's Decision section is explicitly numbered. The literal line ADR-0016 receives:

  ```
  - **Status:** Accepted — §2 superseded by [ADR-0021](0021-git-repository-acquisition-mechanism.md) <!-- git remote egress only; §§1, 3-8, 10-12 stand -->
  ```

  Its body is untouched — `CONVENTIONS.md` puts `adr/*` in the Frozen layer where "status fields can change, bodies cannot."
- `spec-git-ingestion` is amended in the implementing PR; spec drift is a bug.
- The as-built divergence register in `architecture/biz-ops-knowledge-graph/ingestion.md` is replaced by a description of the built mechanism.

**Regardless of selection:**

- `ephemeral_storage` is set from a measurement of the container image's contribution, which does not exist yet.
- Concurrent-run protection is still unowned. `ManifestManager` documents an assumption that one task runs at a time, and the EventBridge trigger does not enforce it. **Candidate E2 would sharpen this, not resolve it:** ECS creates a new volume per task, so two concurrent runs receive two volumes; both restore the same snapshot and diverge, and because the next run resolves a single latest-snapshot pointer, one run's writes are never seen again. E1 leaves the gap unchanged rather than worsened. Selecting E2 means single-flight gains an owner in the same decision.

## Confirmation

- **Mode:** architecture fitness test
- **Signal:** a delta that cannot be established correctly — a truncated file list, an unreachable `last_sha`, or an unauthenticated source — produces a failed run and an SNS alert, never a `SUCCEEDED` run item. A run that completes asserts that its delta was computed over a known-good base.
- **Owner:** whoever owns the ingestion task's on-call path; unassigned while this ADR is `Proposed`.

## Alternatives considered

The candidates, with their principal cost. **A–D are the acquisition axis and are mutually exclusive; E is a separate working-storage axis that composes with A, B, or C and is moot under D.** Full analysis, including the requirement-by-candidate matrix and E's composed scoring, is in [RFC-0005](../rfc/0005-git-repository-acquisition-mechanism.md).

| Candidate | Shape | Principal cost |
|---|---|---|
| **A** — CodeBuild bundle hop | `CODEBUILD_CLONE_REF` → CodeBuild full clone → `git bundle` → S3 → bare clone on the task | Bound to CodeConnections' provider list, and self-managed hosts need a host resource plus a console step no IaC can complete. Largest disk footprint, scaled by repository history. |
| **B** — `CODE_ZIP` tree-diff | Keep the deployed format; diff the extracted tree against a stored path→hash manifest in Python | Same CodeConnections binding as A. Loses rename detection and historical-byte access, and holds one extracted tree plus a path→hash index. Also supersedes ADR-0016 §1's diff-mechanism sentence — not its signal choice. |
| **C** — Self-hosted mirror task | Short-lived Fargate task runs plain `git` against any remote, writes a bundle to S3 with one atomic `PUT`; EventBridge Scheduler wakes it, `git ls-remote` short-circuits when HEAD is unchanged | We operate a component AWS was operating. Change latency becomes the poll interval. The public-subnet variant creates an internet-facing ENI — an ADR-0002 posture question even with an egress-only security group. |
| **D** — Host REST API | Organisation service account calls the host's compare API for the delta and its contents API for bytes, behind a per-host-family adapter. No git binary, no repository on disk | GitHub's compare endpoint returns at most 300 changed files "for the entire comparison" and truncates with no flag, so the correctness check is a count bound rather than an assertion. GitLab signals truncation explicitly via `compare_timeout`, `collapsed`, and `too_large`. Two API dialects to maintain. |
| **E** — Provisioned working volume *(separate axis)* | KMS-encrypted gp3 volume attached to the ingestion task holds the bare repository. **E1**: fresh volume per run — headroom only. **E2**: warm clone across runs, by snapshot round-trip or EFS — `git fetch` replaces a full clone | ECS cannot reattach a volume — "it must be a new volume. You can't attach an existing Amazon EBS volume to a task" — so E2 is a restore-from-snapshot loop with a per-run snapshot create, restore, and orphan reaper, or EFS (which forfeits single-attach). EventBridge's `EcsParameters` has no volume field, so the deployed trigger must become a `RunTask` shim; this is an AWS API limit, not a Terraform gap. E2 retains a billable artifact past `destroy` unless it is put in the teardown path, and it makes the unowned concurrent-run gap more dangerous; E1 leaves that gap unchanged. |

**Do-nothing** was rejected outright: the deployed path cannot compute a delta, so leaving it produces successful-looking runs over a stale graph.

## Open questions

- Which acquisition candidate (A–D). This is the main body of RFC-0005.
- Whether to take candidate E, and if so E1 or E2 — a **separate decision** from the
  one above, settled in the same review. Selecting D makes it moot.
- Whether requirements 3 and 4 in Decision drivers are as hard as written. If the platform only ever targets CodeConnections-supported hosts, candidate A returns as a serious contender — RFC-0005 asks the team to confirm the requirements before selecting.
- **ADR-0016 §5 specifies `DROP GRAPH` for orphan removal, and its literal text is not safe to implement.** `spec-git-ingestion` forbids it explicitly — "`DROP GRAPH` on `urn:graph:normative` would destroy the entire normative partition" — and requires partition-scoped `DELETE WHERE`, which is what the spec's acceptance criteria, the architecture sequence diagram, and `NeptuneLoadClient` all describe. §5 is carried forward here as written because resolving it is outside RFC-0005's scope, but it needs its own ADR and must not be implemented from ADR-0016's wording.
- The container image's actual size, and therefore real ephemeral headroom. No candidate's storage profile can be made quantitative until it is measured.

## References

- [CodeStarSourceConnection action reference](https://docs.aws.amazon.com/codepipeline/latest/userguide/action-reference-CodestarConnectionSource.html) — `CODE_ZIP` shallow copy; `CODEBUILD_CLONE_REF` is CodeBuild-only
- [Create a connection to GitLab self-managed](https://docs.aws.amazon.com/dtconsole/latest/userguide/connections-create-gitlab-managed.html) — host resource, VPC fields, and the CLI/CloudFormation `PENDING` → console handshake
- [Fargate task ephemeral storage](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-task-storage.html) — 20 GiB default, 200 GiB maximum, both image forms charged against it
- [Use Amazon EBS volumes with Amazon ECS](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ebs-volumes.html) — Fargate platform version 1.4.0+, Linux only, one volume per task, and the "must be a new volume" rule behind candidate E's E1/E2 split
- [Specify Amazon EBS volume configuration at Amazon ECS deployment](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/configure-ebs-volume.html) — `volumeConfigurations` on `RunTask`, `snapshotId`, and the `deleteOnTermination` standalone-task carve-out
- [EventBridge `EcsParameters` API reference](https://docs.aws.amazon.com/eventbridge/latest/APIReference/API_EcsParameters.html) — no volume-configuration field on an EventBridge ECS target, which is why candidate E needs a direct `ecs:RunTask` caller. The corresponding Terraform gap ([provider issue #43350](https://github.com/hashicorp/terraform-provider-aws/issues/43350)) reflects the API, not the provider. Accessed 2026-09-22
- [GitHub REST: compare two commits](https://docs.github.com/en/rest/commits/commits#compare-two-commits) — 300-file cap on the changed-file list, first page only
- [GitLab REST: repositories](https://docs.gitlab.com/api/repositories/) — `compare_timeout` truncation signal. The per-entry `collapsed` and `too_large` flags are documented for diff-bearing responses generally; confirm they appear on the compare response before relying on them.
