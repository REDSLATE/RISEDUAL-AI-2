"""Tests for ``services.tier3_slippage_advisor``.

Pins these invariants:

1. ``analyze_slippage_segments`` is read-only and returns sane
   shapes even when DB is empty.
2. ``detect_outliers`` respects MIN_SEGMENT_SAMPLE_SIZE — a
   segment with n=5 NEVER produces a proposal regardless of bps.
3. ``detect_outliers`` respects ABSOLUTE_BPS_FLOOR — tiny absolute
   bps (e.g. 2bps × 2 baseline = 4bps) doesn't produce a proposal.
4. ``draft_proposals`` produces one proposal per outlier with the
   correct severity bucket (info / warn / critical).
5. ``run_advisor_cycle`` is idempotent — re-running in the same
   ISO week against an unchanged DB does NOT insert duplicates.
6. ``update_proposal_status`` round-trips and stamps reviewer.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from services import tier3_slippage_advisor as adv


# ── In-memory fake DB ────────────────────────────────────────────


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def __aiter__(self):
        async def _g():
            for r in self._rows:
                yield r
        return _g()

    def sort(self, *_a, **_k):
        return self

    def limit(self, n):
        self._rows = list(self._rows)[:n]
        return self

    async def to_list(self, length=None):  # noqa: ARG002
        return list(self._rows)


class _FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []

    def find(self, query, projection=None):  # noqa: ARG002
        matched = []
        for r in self.rows:
            ok = True
            for k, v in query.items():
                if isinstance(v, dict) and "$gte" in v:
                    val = r.get(k)
                    if not isinstance(val, datetime) or val < v["$gte"]:
                        ok = False; break
                elif r.get(k) != v:
                    ok = False; break
            if ok:
                matched.append(r)
        return _FakeCursor(matched)

    async def find_one(self, query, projection=None):  # noqa: ARG002
        for r in self.rows:
            if all(r.get(k) == v for k, v in query.items()):
                return r
        return None

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    async def find_one_and_update(
        self, query, update, return_document=True, projection=None,
    ):  # noqa: ARG002
        for r in self.rows:
            if all(r.get(k) == v for k, v in query.items()):
                r.update(update.get("$set", {}))
                return dict(r)
        return None


class _FakeDB:
    def __init__(self):
        self.paper_trades = _FakeCollection()
        self.crypto_paper_trades = _FakeCollection()
        self.tier3_advisor_proposals = _FakeCollection()

    def __getitem__(self, name):
        return getattr(self, name)


def _stamp_trade(*, lane: str, **kw) -> dict:
    """Build a closed trade with slippage stamps applied."""
    base = {
        "status": "closed",
        "closed_at": datetime.now(timezone.utc),
        "opened_at": datetime.now(timezone.utc) - timedelta(hours=2),
        "direction": "LONG",
        "slippage_method": "ask_fill",
        "slippage_bps": 5.0,
        "exit_slippage_method": "bid_fill",
        "exit_slippage_bps": 5.0,
        "pnl_usd": 0.0,
        "shares": 10, "entry_price": 100.0,
        "symbol": "AAPL",
        "ticker": "AAPL",
    }
    if lane == "crypto":
        base = {
            **base, "position_size_usd": 1000.0,
            "symbol": "BTC", "ticker": None, "pnl": 0.0,
        }
    base.update(kw)
    return base


# ── Empty DB ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analyze_empty_db_returns_zeroes():
    db = _FakeDB()
    out = await adv.analyze_slippage_segments(db)
    assert out["trades_scanned"] == 0
    assert out["trades_with_slippage"] == 0
    assert out["segments"] == []


@pytest.mark.asyncio
async def test_analyze_skips_rows_without_slippage_stamp():
    db = _FakeDB()
    # Row WITHOUT a slippage_method should be excluded.
    db.paper_trades.rows.append({
        "status": "closed",
        "closed_at": datetime.now(timezone.utc),
        "ticker": "AAPL", "pnl_usd": 5.0,
    })
    db.paper_trades.rows.append(_stamp_trade(lane="equity"))
    out = await adv.analyze_slippage_segments(db)
    assert out["trades_scanned"] == 2
    assert out["trades_with_slippage"] == 1


# ── Segmentation math ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_segment_aggregation_counts_per_axis():
    db = _FakeDB()
    # 3 AAPL ask_fill trades, 2 TSLA ask_fill trades.
    for _ in range(3):
        db.paper_trades.rows.append(_stamp_trade(
            lane="equity", symbol="AAPL", ticker="AAPL",
        ))
    for _ in range(2):
        db.paper_trades.rows.append(_stamp_trade(
            lane="equity", symbol="TSLA", ticker="TSLA",
        ))
    out = await adv.analyze_slippage_segments(db)
    sym_segs = {s["value"]: s for s in out["segments"] if s["axis"] == "symbol"}
    assert sym_segs["AAPL"]["count"] == 3
    assert sym_segs["TSLA"]["count"] == 2


# ── Outlier detection gates ──────────────────────────────────────


def test_outlier_gate_sample_size():
    """5 trades with high bps → still no proposal (n < MIN floor)."""
    segments = [{
        "axis": "symbol", "value": "AAPL", "segment_key": "k1",
        "count": 5,  # below MIN_SEGMENT_SAMPLE_SIZE
        "avg_bps": 50.0, "total_dollar_cost": 100.0, "drag_pct_of_pnl": 5.0,
    }]
    baseline = {"avg_bps": 5.0}
    outliers = adv.detect_outliers(segments, baseline)
    assert outliers == []


def test_outlier_gate_absolute_floor():
    """30 trades but only 4bps avg → below ABSOLUTE_BPS_FLOOR."""
    segments = [{
        "axis": "symbol", "value": "AAPL", "segment_key": "k1",
        "count": 30, "avg_bps": 4.0, "total_dollar_cost": 1.0,
        "drag_pct_of_pnl": 0.1,
    }]
    baseline = {"avg_bps": 1.0}
    outliers = adv.detect_outliers(segments, baseline)
    assert outliers == []


def test_outlier_gate_passes_when_meaningful():
    segments = [{
        "axis": "symbol", "value": "TSLA", "segment_key": "k2",
        "count": 50, "avg_bps": 25.0, "total_dollar_cost": 100.0,
        "drag_pct_of_pnl": 12.0,
    }]
    baseline = {"avg_bps": 5.0}
    outliers = adv.detect_outliers(segments, baseline)
    assert len(outliers) == 1
    assert outliers[0]["ratio"] == pytest.approx(5.0, abs=0.01)


# ── Severity bucket ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "ratio, expected",
    [(1.8, "info"), (2.5, "warn"), (3.5, "critical")],
)
def test_severity_buckets(ratio, expected):
    assert adv._severity_from_ratio(ratio) == expected


# ── Proposal drafting ────────────────────────────────────────────


def test_draft_proposals_one_per_outlier():
    outliers = [
        {"axis": "symbol", "value": "TSLA", "segment_key": "k1",
         "count": 50, "avg_bps": 30.0, "total_dollar_cost": 100.0,
         "drag_pct_of_pnl": 12.0, "ratio": 5.0,
         "baseline_avg_bps": 6.0, "outlier_threshold_bps": 10.0},
        {"axis": "session", "value": "pre", "segment_key": "k2",
         "count": 25, "avg_bps": 18.0, "total_dollar_cost": 30.0,
         "drag_pct_of_pnl": 4.0, "ratio": 2.5,
         "baseline_avg_bps": 7.0, "outlier_threshold_bps": 12.0},
    ]
    proposals = adv.draft_proposals(outliers, lookback_days=30)
    assert len(proposals) == 2
    sym_p = next(p for p in proposals if p["axis"] == "symbol")
    assert sym_p["severity"] == "critical"
    assert "TSLA" in sym_p["rationale"]
    assert sym_p["env_knob"] == "TICKER_ABANDONMENT_LIST"


def test_draft_proposals_skips_unknown_axis():
    outliers = [{
        "axis": "weird_unknown_axis", "value": "X", "segment_key": "k",
        "count": 50, "avg_bps": 30.0, "total_dollar_cost": 100.0,
        "drag_pct_of_pnl": 5.0, "ratio": 5.0,
        "baseline_avg_bps": 6.0, "outlier_threshold_bps": 10.0,
    }]
    assert adv.draft_proposals(outliers, lookback_days=30) == []


# ── Idempotent end-to-end ────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_cycle_dedupes_within_iso_week():
    db = _FakeDB()
    # 25 high-drag TSLA trades.
    for _ in range(25):
        db.paper_trades.rows.append(_stamp_trade(
            lane="equity", symbol="TSLA", ticker="TSLA",
            slippage_bps=30.0, exit_slippage_bps=20.0,
            shares=10, entry_price=100.0,
        ))
    # 60 quiet baseline trades on AAPL — pushes the global baseline
    # below the per-symbol threshold so TSLA stands out as outlier.
    for _ in range(60):
        db.paper_trades.rows.append(_stamp_trade(
            lane="equity", symbol="AAPL", ticker="AAPL",
            slippage_bps=1.0, exit_slippage_bps=1.0,
        ))

    first = await adv.run_advisor_cycle(db, lookback_days=30)
    assert first["inserted"] >= 1
    starting = len(db.tier3_advisor_proposals.rows)

    second = await adv.run_advisor_cycle(db, lookback_days=30)
    assert second["inserted"] == 0
    assert second["deduped"] >= 1
    # No new rows added on repeat run.
    assert len(db.tier3_advisor_proposals.rows) == starting


# ── Status update round-trip ─────────────────────────────────────


@pytest.mark.asyncio
async def test_status_update_round_trip():
    db = _FakeDB()
    db.tier3_advisor_proposals.rows.append({
        "segment_key": "abc", "generated_week": "2026-W18",
        "status": "pending", "rationale": "x",
    })
    res = await adv.update_proposal_status(
        db, segment_key="abc", generated_week="2026-W18",
        new_status="accepted", actor="owner@risedual.ai",
    )
    assert res is not None
    assert res["status"] == "accepted"
    assert res["reviewed_by"] == "owner@risedual.ai"


@pytest.mark.asyncio
async def test_status_update_invalid_status_returns_none():
    db = _FakeDB()
    res = await adv.update_proposal_status(
        db, segment_key="abc", generated_week="2026-W18",
        new_status="invalid_status",
    )
    assert res is None


@pytest.mark.asyncio
async def test_status_update_missing_proposal_returns_none():
    db = _FakeDB()
    res = await adv.update_proposal_status(
        db, segment_key="missing", generated_week="2026-W01",
        new_status="accepted",
    )
    assert res is None
