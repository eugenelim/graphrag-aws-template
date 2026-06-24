# Adversarial diff review — round 1

No Blockers. Two findings, both resolved in the same loop:

**1. AC2 test names TLS-verification coverage it does not exercise.** `packages/graphrag/tests/test_store_opensearch.py:194`. The narrow-catch test injected only a bare `URLError("connection refused")`, not the `ssl.SSLCertVerificationError`-wrapped TLS failure the spec's Boundaries `Never do` explicitly names. Fix: parametrized the test to also inject `URLError(ssl.SSLCertVerificationError(...))` and tightened the docstring. RESOLVED.

**2. Error-path `errors="replace"` decode is untested.** `packages/graphrag/src/graphrag/store/opensearch.py:69,78`. The intentional strict-utf-8 (success) vs replace (error) asymmetry had no test, so a regression dropping `errors="replace"` would pass. Fix: added a trailing `0xff` byte to AC1's error body — strict utf-8 would now raise a UnicodeDecodeError inside the except and fail the test. RESOLVED.

Verdict: No Blockers. Every AC maps to a verifying artifact; catch scope correct (HTTPError only); scope clean (three files, Neptune untouched).
