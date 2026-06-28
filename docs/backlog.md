# Backlog — open items by spec

Single index of **open** work across every spec in `docs/specs/`. Each item
names the spec, the Acceptance Criterion (where one applies), what's blocking
it, and how it gets unblocked. Closed/shipped work is **not** kept here — see
each spec's Changelog and [`product/changelog.md`](product/changelog.md).

This is the tactical **backlog**: per-instance, no pack-side source after first
install — it's yours to curate. It is distinct from the **product roadmap**
(strategy, not a work index) at [`product/roadmap.md`](product/roadmap.md).
"Roadmap" = direction; "backlog" = the work/deferral index.

Deferred acceptance criteria point here by **anchor**: a spec criterion written
`- [ ] <outcome> (deferred: <anchor>)` means `<anchor>` resolves to a heading in
this file (GitHub heading-slug rules — lowercase, spaces become hyphens). The
deferral lives here, version-controlled and greppable, not in a PR comment that
rots. See `CONVENTIONS.md` § 4 (Spec metadata contract).

## How this file is maintained

- Every spec records its own `Status:` field and `Acceptance Criteria`
  checkboxes. This file aggregates the **open** items so they're visible in one
  place — it is not the source of truth.
- When an AC closes or a spec ships, update the spec first, then **remove** the
  now-closed item here in the same change (closed work lives in the spec
  Changelog / `product/changelog.md`, not here).
- When a new spec lands with open ACs, add a section here.
- If an item here is no longer accurate against the underlying spec, trust the
  spec and fix this file.

---

## graph-ingestion-resolution

<!-- Deferral anchors are `###` headings whose GitHub slug equals the
     `(deferred: <anchor>)` token in the spec — the convention this file's header
     describes, and what `lint-spec-status.py` invariant (iv) resolves against. -->

### graph-ingestion-resolution-live-deploy

**AC9 (deferred).** Live-AWS verification of one-command `deploy`/`destroy` — that
`cdk deploy` provisions the slice-1 stack, uploads the corpus snapshot, and runs the
ingestion task once; that `cdk destroy` leaves **no billable resource**; and that the
Budgets alarm actually fires. Blocked on a live AWS account (this PR ships and
synth-tests the IaC, but cannot deploy from CI). Unblocked by a maintainer running
the documented deploy/destroy on a clean account and recording the teardown
smoke-check result.

### graph-ingestion-resolution-full-corpus-eval

**AC5 follow-on (deferred).** Run the resolver eval over a *full* clone of
`kubernetes/community` + `kubernetes/enhancements` (not the pinned real-excerpt
sample committed for CI) and record the precision/recall on the complete
shared-entity set. The committed CI eval (AC5) already runs over real, pinned repo
excerpts — this extends the open confirmation to the whole corpus. Unblocked by
`graphrag resolve-eval --corpus <full-clone>` with a labeled sample of the full
handle/slug set.

## hybrid-orchestration

### hybrid-orchestration-synthesis-edges

