"""Tests for services.structured_log."""
from __future__ import annotations

import logging

import pytest

from services.structured_log import log_warning, log_error, log_info


@pytest.fixture
def logger(caplog):
    caplog.set_level(logging.DEBUG, logger="test.structured")
    return logging.getLogger("test.structured")


# ────────────────────────────────────────────────────────────────────────────
# Level routing
# ────────────────────────────────────────────────────────────────────────────

def test_log_warning_emits_at_warning_level(logger, caplog):
    log_warning(logger, {"context": "x", "type": "Y", "error": "z"})
    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == "WARNING"


def test_log_error_emits_at_error_level(logger, caplog):
    log_error(logger, {"context": "x", "error": "boom"})
    assert caplog.records[-1].levelname == "ERROR"


def test_log_info_emits_at_info_level(logger, caplog):
    log_info(logger, {"context": "x", "note": "hi"})
    assert caplog.records[-1].levelname == "INFO"


# ────────────────────────────────────────────────────────────────────────────
# Message format (human-readable tailing)
# ────────────────────────────────────────────────────────────────────────────

def test_stable_key_order_puts_context_type_error_first(logger, caplog):
    log_warning(logger, {
        "extra_field": "z",
        "error": "bad",
        "context": "ctx",
        "type": "ValueError",
    })
    msg = caplog.records[-1].getMessage()
    # context leads, then type, then error, then whatever else.
    assert msg.index("context=") < msg.index("type=")
    assert msg.index("type=") < msg.index("error=")
    assert msg.index("error=") < msg.index("extra_field=")


def test_newlines_stripped_from_string_values(logger, caplog):
    """Multi-line error strings must not break grep-based log parsing."""
    log_warning(logger, {
        "context": "x",
        "error": "line1\nline2\rline3",
    })
    msg = caplog.records[-1].getMessage()
    assert "\n" not in msg
    assert "\r" not in msg
    assert "line1 line2 line3" in msg


def test_empty_payload_does_not_crash(logger, caplog):
    log_warning(logger, {})
    msg = caplog.records[-1].getMessage()
    assert "empty log payload" in msg


# ────────────────────────────────────────────────────────────────────────────
# Structured extra field (log-aggregator path)
# ────────────────────────────────────────────────────────────────────────────

def test_payload_attached_under_structured_extra_key(logger, caplog):
    """Downstream processors (Datadog, Elastic) parse `extra` attrs off
    the LogRecord. We namespace under `structured` to avoid colliding
    with stdlib reserved names like `message`/`levelname`."""
    payload = {"context": "fred_fetch", "type": "CancelledError",
               "error": "task cancel", "spec_id": "CPIAUCSL"}
    log_warning(logger, payload)
    record = caplog.records[-1]
    assert hasattr(record, "structured")
    assert record.structured == payload


def test_structured_extra_uses_caller_logger_name(logger, caplog):
    log_warning(logger, {"context": "x"})
    assert caplog.records[-1].name == "test.structured"
