"""Tests for Patent M (Alpha) — Shelly ingest adapter + shadow
delta logger.

Pinned invariants:

INGEST ADAPTER (``shelly_ingest_adapter.py``):
* ``test_ingest_skipped_when_env_flag_off``
* ``test_ingest_skipped_when_macro_unavailable``
* ``test_ingest_skipped_for_hold_or_unknown_direction``
* ``test_ingest_succeeds_with_full_macro``
* ``test_paper_trade_to_memory_derives_pnl_when_missing``
* ``test_paper_trade_to_memory_handles_short_direction_correctly``
* ``test_feed_shelly_never_raises_on_bad_input``

SHADOW LOGGER (``shelly_shadow_logger.py``):
* ``test_logger_skipped_when_env_flag_off``
* ``test_logger_skipped_when_no_learning_core_field``
* ``test_logger_does_not_mutate_payload``
* ``test_logger_writes_expected_row_shape``
* ``test_logger_handles_mongo_failure_gracefully``
* ``test_logger_caps_hypothetical_confidence_at_max_delta``
* ``test_logger_rm_floor_and_one_way_down``
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("REGIME_MEMORY_ENABLED", "true")
os.environ.setdefault("REGIME_MEMORY_MODE", "full_context")

import services.regime_memory_retrieval as _rmr  # noqa: E402

_rmr.REGIME_MEMORY_ENABLED = True
_rmr.REGIME_MEMORY_MODE = "full_context"

from services.auto_regime_tagger import RawMacroData  # noqa: E402
from services.learning_core_service import (  # noqa: E402
    reset_singleton_for_tests,
)
from services.shelly_ingest_adapter import (  # noqa: E402
    feed_shelly_from_closed_trade,
    paper_trade_to_memory,
)
from services.shelly_shadow_logger import (  # noqa: E402
    COLLECTION_NAME as SHADOW_COLLECTION,
    log_shadow_delta,
)


# ─── builders ──────────────────────────────────────────────────


def _macro() -> RawMacroData:
    return RawMacroData(
        date="2026-02-15",
        vix=18.0,
        yield_2y=4.5,
        yield_10y=4.6,
        dxy=104.0,
        hy_oas_bp=420.0,
        ig_oas_bp=120.0,
        liquidity_z=0.0,
    )


def _trade(
    direction: str = "LONG",
    ep: float = 100.0,
    xp: float = 103.0,
    pnl_pct: float | None = None,
) -> dict:
    out: dict = {
        "trade_id": "t1",
        "symbol": "BTC",
        "direction": direction,
        "entry_price": ep,
        "exit_price": xp,
        "opened_at": datetime(2026, 2, 14, tzinfo=timezone.utc),
        "closed_at": datetime(2026, 2, 15, tzinfo=timezone.utc),
    }
    if pnl_pct is not None:
        out["pnl_pct"] = pnl_pct
    return out


# ─── ingest adapter ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ingest_skipped_when_env_flag_off(monkeypatch):
    monkeypatch.setenv("LEARNING_CORE_INGEST_ENABLED", "false")
    out = await feed_shelly_from_closed_trade(MagicMock(), _trade())
    assert out["ok"] is False
    assert out["reason"] == "env_flag_off"


@pytest.mark.asyncio
async def test_ingest_skipped_when_macro_unavailable(monkeypatch):
    """If the macro cache is cold, we'd rather skip than feed
    Shelly fabricated data."""
    monkeypatch.setenv("LEARNING_CORE_INGEST_ENABLED", "true")
    # Patch the resolver to return None directly to avoid touching
    # the real fred_service cache.
    import services.shelly_ingest_adapter as mod
    monkeypatch.setattr(
        mod, "_resolve_current_macro", AsyncMock(return_value=None),
    )
    out = await feed_shelly_from_closed_trade(MagicMock(), _trade())
    assert out["ok"] is False
    assert out["reason"] == "no_macro"


@pytest.mark.asyncio
async def test_ingest_skipped_for_hold_or_unknown_direction(monkeypatch):
    """HOLD / UNKNOWN / non-trade tokens must not enter Shelly's
    memory bank — that's an Alpha-side guarantee."""
    monkeypatch.setenv("LEARNING_CORE_INGEST_ENABLED", "true")
    import services.shelly_ingest_adapter as mod
    monkeypatch.setattr(
        mod, "_resolve_current_macro", AsyncMock(return_value=_macro()),
    )
    for d in ("HOLD", "UNKNOWN", "ZIGZAG", "", None):
        out = await feed_shelly_from_closed_trade(
            MagicMock(), _trade(direction=d or ""),
        )
        assert out["ok"] is False
        assert out["reason"] == "trade_not_eligible"


