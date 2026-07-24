"""Structured JSON logging — ADR-0015 item 4.

``configure_json_logging()`` installs a ``pythonjsonlogger.JsonFormatter`` on
the root logger so all output is structured as::

    {"timestamp": "...", "level": "INFO", "name": "...", "message": "...", "request_id": "..."}

``request_id`` is injected by ``_RequestIdFilter``: it reads from Lambda's
``LambdaContext.aws_request_id`` when available; otherwise it generates a
UUID that persists for the process lifetime (or until a new request starts
the next Lambda invocation).

Content-capture
---------------
Question text must never appear in a log field at INFO level or above.
The JSON formatter outputs what the caller passes; the content-capture policy
is a convention enforced by ``spec-mcp-tool-server`` AC5's static linter at
author time.  This module does not inspect log message content — it provides
the structured format, not a runtime content scanner.

Idempotency
-----------
Calling ``configure_json_logging()`` twice is safe; the second call no-ops
when a ``JsonFormatter`` is already installed.
"""

from __future__ import annotations

import logging
import threading
import uuid

logger = logging.getLogger(__name__)

_CONFIGURED = False
_lock = threading.Lock()

# Thread-local request ID (updated per Lambda invocation or per-request in
# long-lived processes).
_request_id_local: threading.local = threading.local()


def configure_json_logging(level: int = logging.INFO) -> None:
    """Install JSON log formatting on the root logger.

    Parameters
    ----------
    level:
        Root logger level.  Defaults to ``logging.INFO``.
    """
    global _CONFIGURED  # noqa: PLW0603
    with _lock:
        if _CONFIGURED:
            return
        try:
            from pythonjsonlogger.jsonlogger import JsonFormatter  # noqa: PLC0415

            root = logging.getLogger()
            root.setLevel(level)

            # Remove any existing handlers to avoid duplicate output
            if root.handlers:
                for h in list(root.handlers):
                    root.removeHandler(h)

            handler = logging.StreamHandler()
            formatter = JsonFormatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                rename_fields={"levelname": "level", "asctime": "timestamp"},
            )
            handler.setFormatter(formatter)
            handler.addFilter(_RequestIdFilter())
            root.addHandler(handler)
            _CONFIGURED = True
            logger.debug("configure_json_logging: JSON formatter installed")
        except ImportError:
            logging.basicConfig(level=level)
            logger.warning("python-json-logger not installed; using basic logging")
            _CONFIGURED = True


def set_request_id(request_id: str) -> None:
    """Set the request ID for the current thread (call at Lambda handler entry)."""
    _request_id_local.value = request_id


def get_request_id() -> str:
    """Return the current request ID, generating one if absent."""
    value = getattr(_request_id_local, "value", None)
    if value is None:
        value = str(uuid.uuid4())
        _request_id_local.value = value
    return value


def reset_for_testing() -> None:
    """Reset the configured flag — **for tests only**."""
    global _CONFIGURED  # noqa: PLW0603
    with _lock:
        _CONFIGURED = False
        root = logging.getLogger()
        for h in list(root.handlers):
            root.removeHandler(h)


class _RequestIdFilter(logging.Filter):
    """Inject ``request_id`` into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if not hasattr(record, "request_id"):
            record.request_id = get_request_id()
        return True
