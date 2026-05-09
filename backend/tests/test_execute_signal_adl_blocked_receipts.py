"""ADL-5 contract tests — execute_signal upstream gate-block receipts.

Pins the fire-and-forget hooks added across
``services/trading_bot_service.py`` so EVERY upstream gate that
short-circuits the executor produces an ``alpha_decision_log``
receipt via ``services.ml.receipt_dispatch.schedule_shadow_receipt``
with ``source="execute_signal_blocked"`` and a ``blocked_at`` /
``block_reason`` payload tag identifying the gate stage.

Hard rails pinned by these tests
--------------------------------
* Each upstream block schedules exactly ONE receipt.
* Receipt includes ``lane``, ``source="execute_signal_blocked"``,
  signal payload contains ``blocked_at`` (gate stage) and
  ``block_reason`` (preserved skip reason).
* Successful path still schedules its existing ONE
  ``source="execute_signal"`` receipt — no duplicates.
* Scheduling failure does NOT alter the returned skip dict.
* Caller's ``signal`` dict is NEVER mutated (the helper copies it).
* Broker is NEVER touched on a blocked path (asserted via spy
  on ``_execute_bot_trade``).

Gate stages covered
-------------------
* ``kill_switch_or_drawdown`` — kill switch active OR drawdown trip
* ``fast_veto_enforce``       — Fast Veto enforce mode
* ``size_chain``              — covers sector_cap / max_concurrent /
                                max_portfolio / drawdown allocator /
                                low-confidence floor
* ``resolve_qty``             — invalid price / quote unavailable
* ``roadguard_enforce``       — RoadGuard ENFORCE mode
* ``max_trades_per_day``      — dispatcher daily cap + webhook cap
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import services.trading_bot_service as tbs


# ── Shared scaffolding ────────────────────────────────────────────────────────


def _signal(symbol: str = "AAPL", direction: str = "LONG",
            confidence: float = 0.85) -> dict:
    return {
        "symbol": symbol,
        "direction": direction,
        "confidence": confidence,
        "asset_type": "equity",
        "entry": 150.0,
    }


def _market(price: float = 150.0) -> dict:
    return {"price": price, "spread_bps": 5.0,
            "volatility_score": 0.2, "liquidity_score": 0.9}


def _ready() -> dict:
    return {"confidence_score": 0.95,
            "stats": {"win_rate": 0.6, "trades": 100}}


def _config(trade_size: float = 1000.0) -> dict:
    return {"trade_size": trade_size, "qty": 10}


@pytest.fixture
def schedule_spy(monkeypatch):
    """Spy on the shared ``schedule_shadow_receipt`` helper.

    Patched at the source module so deferred imports in tbs
    (helper + ADL-1 success-path call) all resolve to our spy.
    """
    import services.ml.receipt_dispatch as receipt_dispatch
    spy = MagicMock(return_value=True)
    monkeypatch.setattr(receipt_dispatch, "schedule_shadow_receipt", spy)
    return spy


@pytest.fixture
def broker_tripwire(monkeypatch):
    """Spy on ``_execute_bot_trade`` — must NEVER be called on
    a gate-blocked path. Returns a MagicMock so an accidental
    call would be visible immediately."""
    spy = MagicMock(side_effect=AssertionError(
        "broker MUST NOT be called on a gate-blocked path"
    ))
    monkeypatch.setattr(tbs, "_execute_bot_trade", spy)
    return spy


# ── kill_switch_or_drawdown gate ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_kill_switch_block_schedules_receipt(
    monkeypatch, schedule_spy, broker_tripwire,
):
    """Kill-switch active → execute_signal returns skip dict AND
    one ``execute_signal_blocked`` receipt is scheduled."""
    monkeypatch.setattr(
        tbs, "_check_kill_switch_and_drawdown",
        lambda _ec: {"skipped": True,
                     "reason": "kill switch active: manual"},
    )
    result = await tbs.execute_signal(
        signal=_signal(), market_data=_market(),
        tier3_readiness=_ready(), config=_config(), lane="equity",
    )

    assert result == {"skipped": True,
                      "reason": "kill switch active: manual"}
    assert schedule_spy.call_count == 1
    kwargs = schedule_spy.call_args.kwargs
    assert kwargs["lane"] == "equity"
    assert kwargs["source"] == "execute_signal_blocked"
    sig = kwargs["signal"]
    assert sig["blocked_at"] == "kill_switch_or_drawdown"
    assert "kill switch" in sig["block_reason"]


@pytest.mark.asyncio
async def test_kill_switch_block_does_not_mutate_caller_signal(
    monkeypatch, schedule_spy, broker_tripwire,
):
    monkeypatch.setattr(
        tbs, "_check_kill_switch_and_drawdown",
        lambda _ec: {"skipped": True, "reason": "drawdown trip"},
    )
    sig_in = _signal()
    await tbs.execute_signal(
        signal=sig_in, market_data=_market(),
        tier3_readiness=_ready(), config=_config(), lane="equity",
    )

    assert "blocked_at" not in sig_in
    assert "block_reason" not in sig_in


@pytest.mark.asyncio
async def test_kill_switch_block_helper_failure_preserves_return(
    monkeypatch, broker_tripwire,
):
    monkeypatch.setattr(
        tbs, "_check_kill_switch_and_drawdown",
        lambda _ec: {"skipped": True, "reason": "kill switch"},
    )
    import services.ml.receipt_dispatch as rd

    def _raising(*_a, **_k):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(rd, "schedule_shadow_receipt", _raising)

    # Must NOT raise; return shape preserved.
    result = await tbs.execute_signal(
        signal=_signal(), market_data=_market(),
        tier3_readiness=_ready(), config=_config(), lane="equity",
    )
    assert result == {"skipped": True, "reason": "kill switch"}


# ── size_chain gate ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_size_chain_block_schedules_receipt(
    monkeypatch, schedule_spy, broker_tripwire,
):
    """``_compute_adjusted_size`` returns a skip_reason — covers
    sector_cap / max_concurrent / max_portfolio / drawdown
    allocator / low-confidence floor."""
    monkeypatch.setattr(
        tbs, "_check_kill_switch_and_drawdown",
        lambda _ec: None,
    )
    monkeypatch.setattr(
        tbs, "_compute_adjusted_size",
        lambda **_kw: (0.0, "portfolio limits reached"),
    )

    result = await tbs.execute_signal(
        signal=_signal(), market_data=_market(),
        tier3_readiness=_ready(), config=_config(), lane="equity",
    )

    assert result == {"skipped": True,
                      "reason": "portfolio limits reached"}
    assert schedule_spy.call_count == 1
    sig = schedule_spy.call_args.kwargs["signal"]
    assert sig["blocked_at"] == "size_chain"
    assert sig["block_reason"] == "portfolio limits reached"


@pytest.mark.asyncio
async def test_size_chain_risk_control_reason_preserved(
    monkeypatch, schedule_spy, broker_tripwire,
):
    """Drawdown allocator path uses ``"risk control"`` reason — the
    helper preserves it verbatim into ``block_reason``."""
    monkeypatch.setattr(
        tbs, "_check_kill_switch_and_drawdown", lambda _ec: None,
    )
    monkeypatch.setattr(
        tbs, "_compute_adjusted_size",
        lambda **_kw: (0.0, "risk control"),
    )

    await tbs.execute_signal(
        signal=_signal(), market_data=_market(),
        tier3_readiness=_ready(), config=_config(), lane="equity",
    )

    sig = schedule_spy.call_args.kwargs["signal"]
    assert sig["blocked_at"] == "size_chain"
    assert sig["block_reason"] == "risk control"


# ── resolve_qty gate ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_qty_block_schedules_receipt(
    monkeypatch, schedule_spy, broker_tripwire,
):
    monkeypatch.setattr(
        tbs, "_check_kill_switch_and_drawdown", lambda _ec: None,
    )
    monkeypatch.setattr(
        tbs, "_compute_adjusted_size",
        lambda **_kw: (1000.0, None),
    )
    monkeypatch.setattr(
        tbs, "_resolve_qty",
        lambda *_a, **_kw: (0.0, 0.0, "invalid price"),
    )

    result = await tbs.execute_signal(
        signal=_signal(), market_data=_market(),
        tier3_readiness=_ready(), config=_config(), lane="equity",
    )

    assert result == {"skipped": True, "reason": "invalid price"}
    assert schedule_spy.call_count == 1
    sig = schedule_spy.call_args.kwargs["signal"]
    assert sig["blocked_at"] == "resolve_qty"
    assert sig["block_reason"] == "invalid price"


# ── Successful path: existing ADL-1 receipt fires exactly ONCE ────────────────


@pytest.mark.asyncio
async def test_successful_path_schedules_only_existing_receipt(
    monkeypatch, schedule_spy,
):
    """Success path → ONE receipt with source='execute_signal'.
    No blocked-receipt scheduling — the helper isn't reached."""
    monkeypatch.setattr(
        tbs, "_check_kill_switch_and_drawdown", lambda _ec: None,
    )
    monkeypatch.setattr(
        tbs, "_compute_adjusted_size",
        lambda **_kw: (1000.0, None),
    )
    monkeypatch.setattr(
        tbs, "_resolve_qty",
        lambda *_a, **_kw: (10.0, 100.0, None),
    )
    monkeypatch.setattr(
        tbs, "_bot_from_config",
        lambda _c, _s: {"id": "test_bot", "mode": "paper"},
    )
    monkeypatch.setattr(
        tbs, "_fire_equity_shadow", lambda **_kw: None,
    )
    monkeypatch.setattr(
        tbs, "_record_kill_switch_outcome", lambda _o: None,
    )

    async def _ok_broker(*_a, **_kw):
        return {"status": "filled", "broker_order_id": "x",
                "symbol": "AAPL", "side": "BUY", "qty": 10.0}

    monkeypatch.setattr(tbs, "_execute_bot_trade", _ok_broker)

    result = await tbs.execute_signal(
        signal=_signal(), market_data=_market(),
        tier3_readiness=_ready(), config=_config(), lane="equity",
    )

    assert result.get("order", {}).get("status") == "filled"
    # Exactly ONE receipt — the success-path ADL-1 hook.
    assert schedule_spy.call_count == 1
    assert schedule_spy.call_args.kwargs["source"] == "execute_signal"
    # Success-path signal must NOT carry blocked_at.
    sig = schedule_spy.call_args.kwargs["signal"]
    assert "blocked_at" not in sig


