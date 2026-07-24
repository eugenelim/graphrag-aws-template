# Plan: spec-ingestion-extraction-cleanse

- **Spec:** [`spec.md`](spec.md)
- **Status:** Drafting <!-- Drafting | Executing | Done -->

> **Plan contract:** this is the implementation strategy. Unlike the spec, this
> document is allowed to change as you learn. When it changes substantially
> (a different approach, not just a re-ordering), note why in the changelog
> at the bottom.

## Approach

Six tasks in a linear dependency chain. T1 (`FormatRouter` + extractor stubs) establishes the routing dispatch with no external library dependencies. T2 (`CleansingPipeline`) depends on T1 — it operates on the extraction output and produces the Silver Markdown + cleansing report. T3 (`RDFEmitter` + SHACL gate) depends on T2 — it classifies and emits RDF from the cleansed Markdown; calls `graphrag.ontology.validate_graph()` (already shipped). T4 (`ChunkEmbedder`) depends on T2 — chunks the Silver Markdown and calls Bedrock; parallel with T3 (both read Silver output). T5 (`process_document()` entry point) depends on T3 and T4 — assembles the full pipeline and returns `ProcessResult`. T6 (Fargate task sizing + env-var assertions) is a Terraform/Dockerfile assertion task, independent of T1–T5.

The riskiest part is the docling integration in T1: docling's model weights (~2.4 GB) are baked into the Docker image; unit tests must mock the docling extractor to avoid loading the model in CI. The `FormatRouter` is structured to accept injected extractor instances, enabling the mock substitution.

No AWS credentials are needed for T1–T5 unit tests. Textract tests are tagged `@pytest.mark.live_aws` and skipped offline.

## Constraints

- ADR-0016: Silver artifact `silver/<doc_uri>/<sha>.md` + `silver/<doc_uri>/<sha>.report.json`; Gold artifact `gold/<doc_uri>/<sha>.ttl` + `gold/<doc_uri>/<sha>.vectors.json`.
- ADR-0012: SHACL gate via `graphrag.ontology.validate_graph(inference="none")` before Gold artifact write; SHACL failure → `urn:graph:quarantine`; `biz:gitCommitSHA` required on every emitted triple.
- ADR-0016: PII flag-and-surface; PII-flagged documents stay in their natural partition.
- License: `pymupdf4llm` must not be used — AGPL; use docling (MIT/Apache 2.0) for digital PDFs.
- Fargate sizing: 2048 CPU / 8192 MiB — required for docling model weights.
- Ruff + mypy CI gates must stay green.
- `FormatRouter` and `CleansingPipeline` must be importable without boto3/botocore.

## Construction tests

**T1 (format router):**
- `.docx` → `PandocExtractor` class dispatched.
- `.pdf` (fixture with text layer bytes) → `DoclingExtractor` dispatched.
- `.pdf` (fixture with no text layer) → `TextractExtractor` dispatched.
- `.pptx` → `MarkitdownExtractor` dispatched.
- `.md` → `PassThroughExtractor` (no-op) dispatched.
- Each extractor's `extract(bytes, path)` is mocked; router dispatch is the only assertion.

**T2 (cleansing pipeline):**
- Fixture with 100 chars → quarantine, gate `"min_content"`.
- Fixture with no headings → quarantine, gate `"structure"`.
- Fixture with page headers "Page 1 of 10" → stripped from output.
- Fixture with fixture email `user@example.com` → `pii_flagged=True`.
- Cleansing report JSON written for passing and failing documents.

**T3 (RDF emitter):**
- Fixture classifier returns `"policy"` → `rdf:type biz:Policy`; partition `urn:graph:normative`.
- Fixture with missing `biz:effectiveDate` → SHACL `conforms=False` → quarantine.
- Emitted Turtle contains `biz:gitCommitSHA`, `biz:gitPath`, `biz:extractorUsed`.

**T4 (chunk embedder):**
- Fixture Markdown → chunks split at sentence boundary; chunk count > 0.
- Bedrock `invoke_model` called once per chunk with the chunk text.
- Gold vectors artifact JSON contains `chunks` list with `text`, `embedding`, `chunk_index` per item.

**T5 (entry point):**
- `process_document(bytes, path, sha, doc_uri)` → `ProcessResult(outcome="loaded", silver_artifact_uri=…, gold_artifact_uri=…)` for a passing fixture.
- SHACL failure → `ProcessResult(outcome="quarantined", quarantine_reason=…)`.

