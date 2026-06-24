# Spec: opensearch-create-index-idempotency

- **Status:** Shipped
- **Shape:** service (store-layer HTTP client bug fix)
- **Mode:** full (risk trigger fired — the change touches the network-I/O error-handling path of a SigV4/TLS HTTP client; see Assumptions)
- **Plan:** [`plan.md`](plan.md)
- **Contract:** the `HttpClient` Protocol in `store/opensearch.py` (`request(...) -> HttpResponse`) and `OpenSearchVectorStore.create_index()`'s documented idempotency
- **Constrained by:** [`vector-rag-baseline`](../vector-rag-baseline/spec.md) (slice-2 AC4 — the OpenSearch k-NN adapter this fixes)

> A latent-bug fix scoped to **`packages/graphrag/src/graphrag/store/opensearch.py`** and its
> test. The default `_UrllibClient.request()` lets `urllib.error.HTTPError` propagate on a 4xx/5xx
> instead of returning an `HttpResponse(status, text)`. Because `HTTPError` is an `OSError`
> subclass (not a `RuntimeError`), `_request`'s uniform status check is bypassed and
> `create_index()`'s documented already-exists tolerance (`except RuntimeError`,
> `"resource_already_exists"`) never fires — so the Fargate ingestion task aborts with an uncaught
> `HTTPError 400` whenever the index already exists. `Depends on: none`.

## Objective

`OpenSearchVectorStore.create_index()` promises idempotency ("an already-exists 400 is fine") so
the corpus ingestion can run after the slice-2 vector smoke probe, which leaves the index behind
(it deletes its *doc*, not the index). Today that promise is hollow: the default urllib client
raises `HTTPError` on the already-exists 400 before the response ever reaches the status check that
would turn it into the `RuntimeError` `create_index()` catches. Observed live (2026-06-24): the
Fargate ingestion task aborted at `create_index` before writing any corpus chunk.

The fix makes `_UrllibClient.request()` honour the `HttpClient` Protocol's real contract —
**return, don't raise, on an HTTP-error response** — so `_request` applies its uniform status check
and the documented already-exists tolerance actually works. The fix must catch `HTTPError` only (a
received HTTP response), never the broader `URLError`, so transport failures (connection refused,
TLS verification failure) still surface loudly.

## Boundaries

### Always do

- **Honour the `HttpClient` Protocol's return-not-raise contract on 4xx/5xx.** The injectable
  fakes (`RecordingHttp`) already return `HttpResponse(status, text)` for a 4xx; the real
  `_UrllibClient` must match them so `_request` is the single place status is interpreted.
- **Catch `urllib.error.HTTPError` specifically.** It is a received HTTP response carrying a
  status and body. Transport-level failures (`URLError` without a status, TLS/connection errors)
  must continue to propagate uncaught — they are not a server response.

### Never do

- **Never broaden the catch to `URLError` or a bare `except`.** That would swallow TLS-verification
  and connection failures into a fabricated response, hiding a security-relevant failure behind a
  fake status.
- **Never weaken `create_index()`'s guard** so it tolerates anything other than
  `resource_already_exists`. Other 4xx/5xx (mapping errors, auth failures) must still raise.
- **Never touch the infra scripts** (`apps/infra/scripts/`) or the Neptune adapter in this change.

### Ask first

- **Widening the fix to `store/neptune.py`'s identical `_UrllibClient`.** Neptune shares the same
  `urlopen`-raises-on-4xx pattern (same *class* of latent bug) but has no idempotency-tolerance
  path that depends on return-not-raise, so it has no observed impact. This PR records it as a
  follow-up heading in `docs/backlog.md` (`neptune-urllib-http-error-idempotency`, carrying the
  same "catch `HTTPError` only, never `URLError`" constraint — Neptune's client is closer to the
  public Function-URL boundary); it is **not** fixed here.

## Testing Strategy

TDD (the contract is a pure-ish HTTP-client behaviour, mockable without a live domain). Three
construction tests, all reusing the existing fakes / `monkeypatch` in
`tests/test_store_opensearch.py`:

1. The real `_UrllibClient.request()` **returns** an `HttpResponse` (status + body) when
   `urllib.request.urlopen` raises an `HTTPError` on a 4xx — the regression test that closes the
   gap the fake masked (the fake already returned-not-raised, so it never exercised the divergence).
2. The real `_UrllibClient.request()` still **raises** when `urlopen` raises a non-HTTP `URLError`
   (transport failure) — pins the security invariant that the catch is narrow.
3. `create_index()` **re-raises** a non-already-exists 4xx (e.g. a mapping error) — pins that the
   idempotency tolerance is scoped to `resource_already_exists` only.

The existing `test_create_index_is_idempotent_on_already_exists` already covers the swallow path
(given a returning client) and stays. Gates: `ruff check`, `mypy`, `pytest` (or
`tools/hooks/pre-pr.py`). Optional live confirm (costs a deploy) is out of scope for this PR.

## Acceptance Criteria

- [x] `_UrllibClient.request()` returns an `HttpResponse(status=e.code, text=...)` for an HTTP-error
      (4xx/5xx) response instead of letting `HTTPError` propagate (test 1).
- [x] A transport-level `URLError` (no HTTP status) still propagates uncaught from
      `_UrllibClient.request()` (test 2).
- [x] `create_index()` swallows an already-exists 400 (existing test) but re-raises any other 4xx as
      a `RuntimeError` (test 3).
- [x] The public Function-URL information-disclosure boundary holds. The opensearch `_UrllibClient`
      **is** reachable from the public IAM-auth Function URL (`query_lambda.py:99` constructs
      `OpenSearchVectorStore` with no `http_client`, so `knn`/`count` use it). After the fix a
      store-side 4xx/5xx on that read path returns an `HttpResponse` and `_request` raises a
      body-carrying `RuntimeError` — exactly where the pre-fix code raised an `HTTPError`; both are
      caught identically by `query_lambda`'s `except Exception` (`query_lambda.py:124`), which
      returns a sanitized `{error, correlation_id}` envelope carrying no `resp.text`. The
      sanitized-envelope invariant in [`docs/architecture/security.md`](../../architecture/security.md)
      ("Public Function URL → error responses") is preserved with no behavioural regression
      (only the exception *type* on that path changes, `HTTPError → RuntimeError`, both already
      caught). Verified by the existing `test_internal_failure_returns_sanitized_envelope`.
- [x] Gates green: `ruff check`, `mypy`, `pytest` for the `graphrag` package.
- [x] The `opensearch-create-index-idempotency` entry is removed from `docs/backlog.md` and a
      Neptune sibling-bug follow-up recorded there.
- [ ] Live re-confirm (deferred: opensearch-create-index-idempotency-live-confirm) — deploy →
      slice-2 vector probe → Fargate ingestion → live `hybrid-query` returns non-empty `vector:`
      seeds → teardown.

## Assumptions

- **Mode: full.** The change touches the network-I/O error-handling path of a SigV4/TLS HTTP
  client — a security-boundary risk trigger. The genuine risk is catch-scope (HTTPError vs URLError),
  which a security-reviewer pass confirms.
- The opensearch `_UrllibClient` is the default HTTP client for `OpenSearchVectorStore`, reached on
  **two** paths: the Fargate ingestion task (the bug's impact site — `create_index`/`index_chunk`)
  **and** the public IAM-auth query Function URL via `query_lambda.py:99` (`knn`/`count`). On the
  Function-URL path the fix changes the exception type for a store-side 4xx/5xx from `HTTPError` to
  a body-carrying `RuntimeError`; both are caught identically by `query_lambda`'s `except Exception`
  (`query_lambda.py:124`), which returns a sanitized `{error, correlation_id}` envelope — so no body
  text crosses the public boundary and there is no behavioural regression there (AC4). The
  `store/neptune.py` `_UrllibClient` is a *separate* client (the CLI's live Function-URL POST client
  and `NeptuneGraphStore`'s default); it shares the same latent bug and is deferred — see Ask first.
- `HTTPError` exposes `.code` (int status) and `.read()` (the response body bytes) — it is itself a
  response-like object — so the returned `HttpResponse` can be built from the caught exception.
