# Spec: spec-ingestion-extraction-cleanse

- **Status:** Approved <!-- Draft | Approved | Implementing | Shipped | Archived -->
- **Owner:** eugenelim
- **Plan:** [`plan.md`](plan.md)
- **Constrained by:** [ADR-0016](../../adr/0016-git-ingestion-commit-sha-delta-medallion.md) (medallion layers — Bronze/Silver/Gold; format router decision; PII flag-and-surface model); [ADR-0012](../../adr/0012-owl-schema-only-and-named-graph-partition.md) (SHACL gate before Neptune LOAD; `biz:gitCommitSHA` required; quarantine routing on violation; PII stays in natural partition); [ADR-0011](../../adr/0011-neptune-sparql-rdf-engine-and-text2sparql-guard.md) (`ingestion_task_role` WriteDataViaQuery for quarantine INSERT); `spec-git-ingestion` (caller of `process_document()`)
- **Brief:** none
- **Discovery:** none
- **Contract:** none
- **Shape:** pipeline

> **Spec contract:** this document defines what "done" means. The implementing
> PR must match this spec, or update it. Verification must be derivable from it.

## Objective

The `graphrag.ingestion.pipeline` module implements the per-document processing pipeline that transforms raw git file bytes (Bronze) into:

1. **Silver artifact** — extracted Markdown + JSON cleansing report, written to S3 at `silver/<doc_uri>/<sha>.md` and `silver/<doc_uri>/<sha>.report.json`. A document that fails a Silver gate (minimum content, structure) is routed to `urn:graph:quarantine` and produces no Gold artifact.

2. **Gold artifact** — RDF triple graph (Turtle) + chunk embedding vectors, written to S3 at `gold/<doc_uri>/<sha>.ttl` and `gold/<doc_uri>/<sha>.vectors.json`. A document that fails the SHACL gate is routed to `urn:graph:quarantine` and its Gold artifact is not written.