**T6 (Terraform assertions):**
- ECS task `cpu=2048`, `memory=8192`.
- `TRANSFORMERS_OFFLINE=1` and `HF_DATASETS_OFFLINE=1` in environment.

## Design (LLD)

### Design decisions

- **Injected extractor instances.** `FormatRouter(extractors: dict[str, Extractor] | None = None)` — if `None`, uses the production defaults; tests inject mock extractors. This avoids patching at the module level and makes the dispatch logic testable without loading docling.
- **Extractor base class / protocol.** All extractors implement `Extractor.extract(file_bytes: bytes, path: str) -> str` (returns Markdown). The `FormatRouter` dispatches by file extension; the extractor handles the conversion.
- **Scanned PDF detection heuristic.** `DoclingExtractor.is_scanned(file_bytes) -> bool`: runs `pdfminer.pdfpage.PDFPage` to check if the page text is below a threshold (< 20 chars per page average). If yes, routes to Textract. This detection runs inside `DoclingExtractor`, not in `FormatRouter`, so the router always dispatches `.pdf` to `DoclingExtractor` first.
- **Classifier is a heuristic, not an ML model.** `RDFEmitter._classify(path, markdown) -> str`: checks `path` components (e.g. `policies/`, `procedures/`, `sops/`) and optional `type:` front-matter field. Defaults to `"sop"` (descriptive) when ambiguous. The honesty-constraint residual (mis-typed documents) is documented in the spec and in ADR-0012; this spec does not address it.
- **PROV-O triples are emitted inline with document triples.** The `RDFEmitter` writes document triples + PROV-O triples into a single `rdflib.Graph` object before Turtle serialisation. PROV-O triples live in the same named graph as the document content — not a separate provenance graph (design.md §Provenance model).
- **Chunking is sentence-aligned, fixed-window.** 512-token window, 64-token overlap, sentence-boundary split using `re.split(r'(?<=[.!?])\s+', text)`. The token count is approximate (character / 4). This is a conservative approach that avoids a tokenizer dependency; the `ChunkEmbedder` can be upgraded to use a proper tokenizer later.

### Data & schema

```python
# graphrag/ingestion/_types.py  (additions to the DeltaEntry types from git-ingestion)

@dataclass
class CleansingReport:
    doc_uri: str
    sha: str
    extractor: str
    char_count_raw: int
    char_count_clean: int
    gates_passed: list[str]
    gates_failed: list[str]
    pii_flagged: bool
    pii_entities_detected: int
    quarantined: bool
    headers_stripped: int
    binary_blocks_stripped: int

@dataclass
class ProcessResult:
    doc_uri: str
    sha: str
    outcome: str           # "loaded" | "quarantined" | "error"
    quarantine_reason: str | None = None
    silver_artifact_uri: str | None = None
    gold_artifact_uri: str | None = None
    cleansing_report: CleansingReport | None = None
```

**S3 artifact key scheme:**
```
silver/<doc_uri_urlencoded>/<sha>.md
silver/<doc_uri_urlencoded>/<sha>.report.json
gold/<doc_uri_urlencoded>/<sha>.ttl         # Turtle RDF + PROV-O
gold/<doc_uri_urlencoded>/<sha>.vectors.json
```
`doc_uri` is URL-encoded (`%3A` for `:`, `%2F` for `/`) to produce a valid S3 key path component.

### Component / module decomposition

```
packages/graphrag/src/graphrag/ingestion/
├── _extraction/
│   ├── __init__.py       # FormatRouter + Extractor protocol
│   ├── _pandoc.py        # PandocExtractor (pypandoc)
│   ├── _docling.py       # DoclingExtractor (docling; scanned detection → Textract fallback)
│   ├── _markitdown.py    # MarkitdownExtractor (pptx, xlsx)
│   ├── _textract.py      # TextractExtractor (AWS Textract via VPC endpoint)
│   └── _passthrough.py   # PassThroughExtractor (md, txt, rst)
├── _cleansing/
│   ├── __init__.py       # CleansingPipeline
│   ├── _gates.py         # min_content, structure, pii, binary_residue gate functions
│   ├── _headers.py       # header/footer removal
│   └── _pii.py           # PII regex patterns + optional Comprehend integration
├── _rdf/
│   ├── __init__.py       # RDFEmitter
│   ├── _classify.py      # document classifier (path heuristic + front-matter)
│   └── _provenance.py    # PROV-O triple emission
├── _embed.py             # ChunkEmbedder — chunk splitter + Bedrock embeddings
└── pipeline.py           # process_document() entry point — assembles the pipeline
```

