# Plan: opensearch-create-index-idempotency

- **Status:** Done
- **Spec:** [`spec.md`](spec.md)

## Strategy

Single logical fix in `packages/graphrag/src/graphrag/store/opensearch.py`: wrap the
`urllib.request.urlopen` call in `_UrllibClient.request()` so an `urllib.error.HTTPError` is
caught and returned as `HttpResponse(status=exc.code, text=exc.read().decode("utf-8"))`. Add
`import urllib.error`. Everything downstream (`_request`'s `200 <= status < 300` check,
`create_index()`'s `resource_already_exists` tolerance) is already correct and stays untouched —
the bug is purely that the error response never reached them.

### Declined patterns

- **Tempted to catch `URLError` for "uniform network-error handling"** — declining; that swallows
  TLS/connection failures into a fabricated response (a security regression). Catch `HTTPError` only.
- **Tempted to also fix `store/neptune.py`'s identical `_UrllibClient`** — declining; out of scope,
  no observed impact (no idempotency-tolerance path), would be opportunistic. Recorded as a backlog
  follow-up instead.
- **Tempted to extract the duplicated `_UrllibClient`/`HttpResponse`/`HttpClient` (neptune +
  opensearch) into a shared module** — declining; a structural refactor, not a bug fix; separate PR.
- **Tempted to harden `create_index`'s `resource_already_exists` substring match into a parsed
  JSON error-type check** — declining; works as-is once the response reaches it; out of scope.

## Tasks

### T1 — `_UrllibClient.request()` returns (not raises) on an HTTP-error response

- **Verification mode:** TDD.
- **Depends on:** none.
- **Tests:** (in `packages/graphrag/tests/test_store_opensearch.py`)
  - `stub: true` — `# STUB: AC1` `test_urllib_client_returns_http_response_on_http_error` —
    monkeypatch `urllib.request.urlopen` to raise `HTTPError(url, 400, "Bad Request",
    Message(), BytesIO(b"resource_already_exists_exception: index exists"))`; assert
    `_UrllibClient().request("PUT", url, data=b"{}", headers={}, verify=True)` returns an
    `HttpResponse` with `status == 400` and `"resource_already_exists" in text`. (Red before fix:
    the `HTTPError` propagates.)
  - `stub: true` — `# STUB: AC2` `test_urllib_client_propagates_transport_errors` — monkeypatch
    `urllib.request.urlopen` to raise `URLError("connection refused")`; assert
    `_UrllibClient().request(...)` re-raises `URLError` (the catch must be narrow). (Green before
    *and* after — pins the security invariant the fix must not break.)
- **Approach:** add `import urllib.error`; wrap the `with urllib.request.urlopen(...)` in
  `try/except urllib.error.HTTPError as exc:` returning `HttpResponse(status=exc.code,
  text=exc.read().decode("utf-8", errors="replace"))`. The `errors="replace"` on the
  **error path only** keeps a non-UTF-8 error body from raising a `UnicodeDecodeError` that would
  mask the real HTTP status (security-reviewer C2); the success path stays strict `"utf-8"` (it
  decodes well-formed OpenSearch JSON).

### T2 — `create_index()` re-raises a non-already-exists 4xx

- **Verification mode:** TDD.
- **Depends on:** none (independent of T1; uses the `RecordingHttp` fake).
- **Tests:**
  - `stub: true` — `# STUB: AC3` `test_create_index_reraises_non_already_exists_4xx` —
    `RecordingHttp([HttpResponse(400, "mapper_parsing_exception: bad mapping")])`; assert
    `_store(http).create_index()` raises `RuntimeError` matching `OpenSearch .* 400`. (Green before
    *and* after — pins the scope of the tolerance so a future broadening of the guard is caught.)
- **Approach:** test-only; no production change beyond T1.

> **AC4 (public-boundary invariant) — no new task.** AC4 is verified by the **existing**
> `test_query_lambda.py::test_internal_failure_returns_sanitized_envelope`, which already pins that
> a body-carrying `RuntimeError` raised below the handler yields a sanitized `{error,
> correlation_id}` envelope with no internal detail. That test raises its `RuntimeError` from the
> Neptune layer, so it verifies the **generic** envelope contract ("any body-carrying `RuntimeError`
> below the handler yields a sanitized envelope"), not the opensearch `knn`/`count` path
> specifically — the guarantee carries to the opensearch path because `query_lambda`'s
> `except Exception` (`query_lambda.py:124`) is provably type-agnostic across both stores. The fix
> only changes the exception *type* on the opensearch read path (`HTTPError` to `RuntimeError`),
> both already caught; confirm the test stays green in GATES.

### T3 — record the backlog transition

- **Verification mode:** goal-based check.
- **Depends on:** none.
- **Done when:**
  - `grep -c '^### opensearch-create-index-idempotency$' docs/backlog.md` returns `0` (the fixed
    triage entry is removed — AC5).
  - A `### opensearch-create-index-idempotency-live-confirm` heading exists (the deferred live
    re-confirm anchor for AC7).
  - A `### neptune-urllib-http-error-idempotency` heading exists, carrying the same
    "catch `HTTPError` only, never `URLError`" constraint forward (security-reviewer Nit 3).
- **Approach:** doc-only edit to `docs/backlog.md`.

## Rollout

Unit-only for this PR. The live re-confirm (deploy → slice-2 probe → Fargate ingestion → live
`hybrid-query` returns non-empty `vector:` seeds → teardown) is **deferred**, tracked under the
`opensearch-create-index-idempotency-live-confirm` backlog heading (AC7); the unit regression test
(T1) pins the client-level contract that was the root cause.