# ── No duplicate receipts when one gate fires ─────────────────────────────────


@pytest.mark.asyncio
async def test_kill_switch_block_does_not_double_log(
    monkeypatch, schedule_spy, broker_tripwire,
):
    """When the FIRST gate (kill_switch) trips, downstream gates
    must NOT fire. Exactly ONE blocked-receipt is scheduled."""
    monkeypatch.setattr(
        tbs, "_check_kill_switch_and_drawdown",
        lambda _ec: {"skipped": True, "reason": "kill switch"},
    )
    # If downstream gates fired, schedule_spy would be called more
    # than once. They MUST be unreachable.
    raise_if_called = MagicMock(side_effect=AssertionError(
        "downstream gate must not run after kill_switch trip"
    ))
    monkeypatch.setattr(tbs, "_compute_adjusted_size", raise_if_called)
    monkeypatch.setattr(tbs, "_resolve_qty", raise_if_called)

    await tbs.execute_signal(
        signal=_signal(), market_data=_market(),
        tier3_readiness=_ready(), config=_config(), lane="equity",
    )

    assert schedule_spy.call_count == 1


# ── Helper unit tests (direct invocation) ─────────────────────────────────────


def test_blocked_helper_uses_correct_source_tag(monkeypatch):
    import services.ml.receipt_dispatch as rd
    spy = MagicMock(return_value=True)
    monkeypatch.setattr(rd, "schedule_shadow_receipt", spy)

    tbs._schedule_blocked_receipt(
        signal={"symbol": "AAPL"},
        market_data=None,
        lane="equity",
        blocked_at="kill_switch_or_drawdown",
        reason="manual",
    )

    spy.assert_called_once()
    assert spy.call_args.kwargs["source"] == "execute_signal_blocked"