### Failure, edge cases & resilience

- **Docling OOM on a very large PDF.** The ingestion task is sized at 8 GB; a 200-page PDF with complex layouts may still OOM docling's inference. Mitigated: Textract is the fallback for scanned PDFs (Fargate has no GPU) and docling CPU inference is bounded at ~40 s/doc. If OOM occurs, the Fargate task is marked failed by ECS; the document is NOT quarantined (no quarantine record is written — the task died). The operator resets the manifest SHA to retry. A configurable `DOCLING_PAGE_LIMIT` env var skips the rest of the document if exceeded.
- **Textract response formatting.** Textract returns block-level JSON, not Markdown. The `TextractExtractor` post-processes block output to produce Markdown with `WORD` blocks joined into `LINE` blocks, `LINE` blocks into paragraphs, and `TABLE` blocks rendered as Markdown tables. This post-processor is a non-trivial transformation — it has its own unit tests in `tests/ingestion/test_textract_extractor.py`.
- **Bedrock embedding throttle.** `ChunkEmbedder` retries with exponential backoff (max 3 attempts) on `ThrottlingException`. After 3 failures, the document is quarantined with `quarantine_reason="embedding_throttle"` — a recoverable error, not a structural failure.
- **PII detection regex false positives.** A document may be flagged `biz:hasPII true` due to a false positive (a phone-number-like number in a table). This is accepted — the flag is conservative; the operator reviews quarantined and PII-flagged documents. False negatives (PII not detected by regex) are mitigated by optional Comprehend.

### Quality attributes (NFRs)

- **Offline CI**: `FormatRouter`, `CleansingPipeline`, `RDFEmitter` tests run with no AWS credentials; Textract tests skipped.
- **No AGPL code**: `pymupdf4llm` is absent; CI runs `pip show pymupdf4llm` and fails if the package is installed.
- **Mypy-clean**: full type annotations.
- **Fargate memory-safe**: `DoclingExtractor` is not instantiated (no model load) unless the format is `.pdf`; the model load happens at `DoclingExtractor.__init__()`, so other format routes pay no memory cost.

## Tasks

### T1: `FormatRouter` + extractor dispatch

**Depends on:** none

**Touches:**
- `packages/graphrag/src/graphrag/ingestion/_extraction/`
- `packages/graphrag/tests/ingestion/test_format_router.py`

**Tests (TDD):**
1–5: Router dispatch for `.docx`, `.pdf`, `.pptx`, `.md`, `.xlsx` (mocked extractors; no library loaded).

**Approach:**
1. Define `Extractor` protocol (`extract(bytes, path) -> str`).
2. Implement `FormatRouter` with the extension map; inject mock extractors in tests.
3. Stub real extractor classes (raise `NotImplementedError`); fill in real implementations as sub-tasks once dependencies are confirmed installed.

**Done when:** 5 dispatch tests pass; `ruff check` and `mypy` clean.

---

### T2: `CleansingPipeline` + `CleansingReport`

**Depends on:** T1

**Touches:**
- `packages/graphrag/src/graphrag/ingestion/_cleansing/`
- `packages/graphrag/tests/ingestion/test_cleansing.py`

**Tests (TDD):**
1. `min_content` gate: fixture < 200 chars → quarantine.
2. `structure` gate: no headings → quarantine.
3. Header/footer removal: "Page 1 of 10" stripped.
4. PII detection: fixture email → `pii_flagged=True`, `pii_entities_detected >= 1`.
5. Binary residue: 15% non-UTF-8 content → block stripped; < 15% → passes.
6. Cleansing report JSON serialises to the expected schema.

**Done when:** 6 tests pass; `ruff check` and `mypy` clean.

---

### T3: `RDFEmitter` + SHACL gate

**Depends on:** T2; `graphrag.ontology` (already shipped)

**Touches:**
- `packages/graphrag/src/graphrag/ingestion/_rdf/`
- `packages/graphrag/tests/ingestion/test_rdf_emitter.py`

**Tests (TDD):**
1. Classifier: path `policies/hr.md` → `rdf:type biz:Policy`; partition `urn:graph:normative`.
2. Classifier: path `sops/ir.md` → `rdf:type biz:SOP`; partition `urn:graph:descriptive`.
3. Missing `biz:effectiveDate` on `biz:Policy` → SHACL `conforms=False` → `ProcessResult.outcome="quarantined"`.
4. PROV-O triples present: `biz:gitCommitSHA`, `biz:gitPath`, `biz:extractorUsed` on emitted document subject.
5. PII-flagged SOP stays in `urn:graph:descriptive` (partition unchanged).
6. Emitted Turtle parses correctly with `rdflib.Graph().parse(data=turtle, format="turtle")`.