**Quality follow-up (AC9 surfaced it; AC9 itself is met).** The live hybrid query works
end-to-end (verified 2026-06-24, 22.7 s), and the seed-and-expand trace *reaches* the owned KEPs
correctly — but the merged context handed to the Bedrock Claude synthesizer lists the graph
**nodes** without their **typed edges** (`OWNS`, `TECH_LEADS`, …). So Claude hedges ("the graph
facts do not include explicit owns edges connecting @thockin to KEPs") instead of stating the
ownership chain, even though the chain is in the trace. The structural win + trace are correct;
this is purely the synthesis-context richness. **Fix:** include the reached edges (src → kind →
dst) in the context `synthesize()` builds — `hybrid_query` already has the hop trace with edge
kinds — so the model can ground the relationship, not just the node set. Verify by re-running the
live entity-led query and confirming the answer names KEP-1880/2086 as `sig-network`-owned.

## vector-rag-baseline

### opensearch-create-index-idempotency-live-confirm

**Deferred live re-confirm (from the `opensearch-create-index-idempotency` fix, spec
`docs/specs/opensearch-create-index-idempotency/`).** The unit regression test
(`test_urllib_client_returns_http_response_on_http_error`) pins the client-level contract
that was the root cause, so the fix ships unit-verified. The full end-to-end re-confirm of
the originally-observed live failure — **deploy** (`apps/infra/scripts/deploy.sh`) → run the
**slice-2 vector smoke probe** (leaves the index behind) → run the **Fargate ingestion task**
(must now complete past `create_index` and write the corpus) → a live `graphrag hybrid-query
--function-url …` returns non-empty `vector:` seeds → **teardown** (`destroy.sh`) — costs a
deploy cycle and is deferred. Blocked on nothing; run when a deploy cycle is otherwise warranted.

### neptune-urllib-http-error-idempotency

**Sibling latent bug (sibling of `opensearch-create-index-idempotency`, deferred from that
fix).** `store/neptune.py`'s `_UrllibClient.request` has the **identical** pattern: it uses
`urllib.request.urlopen`, which raises `urllib.error.HTTPError` on any 4xx/5xx instead of
returning, so `NeptuneGraphStore._request` never applies its uniform status check on an
HTTP-error response. No observed impact today — Neptune has no idempotency-tolerance path (no
`create_index`-style "already-exists is fine" guard) that depends on return-not-raise — so it
was scoped out of the opensearch fix. It matters more here than for opensearch: the neptune
`_UrllibClient` is the **CLI's live Function-URL POST client** and `NeptuneGraphStore`'s
default, i.e. closer to the public boundary. **Fix (when picked up):** mirror the opensearch
fix exactly — catch `urllib.error.HTTPError` **only** (never the broader `URLError`, or TLS /
connection failures get swallowed into a fabricated response) and return
`HttpResponse(status=e.code, text=e.read().decode("utf-8", errors="replace"))`. Add the same
unit test (urllib client returns-not-raises on a 4xx; transport `URLError` still propagates).

### opensearch-urllib-success-decode-and-observability

**Deferred quality findings (from the `opensearch-create-index-idempotency` fix —
quality-engineer review).** Two non-blocking reliability/observability items, both outside that
fix's spec scope (which deliberately scoped the decode tolerance to the *error* path):

1. **Success-path strict decode.** `_UrllibClient.request`'s 2xx branch still does
   `resp.read().decode("utf-8")` (strict). A pathological non-UTF-8 2xx body (a proxy error page
   served as 200, a truncated frame) would raise a bare `UnicodeDecodeError` that bypasses
   `_request`'s uniform `RuntimeError` — the same class of status-masking the error-path
   `errors="replace"` exists to prevent. Effectively never happens (OpenSearch returns UTF-8 JSON),
   hence deferred. **Fix (when picked up):** either apply `errors="replace"` symmetrically on the
   success path, or wrap the success decode so a malformed 2xx surfaces as a `RuntimeError` carrying
   method/URL/status. Same applies to `store/neptune.py`'s client.
2. **No trace on the tolerated already-exists 400.** `create_index`'s swallowed already-exists 400
   leaves no breadcrumb, so an index that "already exists" for the wrong reason is invisible. The
   store layer has no logging today. **Fix (when picked up):** if the store grows debug logging,
   emit a `debug`-level line on `create_index`'s tolerated-4xx branch (not the client) so the
   tolerance is observable without changing the error contract.

## infra-config-separation

### infra-secret-scan-ci

**AC7 follow-on (deferred).** Wire a repo-wide secret scanner (gitleaks / detect-secrets)
**and** `shellcheck -x` into a real CI / pre-commit pipeline. This PR ships the proportionate
in-scope control — a fail-closed `tools/hooks/pre-pr.py` guard over exactly the committed
`config*.env` files it introduces (rejects email-shaped `BUDGET_EMAIL=` and
`arn:aws:iam::<digits>:role/` literals), with a pinned unit test — but the repo has **no CI
at all**, so a repo-wide scanner and a `shellcheck` gate on every later change are a separate
infrastructure concern. Blocked on the repo gaining a CI surface (`.github/workflows` or a
committed pre-commit config). Unblocked by adding a `gitleaks`/`detect-secrets` job + a
`shellcheck` job that runs on push/PR.

## incremental-delta-reingest

### incremental-delta-multicontributed-prop

**AC6 documented limit.** A KEP's `title` is contributed by both its `kep.yaml` and its
README H1. The incremental delta reconciles multiply-contributed props **last-writer-wins** and
cannot subtract a removed contributor's unique prop from a surviving node, so two cases diverge
from a full `--rebuild`: (a) a README-only prose edit that changes the H1 while `kep.yaml` is
unchanged (delta keeps the README's title; rebuild keeps `kep.yaml`'s, resolve's first-writer
order); and (b) deleting **one** co-contributor document (e.g. a README) while another survives
— the surviving node retains a prop only the deleted document set. Both touch only the KEP
`title` in this corpus. AC6's equivalence is scoped to the structural sets
(nodes/edges/chunks/provenance) + props on delta-touched nodes; these multiply-contributed-prop
cases are out of scope (the demo/tests delete or move whole KEP *directories*, not single
co-contributor files, so they reconcile exactly).

### incremental-delta-full-mode-triple-parse

**Perf nit (deferred).** `MODE=full` in `apps/ingestion/entrypoint.py` parses the corpus three
times — `ingest()`, `_vector_dual_write()`, and `build_manifest()`. Harmless on the already-slow
full-ingest path, but wasteful. Deferred rather than fixed now because threading one parsed
`docs` list through all three would touch the slice-1–4 full path that this slice deliberately
keeps byte-unchanged. Unblocked by refactoring the full path to parse once and pass `docs`
(+ `manifest_from_docs`) through, the way the delta path already does.
Resolving it without re-parsing a touched node's *unchanged* co-contributor docs (which would
break the delta-only cost claim for high-fan-in nodes like a SIG) needs per-document property
provenance — deferred. Unblocked by storing per-doc prop provenance, or by making KEP `title`
singly-sourced (README sets it only for legacy KEPs without a `kep.yaml`).

