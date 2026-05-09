"""Chevelle memory labeler — contract tests.

Pins every operator-decreed hard rule (1-10) plus the trust-weight
ladder, era mapping, quarantine semantics, and the no-broker /
no-executor / no-DB-write firewall.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.chevelle_memory_labeler import (
    PUBLIC_LAUNCH_FLOOR_ISO,
    RECENT_PAPER_DAYS,
    TRUST_CURRENT_MACRO_PROXY,
    TRUST_HISTORICAL_PAPER_TRADE,
    TRUST_LIVE_REAL_FILL,
    TRUST_QUARANTINED,
    TRUST_RECENT_PAPER_TRADE,
    TRUST_SYNTHETIC_BACKTEST,
    TRUST_TOXIC_MEMORY,
    ChevelleMemoryRecord,
    DataQuality,
    EventEra,
    FailureMode,
    OutcomeLabel,
    label_memories,
    label_memory,
    quarantined_only,
    trainable_only,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _good_paper_row(**overrides):
    """A well-formed crypto paper-trade row that should label as
    HIGH-quality, recent-paper-trade trust, trainable=True."""
    base = {
        "trade_id": "abc-123",
        "symbol": "BTC-USD",
        "lane": "crypto",
        "source": "crypto_paper_bot",
        "opened_at": _now() - timedelta(days=2),
        "closed_at": _now() - timedelta(days=1),
        "direction": "LONG",
        "confidence": 0.78,
        "regime": "TREND_UP",
        "pnl_usd": 12.34,
    }
    base.update(overrides)
    return base


# ── Rule 1: source MUST be present ────────────────────────────────────────────


def test_missing_source_quarantines_row():
    row = _good_paper_row()
    del row["source"]
    rec = label_memory(row)
    assert rec.trust_weight == TRUST_QUARANTINED
    assert rec.trainable is False
    assert rec.rejection_reason == "missing_source"
    assert rec.chevelle_can_observe is True  # rule 8


def test_blank_source_quarantines_row():
    rec = label_memory(_good_paper_row(source="   "))
    assert rec.trust_weight == TRUST_QUARANTINED
    assert rec.trainable is False


def test_alternate_source_field_accepted():
    """``data_source`` / ``source_engine`` are accepted as fallbacks
    so the labeler can ingest existing collections that didn't
    standardise on ``source`` yet."""
    row = _good_paper_row()
    del row["source"]
    row["data_source"] = "crypto_paper_bot"
    rec = label_memory(row)
    assert rec.source == "crypto_paper_bot"
    assert rec.trainable is True


# ── Rule 2: timestamps required ───────────────────────────────────────────────


def test_no_open_or_close_timestamp_quarantines():
    row = _good_paper_row()
    del row["opened_at"]
    del row["closed_at"]
    rec = label_memory(row)
    assert rec.trainable is False
    assert rec.rejection_reason == "missing_timestamps"


def test_close_without_open_still_passes():
    """closed_at alone is enough — the labeler only needs ONE
    parseable timestamp to anchor the row in time."""
    row = _good_paper_row()
    del row["opened_at"]
    rec = label_memory(row)
    assert rec.trainable is True


# ── Rule 3 + 4: lane and symbol required ──────────────────────────────────────


def test_missing_symbol_quarantines():
    row = _good_paper_row()
    del row["symbol"]
    rec = label_memory(row)
    assert rec.trainable is False
    assert rec.rejection_reason == "missing_symbol"


def test_unknown_lane_does_not_quarantine_but_lowers_resolution():
    """Per the spec rules 1-4 are about absence, not about the
    'unknown' value itself. A row whose lane can't be inferred is
    still trainable at lower trust — operator can review later."""
    row = _good_paper_row()
    row["lane"] = "garbage"
    row["asset_type"] = None
    row["symbol"] = "ZZZZ"  # not in crypto pattern
    rec = label_memory(row)
    assert rec.lane == "unknown"
    # Still observable + trainable as long as required fields are present.
    assert rec.chevelle_can_observe is True


def test_lane_inferred_from_asset_type_when_missing():
    row = _good_paper_row()
    del row["lane"]
    row["asset_type"] = "equity"
    row["symbol"] = "AAPL"
    rec = label_memory(row)
    assert rec.lane == "equity"


def test_lane_inferred_from_symbol_when_lane_and_asset_missing():
    row = _good_paper_row()
    del row["lane"]
    row["symbol"] = "ETH-USD"
    rec = label_memory(row)
    assert rec.lane == "crypto"


# ── Symbol normalization ──────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("BTC-USD", "BTC"),
    ("btc-usd", "BTC"),
    ("BTC/USD", "BTC"),
    ("btc/usdt", "BTC"),
    ("ETH-USDT", "ETH"),
    ("AAPL", "AAPL"),
    ("  spy  ", "SPY"),
])
def test_symbol_normalization(raw, expected):
    rec = label_memory(_good_paper_row(symbol=raw))
    assert rec.symbol == expected


# ── Rule 5: event_era assigned ────────────────────────────────────────────────


@pytest.mark.parametrize("opened_at,expected_era", [
    (datetime(2008, 10, 15, tzinfo=timezone.utc), EventEra.GFC_2008),
    (datetime(2010, 5, 6, tzinfo=timezone.utc), EventEra.FLASH_CRASH_2010),
    (datetime(2015, 9, 1, tzinfo=timezone.utc), EventEra.CHINA_DEVAL_2015),
    (datetime(2018, 2, 15, tzinfo=timezone.utc), EventEra.VOL_SPIKE_2018),
    (datetime(2020, 3, 23, tzinfo=timezone.utc), EventEra.COVID_2020),
    (datetime(2022, 6, 15, tzinfo=timezone.utc), EventEra.RATE_HIKE_2022),
    (datetime(2024, 8, 1, tzinfo=timezone.utc), EventEra.AI_BUBBLE_2024_2025),
    (datetime(2026, 5, 1, tzinfo=timezone.utc), EventEra.CURRENT_REGIME),
    (datetime(1995, 1, 1, tzinfo=timezone.utc), EventEra.UNKNOWN_ERA),
])
def test_event_era_assigned_correctly(opened_at, expected_era):
    rec = label_memory(_good_paper_row(opened_at=opened_at))
    assert rec.event_era is expected_era


# ── Trust ladder (operator-mandated) ──────────────────────────────────────────


def test_trust_live_broker_fill():
    rec = label_memory(_good_paper_row(source="alpaca"))
    assert rec.trust_weight == TRUST_LIVE_REAL_FILL


def test_trust_recent_paper_trade():
    rec = label_memory(_good_paper_row(
        source="crypto_paper_bot",
        opened_at=_now() - timedelta(days=2),
    ))
    assert rec.trust_weight == TRUST_RECENT_PAPER_TRADE


def test_trust_historical_paper_trade():
    rec = label_memory(_good_paper_row(
        source="crypto_paper_bot",
        opened_at=_now() - timedelta(days=RECENT_PAPER_DAYS + 5),
    ))
    assert rec.trust_weight == TRUST_HISTORICAL_PAPER_TRADE


def test_trust_macro_proxy():
    rec = label_memory(_good_paper_row(
        source="fred", lane="macro", symbol="VIX",
    ))
    assert rec.trust_weight == TRUST_CURRENT_MACRO_PROXY


def test_trust_synthetic_backtest_pre_cutover():
    pre_cutover = datetime.fromisoformat(
        PUBLIC_LAUNCH_FLOOR_ISO + "T00:00:00+00:00"
    ) - timedelta(days=30)
    rec = label_memory(_good_paper_row(
        source="backtest", opened_at=pre_cutover,
    ))
    assert rec.trust_weight == TRUST_SYNTHETIC_BACKTEST


def test_trust_yfinance_synthetic():
    rec = label_memory(_good_paper_row(source="yfinance"))
    assert rec.trust_weight == TRUST_SYNTHETIC_BACKTEST


def test_trust_toxic_memory_overrides_recency():
    """Even a recent paper trade collapses to toxic-floor when a
    failure mode is stamped — rule 7 in action."""
    rec = label_memory(_good_paper_row(
        source="crypto_paper_bot",
        opened_at=_now() - timedelta(hours=1),
        failure_mode="blowup",
        pnl_usd=-500.0,
        confidence=0.9,
    ))
    assert rec.trust_weight == TRUST_TOXIC_MEMORY
    assert rec.failure_mode is FailureMode.BLOWUP
    # Rule 7: toxic does NOT delete; the row stays observable.
    assert rec.chevelle_can_observe is True


def test_trust_quarantined_overrides_everything():
    """Even a 'live' source collapses to 0.0 when the row is
    missing required fields."""
    row = _good_paper_row(source="alpaca")
    del row["symbol"]
    rec = label_memory(row)
    assert rec.trust_weight == TRUST_QUARANTINED


# ── Toxic / failure detection ─────────────────────────────────────────────────


def test_high_confidence_loss_inferred_as_toxic():
    """No explicit failure_mode field — but a 0.9-conf trade that
    blew up should self-flag as toxic_high_confidence."""
    rec = label_memory(_good_paper_row(
        confidence=0.92,
        pnl_usd=-200.0,
        r_multiple=-0.8,
        pnl_pct=-0.8,
    ))
    assert rec.failure_mode is FailureMode.TOXIC_HIGH_CONFIDENCE
    assert rec.trust_weight == TRUST_TOXIC_MEMORY


def test_regime_mismatch_failure_mode_recognised():
    rec = label_memory(_good_paper_row(failure_mode="regime_mismatch"))
    assert rec.failure_mode is FailureMode.REGIME_MISMATCH
    assert rec.trust_weight == TRUST_TOXIC_MEMORY


def test_data_integrity_failure_mode_recognised():
    rec = label_memory(_good_paper_row(failure_mode="data_integrity_breach"))
    assert rec.failure_mode is FailureMode.DATA_INTEGRITY


def test_no_failure_when_no_marker_and_normal_trade():
    rec = label_memory(_good_paper_row())
    assert rec.failure_mode is FailureMode.NONE


# ── Outcome labeling ──────────────────────────────────────────────────────────


def test_outcome_win_from_pnl():
    rec = label_memory(_good_paper_row(pnl_usd=10.0))
    assert rec.outcome_label is OutcomeLabel.WIN


def test_outcome_loss_from_pnl():
    rec = label_memory(_good_paper_row(pnl_usd=-5.0))
    assert rec.outcome_label is OutcomeLabel.LOSS


def test_outcome_neutral_from_zero_pnl():
    rec = label_memory(_good_paper_row(pnl_usd=0.0))
    assert rec.outcome_label is OutcomeLabel.NEUTRAL


def test_outcome_unresolved_for_open_position():
    row = _good_paper_row(status="open", closed_at=None)
    row["pnl_usd"] = None
    rec = label_memory(row)
    assert rec.outcome_label is OutcomeLabel.UNRESOLVED


def test_outcome_unresolved_when_no_pnl_field():
    row = _good_paper_row()
    for k in ("pnl_usd", "realized_pnl_usd", "pnl", "r_multiple"):
        row.pop(k, None)
    rec = label_memory(row)
    assert rec.outcome_label is OutcomeLabel.UNRESOLVED


# ── Data quality ──────────────────────────────────────────────────────────────


def test_data_quality_high_when_all_optional_present():
    rec = label_memory(_good_paper_row())
    assert rec.data_quality is DataQuality.HIGH


def test_data_quality_rejected_when_required_missing():
    row = _good_paper_row()
    del row["source"]
    rec = label_memory(row)
    assert rec.data_quality is DataQuality.REJECTED


# ── Rule 6: quarantine bucket exists ──────────────────────────────────────────


def test_quarantined_only_filter():
    good = _good_paper_row()
    bad = _good_paper_row()
    del bad["source"]
    records = list(label_memories([good, bad]))
    quarantined = list(quarantined_only(records))
    assert len(quarantined) == 1
    assert quarantined[0].rejection_reason == "missing_source"


# ── Rule 8: trainable_only excludes quarantined ───────────────────────────────


def test_trainable_only_excludes_quarantined():
    good = _good_paper_row()
    bad = _good_paper_row()
    del bad["source"]
    records = list(label_memories([good, bad]))
    trainable = list(trainable_only(records))
    assert len(trainable) == 1
    assert trainable[0].source == "crypto_paper_bot"


def test_trainable_only_includes_toxic_at_low_trust():
    """Rule 7: toxic memories are STILL trainable at low weight —
    Chevelle learns 'this structure was dangerous'. Only quarantined
    rows (trust=0) are excluded from training."""
    toxic = _good_paper_row(failure_mode="blowup", pnl_usd=-500.0)
    rec = label_memory(toxic)
    trainable = list(trainable_only([rec]))
    assert len(trainable) == 1
    assert trainable[0].trust_weight == TRUST_TOXIC_MEMORY


# ── Rule 9: no verdicts emitted ───────────────────────────────────────────────


def test_record_has_no_verdict_field():
    """Record schema must NEVER expose BUY / SELL / NO_TRADE — those
    are decision-stack outputs. The labeler is observation-only."""
    rec = label_memory(_good_paper_row())
    forbidden = {"buy", "sell", "no_trade", "verdict", "decision",
                 "action", "place_order"}
    record_keys = set(rec.__dict__.keys())
    assert not (forbidden & record_keys), (
        f"verdict-shaped field leaked into record schema: "
        f"{forbidden & record_keys}"
    )


# ── Rule 10: no broker / executor / DB-write imports ──────────────────────────


def test_module_does_not_import_broker_or_executor():
    """Static authority firewall — pin the no-broker /
    no-executor / no-Strategist invariant across all three
    files in the labeler split."""
    from pathlib import Path
    paths = [
        "/app/backend/services/chevelle_memory_labeler.py",
        "/app/backend/services/_chevelle_resolvers.py",
        "/app/backend/services/chevelle_memory_labels.py",
    ]
    forbidden = [
        "from services.broker_service",
        "from services.trading_bot_service",
        "from services.crypto_paper_trader",
        "from services.paper_trading_service",
        "from services.trading_agents",
        "from services.day_trade_scanner",
        "from routes.broker",
        "from routes.trading",
        "from services.ml.executors",
        "from services.ml.broker_wire",
        "from services.ml.shadow_wiring",
        "from services.ml.strategist",
        "from services.ml.auditor",
        "from services.ml.fast_veto_layer",
        "from services.ml.roadguard",
        "from services.fast_veto_layer",
        "from services.roadguard",
        "from services.confidence_gate",
        "from services.commander_decision_stream",
        "from ai_core.kill_switch",
        "from motor",
        "import motor",
        "from pymongo",
        "import pymongo",
    ]
    for p in paths:
        src = Path(p).read_text(encoding="utf-8")
        found = [tok for tok in forbidden if tok in src]
        assert not found, (
            f"{p} imported forbidden module(s): {found}"
        )


def test_module_performs_no_collection_writes():
    from pathlib import Path
    paths = [
        "/app/backend/services/chevelle_memory_labeler.py",
        "/app/backend/services/_chevelle_resolvers.py",
        "/app/backend/services/chevelle_memory_labels.py",
    ]
    write_patterns = [
        ".insert_one(",
        ".insert_many(",
        ".update_one(",
        ".update_many(",
        ".replace_one(",
        ".delete_one(",
        ".delete_many(",
        ".find_one_and_update(",
        ".bulk_write(",
        ".drop(",
    ]
    for p in paths:
        src = Path(p).read_text(encoding="utf-8")
        found = [tok for tok in write_patterns if tok in src]
        assert not found, (
            f"{p} attempted DB writes: {found}"
        )


def test_module_does_not_emit_verdicts_in_source():
    from pathlib import Path
    paths = [
        "/app/backend/services/chevelle_memory_labeler.py",
        "/app/backend/services/_chevelle_resolvers.py",
        "/app/backend/services/chevelle_memory_labels.py",
    ]
    forbidden = [
        "set_active(",
        "promote_now(",
        ".place_order(",
        "broker.execute(",
        "Verdict.BUY",
        "Verdict.SELL",
    ]
    for p in paths:
        src = Path(p).read_text(encoding="utf-8")
        found = [tok for tok in forbidden if tok in src]
        assert not found, (
            f"verdict-shaped tokens leaked into {p}: {found}"
        )


# ── Bulk + non-dict input handling ────────────────────────────────────────────


def test_label_memories_yields_one_record_per_input():
    rows = [_good_paper_row() for _ in range(5)]
    records = list(label_memories(rows))
    assert len(records) == 5
    assert all(isinstance(r, ChevelleMemoryRecord) for r in records)


def test_non_dict_input_is_quarantined_not_raised():
    rec = label_memory("not a dict")
    assert rec.rejection_reason == "non_dict_input"
    assert rec.trust_weight == TRUST_QUARANTINED
    assert rec.trainable is False
    # Improvement: structurally broken input is unobservable too.
    assert rec.chevelle_can_observe is False


def test_none_input_is_quarantined_not_raised():
    rec = label_memory(None)
    assert rec.rejection_reason == "non_dict_input"
    assert rec.chevelle_can_observe is False


# ── Observation policy ────────────────────────────────────────────────────────


def test_default_policy_is_all_observable():
    rec = label_memory(_good_paper_row())
    assert rec.chevelle_can_observe is True


def test_exclude_synthetic_policy_silences_synthetic_rows():
    """``exclude_synthetic`` flips ``chevelle_can_observe=False`` for
    synthetic-tier rows (yfinance / backtest / unknown source)
    while leaving paper / live rows observable."""
    from services.chevelle_memory_labeler import (
        OBSERVATION_POLICY_EXCLUDE_SYNTHETIC,
    )
    rec = label_memory(
        _good_paper_row(source="yfinance"),
        observation_policy=OBSERVATION_POLICY_EXCLUDE_SYNTHETIC,
    )
    assert rec.trust_weight == TRUST_SYNTHETIC_BACKTEST
    assert rec.chevelle_can_observe is False
    # trainable is unchanged — only the observation flag flips.
    assert rec.trainable is True


def test_exclude_synthetic_policy_keeps_paper_observable():
    from services.chevelle_memory_labeler import (
        OBSERVATION_POLICY_EXCLUDE_SYNTHETIC,
    )
    rec = label_memory(
        _good_paper_row(),
        observation_policy=OBSERVATION_POLICY_EXCLUDE_SYNTHETIC,
    )
    assert rec.chevelle_can_observe is True


def test_live_only_policy_silences_paper_rows():
    """``live_only`` flips ``chevelle_can_observe=False`` for
    anything below the live broker tier."""
    from services.chevelle_memory_labeler import (
        OBSERVATION_POLICY_LIVE_ONLY,
    )
    rec = label_memory(
        _good_paper_row(),
        observation_policy=OBSERVATION_POLICY_LIVE_ONLY,
    )
    assert rec.trust_weight == TRUST_RECENT_PAPER_TRADE
    assert rec.chevelle_can_observe is False


def test_live_only_policy_keeps_live_observable():
    from services.chevelle_memory_labeler import (
        OBSERVATION_POLICY_LIVE_ONLY,
    )
    rec = label_memory(
        _good_paper_row(source="alpaca"),
        observation_policy=OBSERVATION_POLICY_LIVE_ONLY,
    )
    assert rec.trust_weight == TRUST_LIVE_REAL_FILL
    assert rec.chevelle_can_observe is True


def test_unknown_policy_falls_back_to_all_without_raising():
    """An operator-supplied policy string the labeler doesn't
    recognise must NOT raise — fall back to 'all'."""
    rec = label_memory(
        _good_paper_row(),
        observation_policy="something_we_havent_built_yet",
    )
    assert rec.chevelle_can_observe is True


def test_policy_does_not_change_trust_or_trainable():
    """Critical invariant: the policy is observation-only. Trust
    weight and trainable flag MUST be invariant across policies."""
    from services.chevelle_memory_labeler import (
        OBSERVATION_POLICY_EXCLUDE_SYNTHETIC,
        OBSERVATION_POLICY_LIVE_ONLY,
    )
    row = _good_paper_row(source="yfinance")
    a = label_memory(row, observation_policy="all")
    b = label_memory(row, observation_policy=OBSERVATION_POLICY_EXCLUDE_SYNTHETIC)
    c = label_memory(row, observation_policy=OBSERVATION_POLICY_LIVE_ONLY)
    assert a.trust_weight == b.trust_weight == c.trust_weight
    assert a.trainable == b.trainable == c.trainable


def test_label_memories_forwards_observation_policy():
    """The bulk function must thread the policy through to every
    record — no per-row policy drift."""
    from services.chevelle_memory_labeler import (
        OBSERVATION_POLICY_LIVE_ONLY,
    )
    rows = [_good_paper_row(symbol=s) for s in ("BTC", "ETH", "SOL")]
    records = list(
        label_memories(rows, observation_policy=OBSERVATION_POLICY_LIVE_ONLY)
    )
    assert len(records) == 3
    # All paper trades → all silenced under live_only.
    assert all(r.chevelle_can_observe is False for r in records)


# ── End observation policy ────────────────────────────────────────────────────


# ── Idempotence ───────────────────────────────────────────────────────────────


def test_labeling_is_idempotent_on_pure_inputs():
    """Same input → same labeled record."""
    row = _good_paper_row()
    a = label_memory(dict(row))
    b = label_memory(dict(row))
    assert a == b


def test_label_memory_does_not_mutate_input():
    row = _good_paper_row()
    snapshot = dict(row)
    label_memory(row)
    assert row == snapshot
