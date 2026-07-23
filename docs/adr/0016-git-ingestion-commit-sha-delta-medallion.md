# ADR-0016: Git ingestion: commit-SHA delta + medallion over CodePipeline/S3-mirror bronze source

- **Status:** Accepted
- **Date:** 2026-07-23
- **Decision-makers:** eugenelim
- **Supersedes:** [ADR-0007](0007-silver-cache-content-and-config-addressed.md) (Silver-cache S3-key addressing superseded by git commit-SHA artifact keying; the "silver cache" artifact is renamed the Gold layer in medallion terminology)
- **Related:** [RFC-0004 §D6, §Security posture](../rfc/0004-biz-ops-kg-pivot.md); [RFC-0003](../rfc/0003-medallion-staging.md) (medallion precedent); [ADR-0002](0002-ephemeral-vpc-store-topology.md) (no-NAT egress posture carried forward); [ADR-0011](0011-neptune-sparql-rdf-engine-and-text2sparql-guard.md) (Neptune SPARQL endpoint the Gold layer loads into); [ADR-0012](0012-owl-schema-only-and-named-graph-partition.md) (named-graph partition and SHACL gate the Gold layer enforces); `spec-git-ingestion`; `spec-ingestion-extraction-cleanse`

## Decision summary

- **Decision:** We will use git commit-SHA delta as the change signal for the ingestion pipeline, stage documents through a three-layer medallion (Bronze → Silver → Gold), and deliver git content to the VPC-private Fargate task via a CodePipeline/S3-mirror source — no NAT gateway.
- **Because:** Git is the canonical source of truth for the corpus; a SHA diff gives an exact add/modify/delete set without processing unchanged files; and the CodePipeline/S3-mirror pattern keeps the ingestion task fully VPC-private, preserving the no-NAT egress posture ADR-0002's controls depend on.
- **Applies to:** The ingestion pipeline change-detection mechanism, the S3 artifact keying scheme, the ingestion Fargate task's git-remote egress path, and the medallion layer structure.
- **Tradeoff accepted:** Git-tracked corpora only for the primary ingestion path (seam stays pluggable); CodePipeline is an additional AWS service in the ingestion path; Gold artifacts are keyed per commit SHA, so a corpus with frequent churn produces proportionally more Gold artifacts.
- **Revisit if:** An adopter's primary corpus is not git-tracked, or the CodePipeline/S3-mirror latency is unacceptable for the ingestion SLA.

## Context

The existing ingest pipeline (ADR-0007, RFC-0003) hashes raw document bytes to detect changes — a document's S3 artifact key includes a content hash so unchanged bytes skip the Bedrock extraction work. That worked for the Kubernetes demo corpus (stable S3 blobs) but has three problems under the biz-ops pivot:

1. **S3 content-hash is blind to the authoring signal.** Business-operations documents are authored in git. The hash sees the S3 mirror's churn, not the actual author event — a CodePipeline sync without a commit produces a non-empty delta and triggers unnecessary re-ingestion.
2. **The S3 artifact key cannot carry commit context.** PROV-O provenance triples (`biz:gitCommitSHA`, `biz:gitRepo`, `biz:gitPath`) are required by ADR-0012's SHACL shapes for every RDF emission. A content-hash key has no commit-SHA dimension.
3. **The Silver-layer / Gold-layer split does not exist yet.** ADR-0007's "silver cache" holds chunks + embeddings — what the biz-ops pipeline calls the **Gold layer** (RDF triples + vectors, the artifact actually loaded into Neptune and OpenSearch). The new pipeline adds a **Silver layer** (extracted Markdown + cleansing report) between Bronze (raw git) and Gold, following RFC-0003's medallion precedent.

The VPC-private constraint from ADR-0002 (no-NAT, no inbound internet) is still in force. The Fargate ingestion task must reach the git remote — but a NAT gateway on the ingestion subnet would reopen the no-egress posture that the Text2SPARQL guard (ADR-0011) and the OpenSearch kNN access (ADR-0009) depend on. The accepted path is a CodePipeline source action that mirrors the git repo to S3; the Fargate task reads from S3 via a VPC endpoint, never reaching the public internet.

## Decision

> We will use git commit-SHA delta as the change signal: the ingestion task loads the last-ingested SHA from S3, diffs `last_sha..HEAD` via `git diff --name-status`, and processes only the add/modify/delete set. Git content reaches the VPC-private Fargate task via a CodePipeline source action that mirrors the repo to S3 — no NAT gateway. Documents are staged through three medallion layers: Bronze (raw git file), Silver (extracted Markdown + cleansing report), Gold (RDF triples + vectors, SHACL-validated, loaded into Neptune and OpenSearch).