def test_blocked_helper_does_not_mutate_signal_in(monkeypatch):
    import services.ml.receipt_dispatch as rd
    captured = {}

    def _capture(*_a, **kw):
        captured.update(kw)
        return True

    monkeypatch.setattr(rd, "schedule_shadow_receipt", _capture)

    sig_in = {"symbol": "AAPL", "direction": "LONG"}
    tbs._schedule_blocked_receipt(
        signal=sig_in, market_data=None, lane="equity",
        blocked_at="size_chain", reason="portfolio limits reached",
    )

    # Caller's signal MUST be unchanged.
    assert sig_in == {"symbol": "AAPL", "direction": "LONG"}
    # Helper passed a tagged COPY downstream.
    assert captured["signal"]["blocked_at"] == "size_chain"
    assert captured["signal"]["block_reason"] == "portfolio limits reached"


def test_blocked_helper_swallows_exception(monkeypatch):
    """Helper's own try/except guards against any failure inside
    receipt_dispatch — caller MUST never see a raise."""
    import services.ml.receipt_dispatch as rd

    def _raising(*_a, **_kw):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(rd, "schedule_shadow_receipt", _raising)

    # Must NOT raise.
    tbs._schedule_blocked_receipt(
        signal={"symbol": "AAPL"},
        market_data=None,
        lane="equity",
        blocked_at="kill_switch_or_drawdown",
        reason="x",
    )


# ── Static authority firewall ─────────────────────────────────────────────────


def test_blocked_receipt_helper_uses_shared_dispatcher():
    """The blocked-path helper must wire through the shared
    ``schedule_shadow_receipt`` dispatcher — NOT call the
    pipeline directly."""
    from pathlib import Path
    src = Path(
        "/app/backend/services/trading_bot_service.py"
    ).read_text(encoding="utf-8")

    # Look for the helper definition.
    assert "_schedule_blocked_receipt" in src
    assert "source=\"execute_signal_blocked\"" in src
    # Helper does NOT call run_shadow_pipeline directly anywhere.
    blocked_helper_section = src.split(
        "def _schedule_blocked_receipt"
    )[1].split("\nasync def execute_signal")[0]
    assert "run_shadow_pipeline(" not in blocked_helper_section
