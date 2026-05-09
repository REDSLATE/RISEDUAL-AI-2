"""ADL-4 contract tests — options_paper agent fires shadow receipts.

Pins the fire-and-forget hook added to
``services/trading_agents/options_paper.py:run`` so EVERY options
candidate with a fresh signal produces an ``alpha_decision_log``
receipt via ``services.ml.receipt_dispatch.schedule_shadow_receipt``
with ``lane="options"`` and ``source="options_paper_bot"``.

Hard rails pinned by these tests
--------------------------------
* APPROVED candidates (conf ≥ 0.72) produce a receipt.
* NO_TRADE / blocked candidates (conf < 0.72) ALSO produce a
  receipt — the decision object exists at this point.
* Pre-signal early returns produce ZERO receipts:
    - ``db is None``
    - ``kill_switch_blocks()`` returns True
    - ``_score_ticker`` returns ``None`` (no fresh snapshot)
* The hook is fire-and-forget — a raising helper does NOT crash
  ``run``.
* ``lane`` is ALWAYS ``"options"``.
* ``source`` is ALWAYS ``"options_paper_bot"``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.trading_agents import options_paper as opt_mod


@pytest.fixture
def schedule_spy(monkeypatch):
    """Spy on the shared ``schedule_shadow_receipt`` helper.

    Patches the helper at its source module so the deferred import
    inside ``run`` resolves to our spy.
    """
    import services.ml.receipt_dispatch as receipt_dispatch
    spy = MagicMock(return_value=True)
    monkeypatch.setattr(receipt_dispatch, "schedule_shadow_receipt", spy)
    return spy


@pytest.fixture(autouse=True)
def _shrink_watchlist_to_one(monkeypatch):
    """Run options_paper.run against a single-symbol watchlist so
    the receipt-spy assertions are deterministic. Production
    behaviour is unchanged — module-level constant is restored
    when the monkeypatch tears down."""
    monkeypatch.setattr(opt_mod, "_WATCHLIST", ["AAPL"])


@pytest.fixture(autouse=True)
def _kill_switch_inactive(monkeypatch):
    """Default: kill switch is OFF so the per-ticker loop runs.
    Tests that exercise the kill-switch path override this."""
    async def _inactive():
        return False
    monkeypatch.setattr(opt_mod, "kill_switch_blocks", _inactive)


@pytest.fixture
def stub_quote_ok(monkeypatch):
    async def _quote(_ticker):
        return 100.0
    monkeypatch.setattr(opt_mod, "_fetch_quote", _quote)


@pytest.fixture
def stub_record_paper_trade(monkeypatch):
    """Replace record_paper_trade with a tracking AsyncMock."""
    spy = AsyncMock(return_value="trade-id-123")
    monkeypatch.setattr(opt_mod, "record_paper_trade", spy)
    return spy


def _make_score_stub(direction: str, confidence: float | None):
    """Build an async stub for _score_ticker that returns the given
    (direction, confidence) tuple — or None when confidence is None.
    """
    async def _stub(_ticker, _db):
        if confidence is None:
            return None
        return direction, confidence
    return _stub


# ── APPROVED path ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approved_high_conviction_schedules_receipt(
    monkeypatch, schedule_spy, stub_quote_ok, stub_record_paper_trade,
):
    """Conf ≥ 0.72 → trade fires AND receipt is scheduled."""
    monkeypatch.setattr(opt_mod, "_score_ticker", _make_score_stub("UP", 0.85))
    db = MagicMock()  # opaque — never indexed by run()

    await opt_mod.run(db)

    schedule_spy.assert_called_once()
    stub_record_paper_trade.assert_awaited()


@pytest.mark.asyncio
async def test_approved_path_uses_options_lane_and_source(
    monkeypatch, schedule_spy, stub_quote_ok, stub_record_paper_trade,
):
    monkeypatch.setattr(opt_mod, "_score_ticker", _make_score_stub("UP", 0.85))
    db = MagicMock()

    await opt_mod.run(db)

    kwargs = schedule_spy.call_args.kwargs
    assert kwargs.get("lane") == "options"
    assert kwargs.get("source") == "options_paper_bot"


@pytest.mark.asyncio
async def test_approved_signal_payload(
    monkeypatch, schedule_spy, stub_quote_ok, stub_record_paper_trade,
):
    monkeypatch.setattr(opt_mod, "_score_ticker", _make_score_stub("UP", 0.85))
    db = MagicMock()

    await opt_mod.run(db)

    sig = schedule_spy.call_args.kwargs.get("signal")
    assert isinstance(sig, dict)
    assert sig["symbol"] == "AAPL"
    assert sig["direction"] == "UP"
    assert sig["confidence"] == pytest.approx(0.85)
    assert sig["strategy"] == "options_paper"
    assert sig["source_layer"] == "options_paper_agent"


@pytest.mark.asyncio
async def test_approved_passes_options_position_notional(
    monkeypatch, schedule_spy, stub_quote_ok, stub_record_paper_trade,
):
    """Receipts get the agent's _POSITION_USD as the requested
    notional — useful for downstream sizing-vs-realized analysis."""
    monkeypatch.setattr(opt_mod, "_score_ticker", _make_score_stub("UP", 0.85))
    db = MagicMock()

    await opt_mod.run(db)

    notional = schedule_spy.call_args.kwargs.get("requested_notional_usd")
    assert notional == pytest.approx(opt_mod._POSITION_USD)


# ── NO_TRADE / below-conviction path ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_below_conviction_still_schedules_receipt(
    monkeypatch, schedule_spy, stub_quote_ok, stub_record_paper_trade,
):
    """Conf below 0.72 → NO trade, but receipt MUST still fire so
    the retrain join sees the decision."""
    monkeypatch.setattr(opt_mod, "_score_ticker", _make_score_stub("UP", 0.50))
    db = MagicMock()

    await opt_mod.run(db)

    schedule_spy.assert_called_once()
    # No trade was opened.
    stub_record_paper_trade.assert_not_awaited()
    sig = schedule_spy.call_args.kwargs["signal"]
    assert sig["confidence"] == pytest.approx(0.50)


@pytest.mark.asyncio
async def test_below_conviction_lane_and_source_match_approved(
    monkeypatch, schedule_spy, stub_quote_ok, stub_record_paper_trade,
):
    monkeypatch.setattr(opt_mod, "_score_ticker", _make_score_stub("DOWN", 0.40))
    db = MagicMock()

    await opt_mod.run(db)

    kwargs = schedule_spy.call_args.kwargs
    assert kwargs["lane"] == "options"
    assert kwargs["source"] == "options_paper_bot"


# ── Pre-signal early returns: NO receipt ──────────────────────────────────────


@pytest.mark.asyncio
async def test_db_none_logs_no_receipt(monkeypatch, schedule_spy):
    monkeypatch.setattr(opt_mod, "_score_ticker", _make_score_stub("UP", 0.85))

    await opt_mod.run(None)

    schedule_spy.assert_not_called()


@pytest.mark.asyncio
async def test_kill_switch_active_logs_no_receipt(monkeypatch, schedule_spy):
    async def _active():
        return True
    monkeypatch.setattr(opt_mod, "kill_switch_blocks", _active)
    monkeypatch.setattr(opt_mod, "_score_ticker", _make_score_stub("UP", 0.85))
    db = MagicMock()

    await opt_mod.run(db)

    schedule_spy.assert_not_called()


@pytest.mark.asyncio
async def test_score_ticker_none_logs_no_receipt(
    monkeypatch, schedule_spy, stub_record_paper_trade,
):
    """When ``_score_ticker`` returns None there is no decision
    object yet — the hook MUST be skipped."""
    monkeypatch.setattr(opt_mod, "_score_ticker", _make_score_stub("UP", None))
    db = MagicMock()

    await opt_mod.run(db)

    schedule_spy.assert_not_called()
    stub_record_paper_trade.assert_not_awaited()


# ── Failure isolation ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_helper_raise_does_not_crash_run(
    monkeypatch, stub_quote_ok, stub_record_paper_trade,
):
    """A raising helper must NOT escape ``run`` — belt+braces
    try/except at the call site catches it."""
    monkeypatch.setattr(opt_mod, "_score_ticker", _make_score_stub("UP", 0.85))

    def _raising(*_args, **_kwargs):
        raise RuntimeError("synthetic ADL helper failure")

    import services.ml.receipt_dispatch as receipt_dispatch
    monkeypatch.setattr(receipt_dispatch, "schedule_shadow_receipt", _raising)
    db = MagicMock()

    # Must NOT raise. Trade still fires — execution path unaffected.
    await opt_mod.run(db)
    stub_record_paper_trade.assert_awaited()


@pytest.mark.asyncio
async def test_helper_returning_false_does_not_branch_caller(
    monkeypatch, schedule_spy, stub_quote_ok, stub_record_paper_trade,
):
    schedule_spy.return_value = False
    monkeypatch.setattr(opt_mod, "_score_ticker", _make_score_stub("UP", 0.85))
    db = MagicMock()

    await opt_mod.run(db)

    schedule_spy.assert_called_once()
    stub_record_paper_trade.assert_awaited()


# ── Static authority firewall ─────────────────────────────────────────────────


def test_options_paper_does_not_call_run_shadow_pipeline_directly():
    from pathlib import Path
    src = Path(
        "/app/backend/services/trading_agents/options_paper.py"
    ).read_text(encoding="utf-8")

    assert "run_shadow_pipeline(" not in src, (
        "options_paper must use schedule_shadow_receipt(...) and "
        "never call run_shadow_pipeline directly."
    )
    assert "schedule_shadow_receipt" in src, (
        "options_paper must wire to schedule_shadow_receipt."
    )


def test_options_paper_does_not_import_broker_or_executor():
    from pathlib import Path
    src = Path(
        "/app/backend/services/trading_agents/options_paper.py"
    ).read_text(encoding="utf-8")

    forbidden = [
        "from services.broker_service",
        "from services.trading_bot_service",
        "from routes.broker",
        "from services.ml.executors",
        "from services.ml.broker_wire",
        ".place_order(",
        "broker.execute(",
    ]
    found = [tok for tok in forbidden if tok in src]
    assert not found, (
        "options_paper introduced forbidden broker/executor "
        f"reference(s): {found}"
    )
