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


# ════════════════════════════════════════════════════════════════════════════
# unwrap_gather_result
# ════════════════════════════════════════════════════════════════════════════

from services.structured_log import unwrap_gather_result  # noqa: E402
import asyncio  # noqa: E402


def test_unwrap_passes_through_plain_value(logger, caplog):
    """Non-exception, non-None value comes back unchanged."""
    out = unwrap_gather_result({"k": 1}, {}, logger, "ctx")
    assert out == {"k": 1}
    assert not caplog.records  # no logging on happy path


def test_unwrap_returns_fallback_on_none(logger, caplog):
    out = unwrap_gather_result(None, {"fallback": True}, logger, "ctx")
    assert out == {"fallback": True}
    assert not caplog.records  # None is a silent skip


def test_unwrap_cancelled_error_is_silent(logger, caplog):
    """CancelledError returns fallback without ANY log — would be shutdown spam."""
    out = unwrap_gather_result(
        asyncio.CancelledError("shutdown"),
        {"fallback": True},
        logger, "ctx", "note",
    )
    assert out == {"fallback": True}
    assert not caplog.records, (
        f"CancelledError should not log, got: "
        f"{[r.getMessage() for r in caplog.records]}"
    )


def test_unwrap_real_exception_logs_at_error_level(logger, caplog):
    """A real BaseException (not Cancelled) logs via log_error."""
    err = ValueError("boom")
    out = unwrap_gather_result(
        err, {"fallback": True},
        logger, "test_ctx", "something failed",
    )
    assert out == {"fallback": True}
    assert len(caplog.records) == 1
    rec = caplog.records[0]
    assert rec.levelname == "ERROR"
    assert rec.structured["context"] == "test_ctx"
    assert rec.structured["type"] == "ValueError"
    assert rec.structured["error"] == "boom"
    assert rec.structured["note"] == "something failed"


def test_unwrap_extra_kwargs_propagate_to_structured_payload(logger, caplog):
    """Arbitrary `**extra` kwargs land on the structured log dict —
    used by callers to pass `symbol=AAPL`, `spec_id=CPIAUCSL`, etc."""
    unwrap_gather_result(
        ValueError("x"), {},
        logger, "ctx", "note",
        symbol="AAPL", spec_id="CPIAUCSL", attempt=3,
    )
    assert len(caplog.records) == 1
    s = caplog.records[0].structured
    assert s["symbol"] == "AAPL"
    assert s["spec_id"] == "CPIAUCSL"
    assert s["attempt"] == 3


def test_unwrap_without_logger_silently_swallows(caplog):
    """logger=None opt-out path — used in tests where observability
    isn't the concern. Real errors still swap for fallback, just no log."""
    out = unwrap_gather_result(ValueError("x"), {"f": 1})
    assert out == {"f": 1}
    assert not caplog.records


def test_unwrap_cancelled_takes_priority_over_base_exception_check():
    """Narrowing-order sanity: CancelledError is both a BaseException
    AND CancelledError. Make sure it hits the silent branch, not
    the log branch. Without a logger we can still observe via
    fallback — same returned value, but if log branch fired we'd
    have seen a log_error attempt."""
    # No logger passed; if the CancelledError branch is first, nothing
    # happens. If the BaseException branch is first and logger is None
    # it still returns fallback — so we can't distinguish by return
    # value alone. Instead, pass a logger and assert no log fires.
    import logging as _l
    lg = _l.getLogger("test.order")
    records: list = []

    class _Handler(_l.Handler):
        def emit(self, record):
            records.append(record)

    h = _Handler()
    lg.addHandler(h)
    lg.setLevel(_l.DEBUG)
    try:
        unwrap_gather_result(asyncio.CancelledError("x"), None, lg, "ctx", "note")
    finally:
        lg.removeHandler(h)
    assert not records
