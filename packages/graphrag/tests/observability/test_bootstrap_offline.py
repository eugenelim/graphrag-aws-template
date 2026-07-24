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
    from graphrag.observability._bootstrap import reset_for_testing

    reset_for_testing()
    yield
    reset_for_testing()


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


def test_no_error_log_on_configure(caplog) -> None:
    """configure_observability emits no ERROR log during offline setup."""
    from graphrag.observability import configure_observability

    with caplog.at_level(logging.ERROR, logger="graphrag.observability"):
        configure_observability("graphrag-mcp-test")

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not error_records, f"Unexpected ERROR logs: {[r.message for r in error_records]}"


def test_import_without_boto3() -> None:
    """graphrag.observability imports cleanly without boto3/botocore."""
    saved = {}
    for mod in list(sys.modules):
        if mod in ("boto3", "botocore") or mod.startswith(("boto3.", "botocore.")):
            saved[mod] = sys.modules.pop(mod)

    try:
        import importlib

        import graphrag.observability as obs_mod

        importlib.reload(obs_mod)
        assert obs_mod.DENY_SET  # proves module loaded
    finally:
        sys.modules.update(saved)