<!-- opencypher-templates: AC9 (live governed-query smoke) was verified live on
     2026-06-25 and the deferral closed — no open items. See
     docs/architecture/deployment-and-verification.md. -->

## parent-child-retrieval

- **bound-knn-k-at-vector-adapters (hardening, not an AC):** the `VectorStore.knn` and
  `ParentChildStore.search` adapters take `k` from the caller and don't clamp it. Not
  exploitable today — the public Function-URL path pins `k=DEFAULT_K=5` and the CLI `k` is
  operator-side — so this is defence-in-depth (OWASP API4:2023 Unrestricted Resource
  Consumption) flagged by the parent-child security review. Unblocked by clamping `k` to a
  sane ceiling **at both vector adapters together** (a cross-cutting one-liner; doing it on
  only the new parent-child adapter would leave the sibling flat adapter inconsistent).

<!-- Add one section per spec with open work, e.g.:

## <spec-name>

- **AC<N> (deferred: <anchor>):** <what's open> — blocked on <X>; unblocked by <Y>.

-->

## global-community-summary

- **share-signed-opencypher-run-helper (hardening, not an AC):** `NeptuneCommunityStore._run`
  (`store/community_neptune.py`) is a verbatim copy of `NeptuneGraphStore._run` (TLS-enforce +
  SigV4 + parameter map), mirroring the parent-child adapter's accepted thin-`_request`
  re-implementation. Correct today, but a future hardening to the canonical signer (a timeout
  change, a header tweak, a credential fix) would not propagate — the established-helper-drift
  class flagged by the global-community-summary security review. Unblocked by extracting a shared
  module-level `_signed_opencypher(endpoint, region, session, http, verify, query, params)` in
  `store/neptune.py` and having both Neptune stores call it (cross-cutting — touches the shipped
  `neptune.py` and its tests).
- **global-community-summary-delta-tier-refresh (security residual, not an AC):**
  communities are detected + summarized + tier-tagged on **full ingest / `--rebuild` only**;
  `MODE=delta` does not recompute them. A delta that *raises* a member entity's visibility
  (`public` → `restricted`) leaves the persisted `Community.tier` stale-low and the summary
  already generated over the then-lower-tier member — a down-classification leak the
  query-time clearance gate cannot catch (flagged by the spec-stage security review). Today
  the demo mitigates by **requiring a full re-ingest after any visibility-label change** (the
  spec Never-do + the explanation doc state this), consistent with the project's posture that
  synthetic visibility labels are a teaching stand-in, not real authz (charter principle 5).
  Unblocked by either (a) recomputing communities on delta when any member visibility
  changed, or (b) the fail-closed alternative — having `MODE=delta` **clear** `Community`
  nodes so global search returns "communities unavailable until next full ingest" rather than
  serving stale-tier summaries. A delta-refresh implementer must handle **both directions**:
  the stale-**low** case above (a leak), and the symmetric stale-**high** case (a member
  visibility *lowered* `restricted` → `public` leaves `Community.tier` over-restrictive — an
  availability/correctness bug, not a leak; same full-re-ingest mitigation today).

## medallion-staging

### medallion-fullrebuild-staging

**Follow-on (deferred), not an AC.** T4b wires `ingest_staged` (the Silver-cached Bronze/Silver/
Gold driver) into `MODE=delta` only. `MODE=full` / `MODE=rebuild` retain their existing passes —
`ingest` + `_vector_dual_write` (incl. the parent-child one-embed-pass) + `_community_writeback` +
`_schema_extraction_writeback` — and still write the v1 `manifest.json`. This deliberately
preserves four behaviors the entrypoint suite pins: full returns `IngestReport`; the parent-child
nested index builds from a single embed pass; community summaries; and the schema-extraction
flag-off/on/raising/trace contract. Consequence: full/rebuild do not populate or read the Silver
cache, so the schema-guided **candidate cache** (and the candidate-cache half of the fingerprint
recompute) is exercised only by the offline T2/T4a tests, not the deployed full path; and the first
`MODE=delta` after a v1 manifest re-embeds all docs once (no prior fingerprints), warming Silver.
Unblocked by routing full/rebuild through `ingest_staged` while preserving those four behaviors —
chiefly building the parent-child index from the Silver-materialized embedded chunks (one embed
pass) and reproducing the schema-extraction flag/trace/resilience semantics through the staged
grounding path — and rewriting the corresponding `test_entrypoint.py` expectations.
