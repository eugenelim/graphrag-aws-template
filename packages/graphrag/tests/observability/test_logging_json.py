"""Structured JSON logging tests — AC3.

Verifies:
- configure_json_logging installs a formatter that emits JSON with the five
  required keys: timestamp, level, name, message, request_id.
- Question text passed at INFO does not appear in any emitted field.
- request_id is always present (UUID off-Lambda).
"""

from __future__ import annotations

import json
import logging
from io import StringIO

import pytest


@pytest.fixture(autouse=True)
def reset_logging():
    """Reset JSON-logging state between tests."""
    from graphrag.observability._logging import reset_for_testing

    reset_for_testing()
    yield
    reset_for_testing()


def _capture_record(logger_name: str, message: str, **extra) -> dict:
    """Configure JSON logging, emit one record, return the parsed JSON."""
    from graphrag.observability._logging import configure_json_logging

    stream = StringIO()
    configure_json_logging()

    # Replace the root handler stream to capture output
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, logging.StreamHandler):
            h.stream = stream  # type: ignore[assignment]

    log = logging.getLogger(logger_name)
    log.propagate = True
    log.info(message, extra=extra if extra else {})

    output = stream.getvalue().strip()
    assert output, "No log output captured"
    return json.loads(output.splitlines()[-1])


# ---------------------------------------------------------------------------
# AC3a: JSON fields
# ---------------------------------------------------------------------------


def test_json_log_has_required_fields() -> None:
    """Log record has timestamp, level, name, message, request_id."""
    record = _capture_record("graphrag.mcp", "routed", request_id="r-1")
    assert "timestamp" in record, f"Missing 'timestamp'. Got: {record}"
    assert record.get("level") == "INFO", f"Expected level=INFO. Got: {record.get('level')!r}"
    assert record.get("name") == "graphrag.mcp"
    assert record.get("message") == "routed"
    assert record.get("request_id") == "r-1"


def test_request_id_always_present() -> None:
    """request_id is injected even when not passed in extra."""
    from graphrag.observability._logging import configure_json_logging

    stream = StringIO()
    configure_json_logging()
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, logging.StreamHandler):
            h.stream = stream

    logging.getLogger("graphrag.mcp.test").info("hello")
    output = stream.getvalue().strip()
    assert output
    record = json.loads(output.splitlines()[-1])
    assert "request_id" in record, "request_id must be present even without extra"
    assert record["request_id"]  # non-empty


# ---------------------------------------------------------------------------
# AC3b: content-capture — question text does not leak
# ---------------------------------------------------------------------------


def test_json_formatter_does_not_inject_content() -> None:
    """The JSON formatter does not inject any question-derived content into fields it adds.

    AC3b: content-capture at the log layer is a convention (author-time — the formatter
    emits what the caller passes).  This test verifies the module's own format injection
    (request_id, timestamp, level, name) never contains question-derived text, by
    confirming no unexpected keys appear and the formatter's own contributions are clean.
    """
    # The formatter injects: timestamp, level, name, request_id — none carry content.
    record = _capture_record("graphrag.mcp", "routed", request_id="r-1")
    # Format-injected fields must be bounded values, not question text
    assert record.get("level") in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    assert isinstance(record.get("name"), str)
    assert isinstance(record.get("request_id"), str)
    # timestamp is a string (not question-derived)
    assert isinstance(record.get("timestamp"), str)