The pipeline exposes a single entry point: `process_document(file_bytes, path, sha, doc_uri) → ProcessResult`. The caller (`spec-git-ingestion`'s `MedallionOrchestrator`) handles Neptune INSERT, OpenSearch upsert, and S3 artifact delivery based on `ProcessResult`.

Key sub-components:

- **`FormatRouter`** — selects the extractor by file extension (`.docx` → pandoc, `.pdf` → docling or Textract, `.pptx` → markitdown, `.xlsx` → markitdown, `.md`/`.txt`/`.rst` → pass-through).
- **`CleansingPipeline`** — runs quality gates (minimum content, structure check, header/footer removal, PII detection, binary residue stripping) and produces the JSON cleansing report.
- **`RDFEmitter`** — classifies the document's `rdf:type`, emits RDF triples in Turtle, and runs the SHACL validation gate (`graphrag.ontology.validate_graph()`).
- **`ChunkEmbedder`** — chunks the cleansed Markdown, calls Bedrock for embeddings, and writes the Gold vectors artifact.

## Boundaries

### Always do

- Write a JSON cleansing report for every document processed, regardless of outcome (pass, Silver gate fail, or SHACL fail). The report records what happened.
- Route SHACL failures to `urn:graph:quarantine` with a structured `biz:quarantineReason` triple containing the SHACL violation report — never silently drop. The Gold artifact is not written.
- Route Silver gate failures (minimum content, structure) to `urn:graph:quarantine` with a `biz:quarantineReason` triple containing which gate failed and the observed value.
- Embed `biz:gitCommitSHA`, `biz:gitRepo`, `biz:gitPath`, and `biz:extractorUsed` as PROV-O triples on every document-class RDF subject (ADR-0012/ADR-0016 requirement; SHACL enforces `biz:gitCommitSHA` on every class).
- Set `biz:hasPII true` on documents where PII is detected; keep the document in its natural partition — PII flag and partition routing are orthogonal dimensions (design.md §PII handling).
- Run `pyshacl.validate()` with `inference="none"` via `graphrag.ontology.validate_graph()` — never bypass the SHACL gate.
- Set `TRANSFORMERS_OFFLINE=1` and `HF_DATASETS_OFFLINE=1` in the task environment to prevent docling from attempting network calls from the private VPC.

### Ask first

- Adding a new file format to `FormatRouter` — each new extractor is a new dependency; the Fargate image size and memory requirements may change.
- Changing the chunking strategy (chunk size, overlap, sentence boundary awareness) — affects embedding quality, OpenSearch index size, and Neptune chunk triple count.
- Replacing docling with another PDF extractor — docling is MIT/Apache 2.0 licensed; alternatives (especially `pymupdf4llm`) may carry AGPL or other license restrictions requiring legal review.
- Enabling AWS Comprehend PII detection by default — Comprehend adds per-request cost and a VPC interface endpoint dependency; currently optional.

### Never do

- Use `pymupdf4llm` as a PDF extractor without legal review — it is AGPL-licensed; closed-source commercial use requires a commercial license.
- Use markitdown for digital PDF extraction — `pdfminer.six`-based PDF handling degrades tables to run-on text for complex layouts (multi-column policies, tabular SOPs); use docling for digital PDFs.
- Route a PII-flagged document to `urn:graph:normative` when its `rdf:type` is descriptive, or to `urn:graph:descriptive` when it is normative — PII sensitivity does not change partition assignment. Routing by PII flag would corrupt the exhaustive-recall contract.
- Write a Gold artifact to S3 for a document that failed the SHACL gate — the Gold path is written only after SHACL confirms `conforms=True`.
- Allow docling to download model weights at runtime — weights must be baked into the Docker image at build time; `TRANSFORMERS_OFFLINE=1` prevents any runtime download attempt.
- Import boto3 or botocore inside `FormatRouter` or `CleansingPipeline` — these components must be testable without AWS credentials.

## Testing Strategy

- **TDD** — `FormatRouter` dispatch (AC1–AC3): fixture bytes for `.docx`, `.pdf`, `.pptx`; assert each routes to the correct extractor class. `.pdf` routing distinguishes digital (docling) from scanned (Textract) based on the absence of a text layer — use a fixture that has no text-layer bytes for the scanned case.
- **TDD** — `CleansingPipeline` Silver gates (AC4, AC8): fixture content < 200 chars → quarantine result with gate name `"min_content"`; fixture with no headings or paragraphs → quarantine with gate `"structure"`.
- **TDD** — PII flagging (AC5): fixture Markdown containing a fixture email address and a fixture phone number → `pii_flagged=True`, `pii_entities_detected >= 2` in cleansing report; partition routing unchanged.
- **TDD** — `RDFEmitter` classification (AC6): fixture SOP document → `rdf:type biz:SOP`; SPARQL SELECT on the emitted Turtle confirms `urn:graph:descriptive` assignment. Fixture Policy document → `rdf:type biz:Policy`; confirms `urn:graph:normative` assignment.
- **TDD** — SHACL gate failure → quarantine (AC7): emit a `biz:Policy` Turtle without `biz:effectiveDate`; assert `graphrag.ontology.validate_graph()` returns `conforms=False`; assert `ProcessResult.outcome == "quarantined"`; assert Gold artifact is not in the result; assert `biz:quarantineReason` triple is in the quarantine INSERT payload.
- **TDD** — PROV-O triples (AC9): emit Turtle for a fixture document; SPARQL SELECT on the emitted graph confirms `biz:gitCommitSHA`, `biz:gitRepo`, `biz:gitPath`, `biz:extractorUsed` present on the document subject.
- **TDD** — cleansing report always written (AC12): for a fixture that passes all gates, confirm report JSON contains `quarantined=false` and lists all passed gates. For a fixture that fails a Silver gate, confirm report JSON contains `quarantined=true` and the failing gate name.
- **Goal-based check** — Fargate task sizing (AC10): `Dockerfile` `CMD` confirms 2048 CPU / 8192 MiB task definition in the Terraform resource; a build-time comment records why 8 GB is the minimum for docling model weights.
- **Goal-based check** — offline env vars (AC11): the Terraform ECS task definition resource sets `TRANSFORMERS_OFFLINE=1` and `HF_DATASETS_OFFLINE=1` in the container environment; confirmed by a `grep` on the Terraform source.

## Acceptance Criteria

- [ ] A `.docx` fixture file is routed to pandoc (`pypandoc.convert_file`) and produces Markdown with at least one heading (e.g. `# Introduction`) and no raw XML residue.
- [ ] A `.pdf` fixture with a text layer is routed to docling and produces Markdown with table structure preserved (a fixture PDF containing a 3-column table produces a Markdown table, not run-on text).
- [ ] A `.pptx` fixture is routed to markitdown and produces Markdown with one `##`-level heading per slide.
- [ ] A document whose extracted text is < 200 characters after stripping artifacts produces `ProcessResult.outcome = "quarantined"` with `biz:quarantineReason = "Silver gate failed: min_content (observed: N chars)"` and no Silver S3 artifact write.
- [ ] A document containing the fixture email `test@example.com` produces `biz:hasPII true` in the emitted RDF and `pii_flagged=True` in the cleansing report. Its `rdf:type` and partition assignment are unchanged by the PII flag.
- [ ] `RDFEmitter` assigns `rdf:type biz:Policy` and partition `urn:graph:normative` to a fixture document whose cleansing report classifier is `"policy"`; assigns `rdf:type biz:SOP` and `urn:graph:descriptive` to a fixture with classifier `"sop"`.
- [ ] A `biz:Policy` emitted without `biz:effectiveDate` triggers `validate_graph()` to return `conforms=False`, and `ProcessResult.outcome = "quarantined"`; no Gold S3 artifact is written; the quarantine Neptune INSERT carries a `biz:quarantineReason` triple with the SHACL violation path.
- [ ] A document with no structural elements (no headings, no paragraph blocks detected) produces `ProcessResult.outcome = "quarantined"` with gate `"structure"`.
- [ ] The emitted Turtle for any processed document contains `biz:gitCommitSHA`, `biz:gitRepo`, `biz:gitPath`, and `biz:extractorUsed` as properties on the document-class RDF subject, confirmed by SPARQL SELECT on the Turtle string.
- [ ] The Terraform ECS task definition resource specifies `cpu = 2048` and `memory = 8192` for the Fargate ingestion task — required for docling model weights (~2.4 GB PyTorch stack).
- [ ] The Terraform ECS task definition environment block contains `{ name = "TRANSFORMERS_OFFLINE", value = "1" }` and `{ name = "HF_DATASETS_OFFLINE", value = "1" }`.
- [ ] A JSON cleansing report is written to S3 at `silver/<doc_uri>/<sha>.report.json` for every processed document, whether it passed all gates or was quarantined. The report JSON contains: `doc_uri`, `sha`, `extractor`, `char_count_raw`, `char_count_clean`, `gates_passed`, `gates_failed`, `pii_flagged`, `pii_entities_detected`, `quarantined`.
- [ ] `ruff check` and `mypy` pass on `packages/graphrag/src/graphrag/ingestion/` with zero errors.

## Assumptions

- Technical: `graphrag.ingestion.pipeline` lives in `packages/graphrag/src/graphrag/ingestion/`; `FormatRouter`, `CleansingPipeline`, `RDFEmitter`, `ChunkEmbedder` are in sub-modules of this package.
- Technical: The docling model weights are baked into the Docker image at build time (via `COPY` in the `Dockerfile`); `TRANSFORMERS_OFFLINE=1` prevents any download attempt at runtime. The Fargate task image is built for `linux/amd64` (confirmed by the live-deploy env workaround in project memory: legacy amd64 cross-build needed for the Fargate compute platform).
- Technical: Textract integration uses a VPC interface endpoint (`com.amazonaws.<region>.textract`) already provisioned in the Terraform infra tier. The endpoint is optional in the test environment — scanned PDF tests that require Textract are tagged `@pytest.mark.live_aws` and skipped in offline CI.
- Technical: AWS Comprehend PII detection is opt-in — enabled when the `comprehend` VPC interface endpoint is provisioned and `ENABLE_COMPREHEND_PII=1` env var is set. The baseline PII detection (regex patterns) runs unconditionally.
- Technical: Chunking uses a sliding window of 512 tokens with 64-token overlap; chunk boundaries are sentence-aligned using a simple regex sentence splitter. The chunking parameters are constants in `_chunk.py`, not env vars — override in tests by patching.
- Technical: `graphrag.ontology.validate_graph()` is the SHACL gate (from `spec-rdf-owl-ontology`, already shipped). The pipeline imports it as a dependency; `rdflib` and `pyshacl` are already in the `[ingest]` dependency group.
- Technical: The document classifier (assigning `rdf:type` from the Silver Markdown) is a heuristic based on filename, directory path, and optional front-matter metadata (`type:` field in the Markdown header). A document with no deterministic signal defaults to `biz:SOP` (descriptive partition). The classifier's logic is unit-tested but its accuracy is the honesty-constraint residual named in ADR-0012 — SHACL validates required fields, not semantic `rdf:type` correctness.
- Technical: PII regex patterns cover: email addresses, phone numbers (E.164 + common US/UK formats), SSNs, credit card numbers, and national IDs (UK NI, Australian TFN). The pattern library is in `_pii.py` and is independently testable.
- Product: The Silver Markdown is the extraction output and is not itself a serving artifact — it is a durable intermediary that enables Silver re-runs (re-extraction from git history) without re-cloning. Gold S3 artifacts are the serving artifacts loaded into Neptune and OpenSearch.
- Product: Header/footer removal uses a position heuristic (first and last N% of page content) plus regex patterns for common artifacts (page numbers "Page N of M", running headers with document title, section footers). It is applied after extraction and before the minimum-content gate, so stripped artifacts don't inflate the char count.