@pytest.mark.asyncio
async def test_ingest_succeeds_with_full_macro(monkeypatch):
    monkeypatch.setenv("LEARNING_CORE_INGEST_ENABLED", "true")
    monkeypatch.setenv("LEARNING_CORE_PERSISTENCE_ENABLED", "false")
    reset_singleton_for_tests()

    import services.shelly_ingest_adapter as mod
    monkeypatch.setattr(
        mod, "_resolve_current_macro", AsyncMock(return_value=_macro()),
    )
    out = await feed_shelly_from_closed_trade(MagicMock(), _trade())
    assert out["ok"] is True
    assert out["memory_id"] == "t1"
    assert out["regime_cluster_id"] is not None


def test_paper_trade_to_memory_derives_pnl_when_missing():
    mem = paper_trade_to_memory(
        _trade(direction="LONG", ep=100.0, xp=110.0), _macro(),
    )
    assert mem is not None
    assert mem.pnl_pct == pytest.approx(10.0, abs=1e-6)


def test_paper_trade_to_memory_handles_short_direction_correctly():
    """SHORT trades flip the pnl sign — selling at 110 after
    entering at 100 is a 10% LOSS for a short."""
    mem = paper_trade_to_memory(
        _trade(direction="SHORT", ep=100.0, xp=110.0), _macro(),
    )
    assert mem is not None
    assert mem.pnl_pct == pytest.approx(-10.0, abs=1e-6)


def test_paper_trade_to_memory_canonicalises_buy_to_long():
    """The adapter accepts ``BUY``/``SELL`` from equity closers
    and canonicalises to ``LONG``/``SHORT``."""
    mem = paper_trade_to_memory(_trade(direction="BUY"), _macro())
    assert mem is not None
    assert mem.direction == "LONG"


@pytest.mark.asyncio
async def test_feed_shelly_never_raises_on_bad_input(monkeypatch):
    monkeypatch.setenv("LEARNING_CORE_INGEST_ENABLED", "true")
    import services.shelly_ingest_adapter as mod
    monkeypatch.setattr(
        mod, "_resolve_current_macro", AsyncMock(return_value=_macro()),
    )
    # Trade missing required fields shouldn't crash.
    out = await feed_shelly_from_closed_trade(MagicMock(), {})
    assert out["ok"] is False


# ─── shadow logger ─────────────────────────────────────────────


def _payload_with_lc(
    direction: str = "LONG",
    base_conf: float = 0.60,
    adj_conf: float = 0.72,
    base_rm: float = 1.00,
    pretell: dict | None = None,
) -> dict:
    return {
        "decision": direction,
        "symbol": "AAPL",
        "confidence": base_conf,
        "risk_multiplier": base_rm,
        "learning_core": {
            "direction_canonical": direction,
            "base_confidence": base_conf,
            "adjusted_confidence": adj_conf,
            "memory_win_rate": 0.65,
            "similar_memory_count": 4,
            "pretell_warning": pretell,
        },
    }


def _fake_db_with_capture():
    """Returns (db_mock, captured_rows_list).

    Captures every ``insert_one(doc)`` call so tests can inspect
    the persisted shape without spinning up Mongo.
    """
    captured: list = []

    async def _capture(doc):
        captured.append(doc)
        return MagicMock()

    coll = MagicMock()
    coll.insert_one = _capture
    db = MagicMock()
    db.__getitem__.return_value = coll
    return db, captured


@pytest.mark.asyncio
async def test_logger_skipped_when_env_flag_off(monkeypatch):
    monkeypatch.setenv("LEARNING_CORE_SHADOW_DELTA_LOG_ENABLED", "false")
    out = await log_shadow_delta(MagicMock(), _payload_with_lc())
    assert out["logged"] is False
    assert out["reason"] == "env_flag_off"


@pytest.mark.asyncio
async def test_logger_skipped_when_no_learning_core_field(monkeypatch):
    monkeypatch.setenv("LEARNING_CORE_SHADOW_DELTA_LOG_ENABLED", "true")
    out = await log_shadow_delta(
        MagicMock(),
        {"decision": "LONG", "confidence": 0.7},
    )
    assert out["logged"] is False
    assert out["reason"] == "no_learning_core_field"


