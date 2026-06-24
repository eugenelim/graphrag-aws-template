# Specialist diff review — security-reviewer + quality-engineer

Both returned `Clean — ready to commit.` No Blockers.

## security-reviewer (Clean)
Confirmed all three checks: catch scope is `HTTPError` only (fail-closed; the parametrized
TLS-verification arm guards against broadening to `URLError`); TLS/SigV4 posture untouched
(outside the `try`); no information leak (public Function-URL path returns sanitized envelope via
`query_lambda.py:124` `except Exception`, only exception *type* changes). STRIDE pass clean.

## quality-engineer (Clean — no Blockers)

**1. Success-path strict `decode("utf-8")` can raise on a pathological non-UTF-8 2xx.**
`opensearch.py:69`. Concern, explicitly out of spec scope. RESOLUTION: deferred →
`docs/backlog.md` `### opensearch-urllib-success-decode-and-observability` (behavior change on the
success path the spec didn't authorize; fails the bundled-fixes carve-out).

**2. No trace on the tolerated already-exists 400.** `opensearch.py:70-78`. Nit; the store has no
logging infra. RESOLUTION: deferred → same backlog heading.

**3. AC2 parametrize IDs opaque.** `test_store_opensearch.py:194`. Nit, test-only mechanical.
RESOLUTION: applied — added `ids=["connection-refused", "tls-verify-failed"]`.

Verdict: clean — no open Blockers or Concerns (Concern 1 + Nit 2 deferred with a backlog anchor;
Nit 3 applied).
