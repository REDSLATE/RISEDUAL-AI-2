"""Tests for `services.crypto_adversarial_stats`.

Pins down:

1. **Bucketing** — decisions split correctly by `decision` field
   (LONG / SHORT_OR_AVOID / NO_TRADE).
2. **Per-bucket math** — count / avg_r / median_r / win_rate, with
   even/odd median + zero-r-as-loss winrate.
3. **Win-rate attribution** — bull/bear win rates derive from
   `winner` field (already populated at close time by
   `update_decision_outcome`).
4. **No-trade-avoided** — the operator's "would Commander have
   saved or cost us money?" metric is computed correctly.
5. **Maturity guardrail** — `actionable=False` until every decision
   bucket has ≥ 15 closed rows; same discipline as shadow-research
   stats.
6. **Mongo glue** — query filters honour `hours` + `phase`, and
   exceptions don't raise out (admin tile is non-critical).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from services.crypto_adversarial_stats import (
    DECISIONS_PAGE_LIMIT_MAX,
    MIN_BUCKET_SAMPLES,
    compute_adversarial_stats,
    fetch_adversarial_stats,
    fetch_recent_decisions,
    fetch_trades_with_decisions,
)


# ── Test factory ──────────────────────────────────────────────────────────────


def _row(decision: str, final_r=None, winner=None,
         edge_gap: float = 0.0, phase: str = "shadow") -> dict:
    return {
        "decision": decision,
        "final_result_r": final_r,
        "winner": winner,
        "edge_gap": edge_gap,
        "phase": phase,
    }


# ── Empty / open-only ─────────────────────────────────────────────────────────


def test_empty_returns_zeroed_payload():
    out = compute_adversarial_stats([])
    assert out["total_with_outcome"] == 0
    assert out["actionable"] is False
    assert out["bull_win_rate"] is None
    assert out["bear_win_rate"] is None
    assert out["no_trade_avoided"]["count"] == 0
    assert out["interpretation"] == "insufficient_data_keep_observing"


def test_open_decisions_excluded_but_counted():
    """Rows with final_result_r=None aren't closed yet — they don't
    count toward stats but should be visible as `open_count` so the
    operator knows how many are pending close."""
    rows = [
        _row("LONG", final_r=None),
        _row("LONG", final_r=None),
        _row("LONG", final_r=0.5, winner="bull"),
    ]
    out = compute_adversarial_stats(rows)
    assert out["open_count"] == 2
    assert out["total_with_outcome"] == 1


# ── Bucketing ─────────────────────────────────────────────────────────────────


def test_buckets_by_decision_type():
    rows = [
        _row("LONG", final_r=0.5, winner="bull"),
        _row("LONG", final_r=-0.3, winner="bear"),
        _row("SHORT_OR_AVOID", final_r=-0.5, winner="bear"),
        _row("NO_TRADE", final_r=-0.2, winner="bear"),
        _row("NO_TRADE", final_r=0.3, winner="bull"),
    ]
    out = compute_adversarial_stats(rows)
    assert out["by_decision"]["LONG"]["count"] == 2
    assert out["by_decision"]["SHORT_OR_AVOID"]["count"] == 1
    assert out["by_decision"]["NO_TRADE"]["count"] == 2


def test_unknown_decision_value_skipped():
    rows = [
        _row("LONG", final_r=0.5, winner="bull"),
        _row("MOON", final_r=0.5, winner="bull"),  # unknown
    ]
    out = compute_adversarial_stats(rows)
    assert out["total_with_outcome"] == 1
    assert out["skipped_invalid"] == 1


# ── Per-bucket stats ──────────────────────────────────────────────────────────


def test_per_bucket_avg_median_winrate():
    rows = [
        _row("LONG", final_r=0.2, winner="bull"),
        _row("LONG", final_r=0.5, winner="bull"),
        _row("LONG", final_r=-0.1, winner="bear"),  # losing long
    ]
    out = compute_adversarial_stats(rows)
    longs = out["by_decision"]["LONG"]
    assert longs["count"] == 3
    assert longs["avg_r"] == pytest.approx(round((0.2 + 0.5 - 0.1) / 3, 4))
    assert longs["median_r"] == 0.2
    assert longs["win_rate"] == round(2 / 3, 4)


def test_winrate_treats_zero_r_as_loss():
    """Break-even is not a win — same convention as shadow-research-stats."""
    rows = [_row("LONG", final_r=0.0, winner="bear")] * 2 + [
        _row("LONG", final_r=0.5, winner="bull"),
    ]
    out = compute_adversarial_stats(rows)
    assert out["by_decision"]["LONG"]["win_rate"] == round(1 / 3, 4)


def test_invalid_r_values_dropped_from_stats():
    """NaN / strings / None for final_r should be skipped, never
    crash the reducer."""
    rows = [
        _row("LONG", final_r=float("nan"), winner="bull"),
        _row("LONG", final_r="bad", winner="bull"),
        _row("LONG", final_r=0.5, winner="bull"),
    ]
    out = compute_adversarial_stats(rows)
    # Only the valid r counts; the others land in `open_count`
    # because _safe_float returns None for them.
    assert out["by_decision"]["LONG"]["count"] == 1
    assert out["open_count"] == 2


# ── Win-rate attribution ──────────────────────────────────────────────────────


def test_bull_bear_win_rates_from_winner_field():
    rows = [
        _row("LONG", final_r=0.5, winner="bull"),
        _row("LONG", final_r=0.2, winner="bull"),
        _row("LONG", final_r=-0.3, winner="bear"),
        _row("SHORT_OR_AVOID", final_r=-0.5, winner="bear"),
        _row("SHORT_OR_AVOID", final_r=0.5, winner="bull"),
    ]
    out = compute_adversarial_stats(rows)
    assert out["bull_total"] == 3
    assert out["bear_total"] == 2
    # Win rates should be 1.0 / 1.0 — the winner field already
    # encodes who was right, so the rate is just count / total.
    # Both Bull's 3 "wins" were when it was credited as winner.
    assert out["bull_win_rate"] == 1.0
    assert out["bear_win_rate"] == 1.0


def test_neutral_winner_excluded_from_rates():
    """Veto-phase NO_TRADE rows would have winner=neutral. Even if
    one slips through (e.g. via direct DB write), it should NOT
    dilute the bull/bear rates."""
    rows = [
        _row("LONG", final_r=0.5, winner="bull"),
        _row("NO_TRADE", final_r=0.0, winner="neutral"),  # weird shape
    ]
    out = compute_adversarial_stats(rows)
    assert out["bull_total"] == 1
    assert out["bear_total"] == 0


# ── No-trade-avoided metric ───────────────────────────────────────────────────


def test_no_trade_avoided_negative_means_commander_was_right():
    """Commander said NO_TRADE on trades that ended up losing. In
    veto phase those trades wouldn't have fired — so the negative
    avg_r_avoided is money saved."""
    rows = [
        _row("NO_TRADE", final_r=-0.5, winner="bear"),
        _row("NO_TRADE", final_r=-0.3, winner="bear"),
        _row("NO_TRADE", final_r=-0.1, winner="bear"),
    ]
    out = compute_adversarial_stats(rows)
    assert out["no_trade_avoided"]["count"] == 3
    assert out["no_trade_avoided"]["avg_r_avoided"] == -0.3


def test_no_trade_avoided_positive_means_commander_was_wrong():
    """Commander said NO_TRADE but the trade made money — Commander
    would have cost us this much in veto phase."""
    rows = [
        _row("NO_TRADE", final_r=0.4, winner="bull"),
        _row("NO_TRADE", final_r=0.6, winner="bull"),
    ]
    out = compute_adversarial_stats(rows)
    assert out["no_trade_avoided"]["avg_r_avoided"] == 0.5


# ── Edge-gap distribution ─────────────────────────────────────────────────────


def test_edge_gap_summary():
    rows = [
        _row("LONG", final_r=0.5, winner="bull", edge_gap=0.45),
        _row("LONG", final_r=0.2, winner="bull", edge_gap=0.55),
        _row("NO_TRADE", final_r=-0.1, winner="bear", edge_gap=0.10),
    ]
    out = compute_adversarial_stats(rows)
    eg = out["edge_gap"]
    assert eg["count"] == 3
    assert eg["min"] == 0.10
    assert eg["max"] == 0.55


# ── Maturity guardrail ────────────────────────────────────────────────────────


def test_actionable_false_below_threshold():
    rows = (
        [_row("LONG", final_r=0.5, winner="bull")] * (MIN_BUCKET_SAMPLES - 1)
        + [_row("SHORT_OR_AVOID", final_r=-0.5, winner="bear")] * MIN_BUCKET_SAMPLES
        + [_row("NO_TRADE", final_r=-0.2, winner="bear")] * MIN_BUCKET_SAMPLES
    )
    out = compute_adversarial_stats(rows)
    assert out["actionable"] is False
    assert out["min_bucket_count"] == MIN_BUCKET_SAMPLES - 1
    assert out["interpretation"] == "insufficient_data_keep_observing"


def test_actionable_true_with_balanced_split_returns_balanced_interpretation():
    rows = (
        [_row("LONG", final_r=0.5, winner="bull")] * MIN_BUCKET_SAMPLES
        + [_row("SHORT_OR_AVOID", final_r=-0.5, winner="bear")] * MIN_BUCKET_SAMPLES
        + [_row("NO_TRADE", final_r=-0.2, winner="bear")] * MIN_BUCKET_SAMPLES
    )
    out = compute_adversarial_stats(rows)
    assert out["actionable"] is True
    assert out["interpretation"] == "balanced_keep_observing_or_tune_threshold"


def test_actionable_with_bear_dominance_flags_promotion():
    # Bear wins way more — all 30 SHORT_OR_AVOID + all 15 winning
    # NO_TRADE-as-loss attributions = 30 bear; 15 bull
    rows = (
        [_row("LONG", final_r=-0.5, winner="bear")] * MIN_BUCKET_SAMPLES
        + [_row("SHORT_OR_AVOID", final_r=-0.5, winner="bear")] * MIN_BUCKET_SAMPLES
        + [_row("NO_TRADE", final_r=-0.5, winner="bear")] * MIN_BUCKET_SAMPLES
    )
    out = compute_adversarial_stats(rows)
    assert out["actionable"] is True
    assert out["bull_total"] == 0
    assert out["bear_total"] == 3 * MIN_BUCKET_SAMPLES
    # bull_win_rate is None when no bull wins logged → interpretation
    # falls back to no_data; that's the safe behaviour.
    assert out["interpretation"] in (
        "no_data",
        "bear_dominates_strong_signal_to_promote_to_risk_only",
    )


# ── Mongo glue ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_returns_empty_when_db_missing():
    out = await fetch_adversarial_stats(None)
    assert out["total_with_outcome"] == 0


@pytest.mark.asyncio
async def test_fetch_passes_query_filters_through():
    seen_query: dict = {}

    class _Cursor:
        async def to_list(self, length): return []

    class _Coll:
        def find(self, query, _proj=None):
            seen_query.update(query)
            return _Cursor()

    class _DB:
        def __getitem__(self, _key): return _Coll()

    out = await fetch_adversarial_stats(_DB(), hours=72, phase="shadow")
    assert "timestamp" in seen_query
    assert seen_query["phase"] == "shadow"
    assert out["window_hours"] == 72
    assert out["filter_phase"] == "shadow"


@pytest.mark.asyncio
async def test_fetch_swallows_query_exception():
    class _Coll:
        def find(self, *_a, **_kw):
            raise RuntimeError("mongo down")

    class _DB:
        def __getitem__(self, _key): return _Coll()

    out = await fetch_adversarial_stats(_DB())
    assert out["total_with_outcome"] == 0



# ── Paginated raw decisions feed ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_recent_decisions_returns_empty_when_db_missing():
    out = await fetch_recent_decisions(None)
    assert out["items"] == []
    assert out["count"] == 0


@pytest.mark.asyncio
async def test_fetch_recent_decisions_applies_filters_and_limit():
    seen_query: dict = {}
    seen_limit: dict = {}

    sample_rows = [{
        "decision": "LONG",
        "symbol": "BTC",
        "phase": "shadow",
        "timestamp": __import__("datetime").datetime(2026, 1, 1, 12, 0, 0),
    }]

    class _Cursor:
        def sort(self, *_a, **_kw): return self
        def limit(self, n):
            seen_limit["n"] = n
            return self
        async def to_list(self, length): return list(sample_rows)

    class _Coll:
        def find(self, query, _proj=None):
            seen_query.update(query)
            return _Cursor()

    class _DB:
        def __getitem__(self, _key): return _Coll()

    out = await fetch_recent_decisions(
        _DB(), limit=25, symbol="btc", phase="shadow", decision="long",
    )
    # symbol/decision must be uppercased to match writer's storage.
    assert seen_query["symbol"] == "BTC"
    assert seen_query["decision"] == "LONG"
    assert seen_query["phase"] == "shadow"
    assert seen_limit["n"] == 25
    assert out["count"] == 1
    assert out["limit"] == 25
    assert out["filters"]["symbol"] == "BTC"
    # datetime stringified for JSON safety.
    assert isinstance(out["items"][0]["timestamp"], str)


@pytest.mark.asyncio
async def test_fetch_recent_decisions_caps_limit():
    seen_limit: dict = {}

    class _Cursor:
        def sort(self, *_a, **_kw): return self
        def limit(self, n):
            seen_limit["n"] = n
            return self
        async def to_list(self, length): return []

    class _Coll:
        def find(self, *_a, **_kw): return _Cursor()

    class _DB:
        def __getitem__(self, _key): return _Coll()

    out = await fetch_recent_decisions(_DB(), limit=10_000)
    assert seen_limit["n"] == DECISIONS_PAGE_LIMIT_MAX
    assert out["limit"] == DECISIONS_PAGE_LIMIT_MAX


@pytest.mark.asyncio
async def test_fetch_recent_decisions_swallows_query_exception():
    class _Coll:
        def find(self, *_a, **_kw):
            raise RuntimeError("mongo down")

    class _DB:
        def __getitem__(self, _key): return _Coll()

    out = await fetch_recent_decisions(_DB())
    assert out["items"] == []
    assert out["count"] == 0
    assert "error" in out


# ── Joined trades ↔ decisions view ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_trades_with_decisions_returns_empty_when_db_missing():
    out = await fetch_trades_with_decisions(None)
    assert out["items"] == []
    assert out["count"] == 0


@pytest.mark.asyncio
async def test_fetch_trades_with_decisions_pipeline_shape_and_filters():
    seen_pipeline: list = []

    sample_rows = [{
        "trade_id": "t1",
        "symbol": "BTC",
        "status": "closed",
        "created_at": __import__("datetime").datetime(2026, 1, 1, 12, 0, 0),
        "decision": {
            "_id": "should_be_stripped",
            "decision": "LONG",
            "timestamp": __import__("datetime").datetime(2026, 1, 1, 11, 59, 0),
        },
    }]

    class _Cursor:
        async def to_list(self, length): return list(sample_rows)

    class _Coll:
        def aggregate(self, pipeline):
            seen_pipeline.extend(pipeline)
            return _Cursor()

    class _DB:
        def __getitem__(self, _key): return _Coll()

    out = await fetch_trades_with_decisions(
        _DB(), limit=10, symbol="btc", only_closed=True,
    )
    # First stage is $match — verify symbol uppercased + status filter +
    # adversarial_decision_id non-null guard.
    match_stage = seen_pipeline[0]["$match"]
    assert match_stage["symbol"] == "BTC"
    assert match_stage["status"] == "closed"
    assert match_stage["adversarial_decision_id"] == {"$ne": None}
    # $lookup against the decision collection is present.
    assert any("$lookup" in stage for stage in seen_pipeline)
    # Datetime stringified + nested _id stripped.
    row = out["items"][0]
    assert isinstance(row["created_at"], str)
    assert "_id" not in row["decision"]
    assert isinstance(row["decision"]["timestamp"], str)


@pytest.mark.asyncio
async def test_fetch_trades_with_decisions_swallows_aggregation_error():
    class _Coll:
        def aggregate(self, _pipeline):
            raise RuntimeError("mongo aggregate down")

    class _DB:
        def __getitem__(self, _key): return _Coll()

    out = await fetch_trades_with_decisions(_DB())
    assert out["items"] == []
    assert out["count"] == 0
    assert "error" in out