**Done when:** 6 tests pass; `ruff check` and `mypy` clean.

---

### T4: `ChunkEmbedder`

**Depends on:** T2

**Touches:**
- `packages/graphrag/src/graphrag/ingestion/_embed.py`
- `packages/graphrag/tests/ingestion/test_chunk_embedder.py`

**Tests (TDD):**
1. Fixture Markdown with 3 sentences → at least 1 chunk produced.
2. Bedrock `invoke_model` called once per chunk; mock returns `[0.1, 0.2, …]`.
3. Gold vectors JSON: `chunks` list, each item has `text`, `embedding`, `chunk_index`, `doc_uri`.
4. Bedrock `ThrottlingException` × 3 → `ProcessResult.outcome="quarantined"` with `quarantine_reason="embedding_throttle"`.

**Done when:** 4 tests pass; `ruff check` and `mypy` clean.

---

### T5: `process_document()` entry point

**Depends on:** T1, T2, T3, T4

**Touches:**
- `packages/graphrag/src/graphrag/ingestion/pipeline.py`
- `packages/graphrag/tests/ingestion/test_pipeline.py`

**Tests (TDD):**
1. Full happy path: `process_document(bytes, "policies/hr.md", "abc123", "urn:doc:…")` → `ProcessResult(outcome="loaded")`.
2. Silver gate fail → `ProcessResult(outcome="quarantined")`.
3. SHACL fail → `ProcessResult(outcome="quarantined", quarantine_reason contains SHACL violation path)`.
4. `cleansing_report` present in all outcomes.

**Done when:** 4 tests pass; full test suite green; `ruff check` and `mypy` clean.

---

### T6: Fargate task-definition assertions

**Depends on:** none

**Touches:**
- `apps/infra-tf/tests/test_plan.py`
- `Dockerfile` (verify build target and `COPY` for docling weights)

**Tests (goal-based):**
- `terraform plan` asserts `cpu=2048`, `memory=8192`, `TRANSFORMERS_OFFLINE=1`, `HF_DATASETS_OFFLINE=1`.
- `grep "TRANSFORMERS_OFFLINE" apps/infra-tf/modules/ingestion/*.tf` exits 0.
- `grep "pymupdf4llm" packages/graphrag/pyproject.toml` exits 1 (must be absent).

**Done when:** all 3 goal-based checks pass.

## Rollout

- **Delivery:** no flag — `graphrag.ingestion.pipeline` is a new module.
- **Infrastructure:** Fargate task definition provisioned in `apps/infra-tf/git-ingestion-trigger` (work queue).
- **Deployment sequencing:** depends on `packages/graphrag/shacl-validation` (work queue, which in turn depends on `graphrag.ontology` — already shipped) and on `packages/graphrag/neptune-sparql-store`.

## Risks

- **CodePipeline artifact format.** The "git bundle" assumption may be wrong — CodePipeline's GitHub source action produces a zip archive of the repo tree, not a git bundle. The `GitDeltaReader`'s git-diff step requires a real git repository with commit history. Verify during live-deploy AC: does the CodePipeline artifact include `.git/` history? If not, the delta mechanism requires a different approach (e.g. CodePipeline sends the previous and new archives; the task diffs the file trees directly).
- **docling model weight bake time.** Baking ~2.4 GB model weights into the Docker image adds ~3–5 min to CI build time and significant image size. Use a multi-stage Dockerfile — a `weights-download` stage that pulls the weights (with network access), copied into the final stage (no network access). This pattern is standard for offline-inference containers.
- **Textract post-processor fidelity.** Complex tables in Textract block output (merged cells, column spans) may not render correctly as Markdown tables. The post-processor handles simple tables; complex tables fall back to flat text. Document this as an extraction fidelity limitation in the spec; do not attempt to solve it in this spec.
- **pyproject.toml dependency size.** Adding `docling` (PyTorch stack) to the `[ingest]` group makes `pip install -e ".[dev]"` heavyweight for developers who only work on non-ingestion code. Consider adding a `[ingest-full]` extra that includes docling; the `[ingest]` group includes only the lighter dependencies (pypandoc, markitdown). Record the decision in `packages/graphrag/AGENTS.md` §Dependencies.

## Changelog

- 2026-07-23: initial plan
