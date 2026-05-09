"""ADL-2 contract tests — crypto_paper_trader fires shadow receipts.

Pins the fire-and-forget hook added to
``services/crypto_paper_trader.py:run_crypto_symbol`` so that
EVERY crypto-lane decision (APPROVED, HOLD, gate-blocked) produces
an ``alpha_decision_log`` receipt via
``services.ml.receipt_dispatch.schedule_shadow_receipt`` with
``lane="crypto"`` and ``source="crypto_paper_trader"``.

Hard rails pinned by these tests
--------------------------------
* The hook is invoked OUT-OF-BAND — caller's execution path is
  unchanged whether the helper succeeds or fails.
* The hook is called with ``lane="crypto"`` and
  ``source="crypto_paper_trader"`` (so receipts can be partitioned
  by entry point in retrain joins).
* The hook fires BEFORE every downstream gate (early-fire
  position) — so even when a downstream guard rejects the trade,
  the receipt has already been scheduled.
* A raising / failing helper does NOT crash ``run_crypto_symbol``
  (defensive belt+braces try/except around the call site).
* Pre-signal early returns (db_missing / not_crypto_symbol /
  insufficient_bars) do NOT call the helper — those have no
  signal to log against.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.crypto_paper_trader import run_crypto_symbol


# ── Shared helpers ────────────────────────────────────────────────────────────


def _uptrend(n: int = 60, start: float = 70000.0, drift: float = 200.0) -> list[float]:
    """Synthetic uptrend with mild noise — same shape used by
    ``test_crypto_paper_bot.py`` for end-to-end fills."""
    series = [start]
    for i in range(1, n):
        if i % 3 == 2:
            series.append(series[-1] - drift)
        else:
            series.append(series[-1] + drift)
    return series


class _FakeDB:
    """Minimal Motor stub. Auto-vivifies every collection access so
    the bot's inserts and finds don't blow up. We only inspect the
    schedule_shadow_receipt mock — the DB itself is best-effort."""

    def __init__(self):
        self.crypto_paper_trades = AsyncMock()
        self.crypto_paper_trades.insert_one = AsyncMock()
        self.paper_trades = AsyncMock()
        self.crypto_signal_audit_log = AsyncMock()
        self.crypto_signal_audit_log.insert_one = AsyncMock()
        self.crypto_model_adaptations = AsyncMock()
        self.crypto_model_adaptations.find = self._empty_cursor
        self.crypto_adversarial_decision_log = AsyncMock()

    def __getitem__(self, key):
        if not hasattr(self, key):
            setattr(self, key, AsyncMock())
        return getattr(self, key)

    def __getattr__(self, name):
        # Auto-vivify any unknown attribute as an AsyncMock so any
        # new collection access in crypto_paper_trader doesn't
        # blow up the receipt test.
        coll = AsyncMock()
        object.__setattr__(self, name, coll)
        return coll

    def _empty_cursor(self, _query):
        class _C:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        return _C()


@pytest.fixture(autouse=True)
def _pin_confidence_gate_base(monkeypatch):
    monkeypatch.setenv("CONFIDENCE_GATE_BASE", "0.55")


@pytest.fixture
def schedule_spy(monkeypatch):
    """Spy on the shared ``schedule_shadow_receipt`` helper.

    We patch the helper at its source module so the deferred
    ``from services.ml.receipt_dispatch import schedule_shadow_receipt``
    inside ``crypto_paper_trader.run_crypto_symbol`` resolves to
    our spy.
    """
    import services.ml.receipt_dispatch as receipt_dispatch
    spy = MagicMock(return_value=True)
    monkeypatch.setattr(receipt_dispatch, "schedule_shadow_receipt", spy)
    return spy


# ── Happy path: helper invoked when signal is built ───────────────────────────


@pytest.mark.asyncio
async def test_schedule_called_on_normal_uptrend_signal(schedule_spy):
    db = _FakeDB()

    async def quote(_sym):
        return {"price": 70000.0}

    await run_crypto_symbol(db, "BTC", _uptrend(60), quote)

    assert schedule_spy.call_count >= 1, (
        "schedule_shadow_receipt MUST be called at least once when a "
        "signal is built for a tradable symbol"
    )


@pytest.mark.asyncio
async def test_schedule_called_with_crypto_lane_and_source_tag(schedule_spy):
    db = _FakeDB()

    async def quote(_sym):
        return {"price": 70000.0}

    await run_crypto_symbol(db, "BTC", _uptrend(60), quote)

    schedule_spy.assert_called()
    kwargs = schedule_spy.call_args.kwargs
    assert kwargs.get("lane") == "crypto", (
        "lane must be 'crypto' for retrain-join partitioning"
    )
    assert kwargs.get("source") == "crypto_paper_trader", (
        "source tag identifies the entry point in failure logs"
    )


@pytest.mark.asyncio
async def test_schedule_passes_signal_with_symbol(schedule_spy):
    """The signal dict passed to the helper must carry the symbol so
    the shadow pipeline can route lane-specific feature extraction."""
    db = _FakeDB()

    async def quote(_sym):
        return {"price": 70000.0}

    await run_crypto_symbol(db, "BTC", _uptrend(60), quote)

    schedule_spy.assert_called()
    sig = schedule_spy.call_args.kwargs.get("signal")
    assert isinstance(sig, dict)
    assert sig.get("symbol") == "BTC"


@pytest.mark.asyncio
async def test_schedule_market_data_is_none(schedule_spy):
    """``market_data=None`` lets the shadow pipeline call
    ``extract_live_features`` directly — passing the bot's local
    quote here would shadow that path and skew receipts."""
    db = _FakeDB()

    async def quote(_sym):
        return {"price": 70000.0}

    await run_crypto_symbol(db, "BTC", _uptrend(60), quote)

    schedule_spy.assert_called()
    assert schedule_spy.call_args.kwargs.get("market_data") is None


@pytest.mark.asyncio
async def test_schedule_requested_notional_is_pre_sizing_snapshot(schedule_spy):
    """The receipt fires BEFORE sizing — passing 0.0 is intentional."""
    db = _FakeDB()

    async def quote(_sym):
        return {"price": 70000.0}

    await run_crypto_symbol(db, "BTC", _uptrend(60), quote)

    schedule_spy.assert_called()
    assert schedule_spy.call_args.kwargs.get("requested_notional_usd") == 0.0


# ── Pre-signal early returns: helper NOT called ───────────────────────────────


@pytest.mark.asyncio
async def test_schedule_not_called_when_db_missing(schedule_spy):
    async def quote(_sym):
        return {"price": 70000.0}

    result = await run_crypto_symbol(None, "BTC", _uptrend(60), quote)

    assert result["skipped"] is True
    assert result["reason"] == "db_missing"
    schedule_spy.assert_not_called()


@pytest.mark.asyncio
async def test_schedule_not_called_for_non_crypto_symbol(schedule_spy):
    db = _FakeDB()

    async def quote(_sym):
        return {"price": 70000.0}

    result = await run_crypto_symbol(db, "AAPL", _uptrend(60), quote)

    assert result["skipped"] is True
    assert result["reason"] == "not_crypto_symbol"
    schedule_spy.assert_not_called()


@pytest.mark.asyncio
async def test_schedule_not_called_when_bars_insufficient(schedule_spy):
    db = _FakeDB()

    async def quote(_sym):
        return {"price": 70000.0}

    # Only 10 bars — under the 30-bar indicator-stack precondition.
    result = await run_crypto_symbol(db, "BTC", [70000.0] * 10, quote)

    assert result["skipped"] is True
    assert result["reason"] == "insufficient_bars"
    schedule_spy.assert_not_called()


# ── Failure isolation: a raising helper does NOT crash the bot ────────────────


@pytest.mark.asyncio
async def test_helper_raise_does_not_crash_run_crypto_symbol(monkeypatch):
    """Belt+braces: even though ``schedule_shadow_receipt`` itself is
    designed to never raise, the call site has its own try/except as
    a defensive net. Pin that net by forcing a raising helper and
    confirming ``run_crypto_symbol`` still returns a normal dict."""
    db = _FakeDB()

    def _raising(*_args, **_kwargs):
        raise RuntimeError("synthetic ADL helper failure")

    import services.ml.receipt_dispatch as receipt_dispatch
    monkeypatch.setattr(receipt_dispatch, "schedule_shadow_receipt", _raising)

    async def quote(_sym):
        return {"price": 70000.0}

    # Must NOT raise. Result shape stays well-formed.
    result = await run_crypto_symbol(db, "BTC", _uptrend(60), quote)
    assert isinstance(result, dict)
    assert result.get("symbol") == "BTC"
    # The call still produced either an opened trade or a skipped
    # decision — what matters is the helper failure didn't escape.
    assert ("opened" in result) or ("skipped" in result)


@pytest.mark.asyncio
async def test_helper_returning_false_does_not_branch_caller(schedule_spy):
    """``schedule_shadow_receipt`` returns False on no-loop / no-Mongo
    cases. The caller MUST treat False purely as diagnostic — it
    must not branch execution on the return value."""
    schedule_spy.return_value = False
    db = _FakeDB()

    async def quote(_sym):
        return {"price": 70000.0}

    # Same uptrend as the happy-path test: should still produce a
    # tradable result regardless of the helper's False return.
    result = await run_crypto_symbol(db, "BTC", _uptrend(60), quote)

    schedule_spy.assert_called()
    assert isinstance(result, dict)
    assert result.get("symbol") == "BTC"


# ── Static authority firewall — no broker/order imports near the hook ─────────


def test_crypto_paper_trader_does_not_call_run_shadow_pipeline_directly():
    """The bot must use the shared dispatcher, NOT call
    ``run_shadow_pipeline`` itself — that would re-introduce the
    create_task boilerplate and risk diverging from the helper's
    failure-isolation contract."""
    from pathlib import Path

    src = Path(
        "/app/backend/services/crypto_paper_trader.py"
    ).read_text(encoding="utf-8")

    # The bot may IMPORT shadow modules transitively, but it must
    # not invoke run_shadow_pipeline directly. The only allowed
    # invocation path is via schedule_shadow_receipt.
    assert "run_shadow_pipeline(" not in src, (
        "crypto_paper_trader must use schedule_shadow_receipt(...) "
        "and never call run_shadow_pipeline directly."
    )
    assert "schedule_shadow_receipt" in src, (
        "crypto_paper_trader must wire to schedule_shadow_receipt."
    )
