"""Tests for ``services.ml.receipt_dispatch.schedule_shadow_receipt``.

ADL-1 contract:
  * fire-and-forget — schedule_shadow_receipt returns immediately.
  * Caller's path is NEVER blocked by receipt failure.
  * Receipt failure emits a single ``logger.warning`` and returns
    ``False`` instead of raising.
  * No event loop / no Mongo handle / broken import → ``False``.
  * Sync caller (no running loop) → ``False`` cleanly, no
    side-effects.
  * No broker / kill-switch / RoadGuard imports anywhere in the
    helper module (static check).
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from services.ml import receipt_dispatch
from services.ml.receipt_dispatch import schedule_shadow_receipt


# ── Behaviour pins ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_schedule_returns_true_and_runs_run_shadow_pipeline(monkeypatch):
    """Happy path: helper schedules a task that calls the real shim."""
    called: dict = {}

    async def _spy(db, *, signal, market_data, lane,
                   requested_notional_usd, open_positions,
                   equity_curve, bot_capital):
        called["db"] = db
        called["signal"] = signal
        called["market_data"] = market_data
        called["lane"] = lane
        called["requested_notional_usd"] = requested_notional_usd
        called["open_positions"] = open_positions
        called["equity_curve"] = equity_curve
        called["bot_capital"] = bot_capital
        return {"ok": True}

    import services.ml.shadow_wiring as sw
    monkeypatch.setattr(sw, "run_shadow_pipeline", _spy)

    db = object()
    result = schedule_shadow_receipt(
        db, signal={"symbol": "AAPL"}, market_data=None,
        lane="equity", requested_notional_usd=100.0,
        open_positions=[], equity_curve=[1000.0],
        bot_capital=1000.0, source="test",
    )
    assert result is True

    # Drain pending tasks — they were scheduled fire-and-forget on
    # the running loop. Yield to the loop until the spy ran.
    for _ in range(10):
        if called:
            break
        await asyncio.sleep(0)

    assert called["lane"] == "equity"
    assert called["signal"] == {"symbol": "AAPL"}
    assert called["requested_notional_usd"] == 100.0
    assert called["bot_capital"] == 1000.0


@pytest.mark.asyncio
async def test_schedule_returns_false_when_db_is_none():
    """``db is None`` short-circuits with False, no loop interaction."""
    assert schedule_shadow_receipt(
        None, signal={"symbol": "AAPL"}, market_data=None,
        lane="equity", requested_notional_usd=10.0,
    ) is False


def test_schedule_returns_false_when_no_running_loop(monkeypatch):
    """Sync caller (no event loop) → False. No exception escapes."""
    import services.ml.shadow_wiring as sw

    async def _never_called(*a, **kw):
        raise AssertionError("must not run without a loop")
    monkeypatch.setattr(sw, "run_shadow_pipeline", _never_called)

    assert schedule_shadow_receipt(
        object(), signal={"symbol": "X"}, market_data=None,
        lane="equity", requested_notional_usd=0.0,
    ) is False


@pytest.mark.asyncio
async def test_schedule_returns_false_on_create_task_failure(monkeypatch, caplog):
    """A failure inside ``loop.create_task`` is caught + logged."""
    closed_coros: list = []

    class _BoomLoop:
        def create_task(self, coro):
            # Close the coroutine cleanly so the test doesn't trigger
            # an "unawaited coroutine" RuntimeWarning. Real-world
            # failure paths get the same treatment from asyncio itself
            # when the task is GC'd.
            closed_coros.append(coro)
            coro.close()
            raise RuntimeError("create_task is broken")

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: _BoomLoop())

    with caplog.at_level(logging.WARNING):
        result = schedule_shadow_receipt(
            object(), signal={"symbol": "AAPL"}, market_data=None,
            lane="equity", requested_notional_usd=0.0, source="boom-test",
        )
    assert result is False
    assert any("[adl-receipt]" in r.message for r in caplog.records)
    assert any("boom-test" in r.message for r in caplog.records)
    # Ensure the coroutine was actually constructed (i.e. the helper
    # reached create_task before failing).
    assert len(closed_coros) == 1


def test_schedule_handles_broken_import(monkeypatch, caplog):
    """If ``services.ml.shadow_wiring`` somehow fails to import, the
    helper logs and returns False — never raises."""
    import sys
    real = sys.modules.pop("services.ml.shadow_wiring", None)
    sys.modules["services.ml.shadow_wiring"] = None  # type: ignore[assignment]
    try:
        with caplog.at_level(logging.WARNING):
            result = schedule_shadow_receipt(
                object(), signal={"symbol": "X"}, market_data=None,
                lane="equity", requested_notional_usd=0.0,
                source="import-broken",
            )
        assert result is False
        assert any("shadow_wiring import failed" in r.message
                   for r in caplog.records)
    finally:
        sys.modules.pop("services.ml.shadow_wiring", None)
        if real is not None:
            sys.modules["services.ml.shadow_wiring"] = real


@pytest.mark.asyncio
async def test_schedule_does_not_block_caller_when_pipeline_raises(monkeypatch):
    """Even if ``run_shadow_pipeline`` raises asynchronously, the
    helper returns True (the schedule succeeded) and the caller
    never sees the inner exception."""
    async def _raises(*a, **kw):
        raise RuntimeError("inner failure")

    import services.ml.shadow_wiring as sw
    monkeypatch.setattr(sw, "run_shadow_pipeline", _raises)

    result = schedule_shadow_receipt(
        object(), signal={"symbol": "X"}, market_data=None,
        lane="equity", requested_notional_usd=0.0,
        source="raises-test",
    )
    # schedule succeeded; the inner exception is swallowed by the
    # task's own try/except inside run_shadow_pipeline.
    assert result is True
    # Yield so the task runs and any unhandled exception would surface
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_schedule_is_byte_equivalent_to_legacy_inline_pattern(monkeypatch):
    """Pin the kwargs the helper passes through. Mirrors what the
    pre-ADL-1 inline ``asyncio.create_task(run_shadow_pipeline(...))``
    in trading_bot_service.execute_signal used to pass."""
    captured: dict = {}

    async def _spy(db, *, signal, market_data, lane,
                   requested_notional_usd, open_positions,
                   equity_curve, bot_capital):
        captured.update(
            db=db, signal=signal, market_data=market_data,
            lane=lane, requested_notional_usd=requested_notional_usd,
            open_positions=open_positions, equity_curve=equity_curve,
            bot_capital=bot_capital,
        )

    import services.ml.shadow_wiring as sw
    monkeypatch.setattr(sw, "run_shadow_pipeline", _spy)

    sentinel_db = object()
    sentinel_signal = {"symbol": "BTC", "direction": "BUY"}
    sentinel_md = {"price": 100.0}
    sentinel_pos = [{"symbol": "X", "size_usd": 50}]
    sentinel_curve = [1000.0, 1010.0]

    schedule_shadow_receipt(
        sentinel_db, signal=sentinel_signal, market_data=sentinel_md,
        lane="crypto", requested_notional_usd=42.0,
        open_positions=sentinel_pos, equity_curve=sentinel_curve,
        bot_capital=750.0, source="byte-equivalence",
    )
    for _ in range(10):
        if captured:
            break
        await asyncio.sleep(0)

    assert captured["db"] is sentinel_db
    assert captured["signal"] is sentinel_signal
    assert captured["market_data"] is sentinel_md
    assert captured["lane"] == "crypto"
    assert captured["requested_notional_usd"] == 42.0
    assert captured["open_positions"] is sentinel_pos
    assert captured["equity_curve"] is sentinel_curve
    assert captured["bot_capital"] == 750.0


# ── Static authority firewall ──────────────────────────────


_HELPER_PATH = Path(receipt_dispatch.__file__)


def test_helper_has_no_authority_imports():
    """ADL-1 contract: no broker / executor / kill-switch / RoadGuard
    imports — receipt scheduling is observation only."""
    src = _HELPER_PATH.read_text()
    forbidden = [
        r"\bfrom services\.broker_service\b",
        r"\bfrom services\.trading_bot_service\b",
        r"\bfrom services\.crypto_paper_trader\b",
        r"\bfrom services\.paper_trading_service\b",
        r"\bfrom routes\.broker\b",
        r"\bfrom services\.ml\.executors\b",
        r"\bfrom services\.ml\.roadguard\b",
        r"\bfrom services\.ml\.fast_veto\b",
        r"\bfrom services\.ml\.pipeline\b",
        r"\bfrom services\.ml\.broker_wire\b",
        r"\bfrom ai_core\.kill_switch\b",
        r"\bfrom services\.alpha_decision_log\b",
        # Mongo write verbs
        r"\.insert_one\(", r"\.insert_many\(",
        r"\.update_one\(", r"\.update_many\(",
        r"\.replace_one\(", r"\.delete_one\(", r"\.delete_many\(",
        r"\.drop\(", r"\.bulk_write\(",
        # Decision-issuing markers
        r'"BUY"', r"'BUY'", r'"SELL"', r"'SELL'",
        r"Verdict\.BUY", r"Verdict\.SELL",
        r"\.place_order\(", r"set_active\(", r"promote_now\(",
    ]
    for pattern in forbidden:
        assert not re.search(pattern, src), (
            f"receipt_dispatch.py contains forbidden marker: {pattern}"
        )


def test_helper_has_no_env_mutation():
    """Helper must not read or mutate env. Pinned by static check."""
    src = _HELPER_PATH.read_text()
    assert "os.environ" not in src
    assert "os.getenv" not in src
    assert "setenv" not in src