Concretely:

1. **Change signal: git commit-SHA delta.** The Fargate task reads `last_commit_sha` from the S3 manifest, runs `git diff <last_sha>..HEAD --name-status`, and processes only files in the add/modify/delete set. Unchanged files are skipped — no hash recomputation, no re-extraction.

2. **Git remote egress: CodePipeline/S3-mirror source.** A CodePipeline pipeline with a GitHub/CodeStar source action mirrors the repository to an S3 bucket on each push. The Fargate task clones from S3 (using the AWS CLI `s3 cp` + `git bundle` pattern) through a VPC gateway endpoint. No NAT gateway; no public internet egress from the ingestion subnet. This is a security constraint, not a preference.

3. **Medallion layers:**

   | Layer | Contents | Storage | Gate |
   |---|---|---|---|
   | **Bronze** | Raw git file (passed in-memory; not written to S3) | Git repo via S3 mirror | `git diff` — only added/modified files |
   | **Silver** | Extracted Markdown + cleansing report (format router: pandoc / docling / markitdown / Textract) | S3: `silver/<doc_uri>/<commit_sha>.md` + `.report.json` | Minimum content, structure, PII flag |
   | **Gold** | RDF triple graph (Turtle) + chunk vectors; SHACL-validated; keyed by document URI + commit SHA | S3: `gold/<doc_uri>/<commit_sha>.ttl` + `.vectors.json` | SHACL `pyshacl inference="none"` — violations route to `urn:graph:quarantine`, not Neptune |

4. **Artifact keying by commit SHA.** All S3 artifacts are keyed `<doc_uri>/<commit_sha>.<ext>`. This embeds the authoring signal in the key, enables exact provenance (`biz:gitCommitSHA`), and makes Gold artifacts immutable per commit — a re-ingest of the same commit SHA is idempotent.

5. **Neptune/OpenSearch update on delete.** When `git diff` reports a deleted file, the Fargate task issues `DROP GRAPH` (under `ingestion_task_role`, which holds `WriteDataViaQuery`) for the document's triples and deletes the corresponding OpenSearch documents by `doc_uri`. The `mcp_lambda_role` cannot issue `DROP GRAPH` (ADR-0011 read-only guard).

6. **Git-first seam, not git-only.** The ingestion seam (`BronzeSource` protocol) stays pluggable; other source patterns (S3 event-driven, webhook) can implement the same interface. Git-commit-SHA delta is the only implementation in scope for ini-002.

7. **Commit SHA manifest.** The last-ingested SHA is stored at `s3://<bucket>/manifest/last_commit_sha`. On completion, the task writes the new HEAD SHA. On corruption or absence the task falls back to a full rescan (`git diff --name-status 4b825dc..HEAD`, the empty-tree SHA).

## Decision drivers

- **Git is the canonical authoring signal.** SHA delta is the finest-grained, most semantically correct change unit — it captures author intent (the commit boundary), not storage churn (S3 sync events).
- **PROV-O provenance requires the commit SHA.** ADR-0012's `biz:PolicyShape` and `biz:ChunkShape` SHACL shapes mandate `biz:gitCommitSHA` — the artifact key and the SPARQL provenance triple use the same SHA, so they are trivially consistent.
- **No-NAT constraint from ADR-0002 is load-bearing.** The CodePipeline/S3-mirror is not optional hardening — it is required to honour the no-NAT egress posture that ADR-0002 mandates and RFC-0004's security controls extend.
- **Medallion Silver layer is new in ini-002.** ADR-0007's "silver cache" conflated extraction output with the serving artifact. The new Silver layer separates extraction fidelity (can be re-run at any SHA) from the SPARQL/vector serving artifact (Gold), enabling independent re-runs of extraction and RDF emission without re-pulling from git.
- **Idempotency by design.** Keying Gold artifacts by `doc_uri + commit_sha` makes the full pipeline re-runnable at any SHA without side effects beyond the Neptune/OpenSearch upsert — the S3 write is idempotent, and `INSERT DATA` for an existing triple is a no-op in Neptune SPARQL.

## Consequences

**Positive:**
- Git SHA delta is exact — no false positives from S3 sync churn; only genuinely changed files trigger the Bedrock extraction work.
- All Gold artifacts carry the commit SHA; PROV-O triples are trivially consistent with S3 keys and Neptune triples.
- CodePipeline/S3-mirror preserves the VPC-private ingestion posture; no new internet-facing surface.
- The Silver layer enables independent replay of extraction (Silver re-run) vs RDF emission (Gold re-run) at any commit SHA without re-cloning.
- Corpus re-ingest on manifest corruption falls back safely to a full rescan against the empty-tree SHA.

