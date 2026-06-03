"""Tripwire coverage for the 2026-06-03 scheduler-hang RCA.

Observation:
    Preview pod went silent for 8h (06:11 UTC → 14:14 UTC). Last
    log line before death was an apscheduler ``skipped: maximum
    number of running instances reached (1)`` warning for
    ``_check_smart_orders`` — same job that fetches quotes from
    every smart-order symbol SERIALLY with no per-call timeout.

Doctrine pinned here:
    1. ``check_smart_orders`` uses ``asyncio.gather`` for quote
       fetches (parallel, not serial). A single stalled symbol
       cannot block the whole tick.
    2. Each ``get_quote``/``get_crypto_quote`` call is wrapped in
       ``asyncio.wait_for(timeout=6.0)`` so a hung provider socket
       can't extend the tick past the 30s scheduler interval.
"""
from __future__ import annotations

import asyncio
import inspect

from services import smart_order_service


def test_check_smart_orders_uses_asyncio_gather():
    """Doctrine pin: the quote fetch must be concurrent (gather),
    not a per-symbol for-loop. Static check on the source."""
    src = inspect.getsource(smart_order_service.check_smart_orders)
    assert "asyncio.gather" in src, (
        "check_smart_orders must use asyncio.gather for the quote "
        "batch (regression of 2026-06-03 scheduler-hang RCA)"
    )


def test_check_smart_orders_bounds_each_quote_with_wait_for():
    """Doctrine pin: each quote probe is wrapped in asyncio.wait_for
    with an explicit timeout — guards against a hung provider
    socket extending the 30s tick past its window."""
    src = inspect.getsource(smart_order_service.check_smart_orders)
    assert "asyncio.wait_for" in src, (
        "check_smart_orders must bound each quote with "
        "asyncio.wait_for (regression of 2026-06-03 scheduler-hang RCA)"
    )


def test_smart_order_service_imports_asyncio():
    """Symmetric check — module-level import must be present so
    the function above can reach asyncio without a top-of-function
    local import (which would hide an import error at startup)."""
    assert hasattr(smart_order_service, "asyncio")
    assert smart_order_service.asyncio is asyncio
