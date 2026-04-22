"""Tests for `services.structured_log.safe_gather`.

Covers:
  * Pair-indexed fallbacks — each task gets its own default shape.
  * Mixed CancelledError / Exception / None / valid return values
    unwrap to exactly the expected output.
  * Under-sized `fallbacks` list defaults the overflow to `None`
    (per the contract comment).
  * Only real Exceptions reach the logger — CancelledError is
    silent.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from services.structured_log import safe_gather


# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_safe_gather_pairs_fallbacks_per_task(caplog):
    caplog.set_level(logging.DEBUG, logger="test.safe_gather")
    test_logger = logging.getLogger("test.safe_gather")

    async def ok_task():
        return {"value": 42}

    async def cancelled_task():
        raise asyncio.CancelledError("shutdown")

    async def boom_task():
        raise RuntimeError("provider down")

    async def none_task():
        return None

    overview_fb = {"value": 0}
    earnings_fb = {"quarters": [], "beat_rate": 0}
    insiders_fb = {"trades": []}
    none_fb = {"fallback": True}

    results = await safe_gather(
        ok_task(),
        cancelled_task(),
        boom_task(),
        none_task(),
        fallbacks=[overview_fb, earnings_fb, insiders_fb, none_fb],
        logger=test_logger,
        context="war_room",
    )

    assert results[0] == {"value": 42}
    assert results[1] is earnings_fb    # CancelledError → fallback
    assert results[2] is insiders_fb    # RuntimeError  → fallback
    assert results[3] is none_fb        # None          → fallback

    # CancelledError must NOT log; RuntimeError must emit one ERROR
    # line with the right context + task index.
    err_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(err_records) == 1
    msg = err_records[0].message
    assert "context=war_room" in msg
    assert "type=RuntimeError" in msg
    assert "note=task_2_failure" in msg


@pytest.mark.asyncio
async def test_safe_gather_handles_undersized_fallbacks_list():
    """Extra tasks without a matching fallback get `None`."""
    async def failing():
        raise ValueError("x")

    results = await safe_gather(
        failing(), failing(),
        fallbacks=[{"only": 1}],  # 1 fallback, 2 tasks
    )
    assert results[0] == {"only": 1}
    assert results[1] is None


@pytest.mark.asyncio
async def test_safe_gather_happy_path_no_logs(caplog):
    """All-successful gather makes zero log noise."""
    caplog.set_level(logging.DEBUG, logger="test.safe_gather.happy")

    async def a():
        return 1

    async def b():
        return 2

    results = await safe_gather(a(), b(), fallbacks=[0, 0])
    assert results == [1, 2]
    assert all(r.levelno < logging.ERROR for r in caplog.records)