@pytest.mark.asyncio
async def test_logger_does_not_mutate_payload(monkeypatch):
    """The whole point of step 3 vs step 5 — the *live* payload
    must NEVER carry the consumer's hypothetical mutation."""
    monkeypatch.setenv("LEARNING_CORE_SHADOW_DELTA_LOG_ENABLED", "true")
    payload = _payload_with_lc(base_conf=0.5, adj_conf=0.9, base_rm=1.0)
    snapshot_conf = payload["confidence"]
    snapshot_rm = payload["risk_multiplier"]
    snapshot_decision = payload["decision"]

    db, _ = _fake_db_with_capture()
    await log_shadow_delta(db, payload)

    assert payload["confidence"] == snapshot_conf
    assert payload["risk_multiplier"] == snapshot_rm
    assert payload["decision"] == snapshot_decision
    assert "learning_core_consumed" not in payload


@pytest.mark.asyncio
async def test_logger_writes_expected_row_shape(monkeypatch):
    monkeypatch.setenv("LEARNING_CORE_SHADOW_DELTA_LOG_ENABLED", "true")
    db, captured = _fake_db_with_capture()
    out = await log_shadow_delta(
        db, _payload_with_lc(base_conf=0.6, adj_conf=0.65),
    )
    assert out["logged"] is True
    assert len(captured) == 1
    row = captured[0]
    for key in (
        "ts", "symbol", "decision", "direction_canonical",
        "confidence_before", "confidence_hypothetical",
        "confidence_delta", "risk_multiplier_before",
        "risk_multiplier_hypothetical", "risk_multiplier_delta",
        "memory_win_rate", "similar_memory_count",
        "pretell_warning_present", "would_have_consumed",
    ):
        assert key in row
    assert row["would_have_consumed"] is True
    assert row["confidence_delta"] == pytest.approx(0.05, abs=1e-9)


@pytest.mark.asyncio
async def test_logger_handles_mongo_failure_gracefully(monkeypatch):
    """Mongo failure must NEVER propagate — the live decision
    runs whether or not we manage to log."""
    monkeypatch.setenv("LEARNING_CORE_SHADOW_DELTA_LOG_ENABLED", "true")
    coll = MagicMock()
    coll.insert_one = AsyncMock(side_effect=RuntimeError("db down"))
    db = MagicMock()
    db.__getitem__.return_value = coll

    out = await log_shadow_delta(db, _payload_with_lc())
    assert out["logged"] is False
    assert "db down" in out["reason"]


@pytest.mark.asyncio
async def test_logger_caps_hypothetical_confidence_at_max_delta(monkeypatch):
    """Even if the annotation suggests a wild swing, the
    hypothetical row carries the BOUNDED value the consumer
    would have applied — same ±0.10 cap."""
    monkeypatch.setenv("LEARNING_CORE_SHADOW_DELTA_LOG_ENABLED", "true")
    db, captured = _fake_db_with_capture()
    await log_shadow_delta(
        db,
        _payload_with_lc(base_conf=0.30, adj_conf=0.99),
    )
    row = captured[0]
    # Bound is 0.10 — hypothetical mustn't exceed base+0.10.
    assert row["confidence_hypothetical"] - 0.30 <= 0.10 + 1e-9


@pytest.mark.asyncio
async def test_logger_rm_floor_and_one_way_down(monkeypatch):
    """Risk multiplier is one-way down. Pretell warning damps;
    no warning leaves it untouched."""
    monkeypatch.setenv("LEARNING_CORE_SHADOW_DELTA_LOG_ENABLED", "true")
    db, captured = _fake_db_with_capture()

    # No warning — RM untouched in shadow.
    await log_shadow_delta(
        db, _payload_with_lc(base_rm=1.0, pretell=None),
    )
    assert captured[-1]["risk_multiplier_hypothetical"] == 1.0

    # With warning — RM damped down only.
    await log_shadow_delta(
        db,
        _payload_with_lc(base_rm=1.0, pretell={"shift_type": "vol_spike"}),
    )
    assert captured[-1]["risk_multiplier_hypothetical"] < 1.0
    assert captured[-1]["risk_multiplier_hypothetical"] >= 0.50


def test_shadow_collection_name_is_stable():
    assert SHADOW_COLLECTION == "shelly_shadow_deltas"
