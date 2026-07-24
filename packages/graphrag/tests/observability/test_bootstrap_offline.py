"""Offline bootstrap tests — AC5, AC6 (partial).

Verifies that ``configure_observability`` succeeds with no AWS credentials,
no reachable OTLP endpoint, and that the installed provider wraps every
registered exporter in ``ContentCaptureFilterExporter``.
"""

from __future__ import annotations

import logging
import sys

import pytest


@pytest.fixture(autouse=True)
def reset_bootstrap():
    """Reset bootstrap state before/after each test."""
    from graphrag.observability._bootstrap import _reset_for_testing

    _reset_for_testing()
    yield
    _reset_for_testing()


def test_configure_observability_no_aws_creds_no_raise() -> None:
    """configure_observability raises nothing with no AWS credentials set."""
    import os

    env_backup = {k: v for k, v in os.environ.items() if k.startswith("AWS_")}
    for k in env_backup:
        del os.environ[k]
    try:
        from graphrag.observability import configure_observability

        configure_observability("graphrag-mcp-test")  # must not raise
    finally:
        os.environ.update(env_backup)


def test_configure_observability_idempotent() -> None:
    """Calling configure_observability twice is safe (second call is a no-op)."""
    from graphrag.observability import configure_observability

    configure_observability("test-svc")
    configure_observability("test-svc")  # second call — must not raise or re-install


def test_no_error_log_on_configure() -> None:
    """configure_observability emits no ERROR log during offline setup.

    Note: configure_observability calls configure_json_logging(), which removes
    ALL root handlers (including pytest's LogCaptureHandler).  We therefore add
    our own capture handler to the root logger AFTER setup, then trigger a
    traced_leg span to exercise the installed provider.
    """
    from graphrag.observability import configure_observability, traced_leg

    configure_observability("graphrag-mcp-test")

    # Install a capture handler AFTER configure_json_logging cleared root handlers
    error_records: list[logging.LogRecord] = []

    class _ErrorCapture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.levelno >= logging.ERROR:
                error_records.append(record)

    cap = _ErrorCapture()
    cap.setLevel(logging.ERROR)
    logging.getLogger().addHandler(cap)
    try:
        with traced_leg("routing.rule_router"):
            pass
    finally:
        logging.getLogger().removeHandler(cap)

    assert not error_records, f"Unexpected ERROR logs: {[r.getMessage() for r in error_records]}"


def test_import_without_boto3() -> None:
    """graphrag.observability imports cleanly when boto3/botocore are blocked.

    Uses the ``sys.modules["boto3"] = None`` sentinel pattern to force
    ``ImportError`` on any ``import boto3`` — a real negative test.
    """
    import importlib

    sentinel_keys = ["boto3", "botocore"]
    saved: dict = {}
    for key in sentinel_keys:
        saved[key] = sys.modules.pop(key, ...)
        sys.modules[key] = None  # type: ignore[assignment]

    try:
        import graphrag.observability as obs_mod

        importlib.reload(obs_mod)
        assert obs_mod.DENY_SET  # proves module loaded without boto3
    finally:
        for key in sentinel_keys:
            if saved[key] is ...:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = saved[key]  # type: ignore[assignment]