**Negative:**
- Git-tracked corpora only (for the primary ingestion path). A corpus ingested from an S3 bucket or an event stream requires a separate `BronzeSource` implementation.
- CodePipeline is an additional AWS service — an additional cost item and operational surface. On a first push to a new repo the mirror latency adds ~30–60 s before the Fargate task sees the content.
- High-churn corpora (frequent commits touching many files) produce proportionally more Gold S3 artifacts per document. Cleanup policy is out of scope for ini-002; Gold storage cost grows linearly with commit history depth.
- `biz:gitCommitSHA` is a required SHACL property on every document and chunk shape — a non-git source that cannot supply a commit SHA must supply a synthetic SHA-like identifier or the SHACL gate rejects every emission.

**Revisit if:** An adopter's primary corpus is not git-tracked and no synthetic SHA can be supplied; or CodePipeline/S3-mirror latency violates an ingestion SLA that requires sub-minute trigger-to-ingest turnaround.

## Confirmation

- **Mode:** lint/CI + architecture fitness test
- **Signal (git delta unit tests):** the offline CI suite exercises `git diff --name-status` output parsing for add, modify, and delete cases; the full-rescan fallback path (empty-tree SHA) is covered by a fixture with no `last_commit_sha` in the manifest.
- **Signal (medallion layer gate):** a Silver fixture with a missing required field fails the Silver gate and routes to quarantine; a Gold fixture with a missing `biz:gitCommitSHA` fails the SHACL gate (ADR-0012 Confirmation signal).
- **Signal (no-NAT fitness test):** Terraform plan assertion — the ingestion subnet route table has no default route (`0.0.0.0/0`) to an internet gateway or NAT gateway. Fires in CI on any infra change.
- **Owner:** eugenelim; spec owner: `spec-git-ingestion`

## Alternatives considered

- **S3 content-hash delta (do-nothing, ADR-0007 pattern).** Hash raw document bytes; skip if hash unchanged. *Rejected against the PROV-O driver:* content-hash cannot carry commit SHA; provenance triples are inconsistent with the keying scheme; S3 sync events produce false positives when no commit occurred.
- **S3 object-event delta (EventBridge → SQS → Fargate).** Trigger ingestion on S3 `ObjectCreated` events from the CodePipeline mirror. *Rejected:* the S3 event detects mirror churn (a CodePipeline sync), not commit boundaries — the same false-positive problem as content-hash. Also requires SQS and EventBridge in the ingestion path, with no correctness advantage over SHA-keyed polling.
- **Webhook-triggered full rescan.** On each push, re-process the entire corpus. *Rejected:* no delta — every commit re-runs Bedrock extraction on every document regardless of whether it changed; not viable for a corpus of any non-trivial size. Retained as a valid fallback path for small corpora (implemented via the empty-tree SHA in the manifest corruption recovery path).
- **NAT gateway for direct git clone.** Put a NAT gateway on the ingestion subnet so Fargate can `git clone` the remote directly. *Rejected against the no-NAT constraint:* ADR-0002's no-NAT egress posture is carried forward by RFC-0004 (§Security posture) as a load-bearing control; a NAT gateway reopens the unrestricted egress surface that the read-only guard and the OpenSearch ACL controls depend on.

## References

- [RFC-0004 §D6 — Git commit-SHA delta + medallion ingestion](../rfc/0004-biz-ops-kg-pivot.md)
- [RFC-0003 — Medallion staging precedent](../rfc/0003-medallion-staging.md)
- [ADR-0002](0002-ephemeral-vpc-store-topology.md) — no-NAT egress posture (carried forward)
- [ADR-0007](0007-silver-cache-content-and-config-addressed.md) — superseded; Gold layer replaces the "silver cache"
- [ADR-0011](0011-neptune-sparql-rdf-engine-and-text2sparql-guard.md) — `ingestion_task_role` WriteDataViaQuery grant; `DROP GRAPH` on delete
- [ADR-0012](0012-owl-schema-only-and-named-graph-partition.md) — `biz:gitCommitSHA` required by SHACL shapes; quarantine routing
- [biz-ops architecture design.md §Medallion architecture](../architecture/biz-ops-knowledge-graph/design.md)
- `spec-git-ingestion`; `spec-ingestion-extraction-cleanse`
