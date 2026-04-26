"""Tests for `services.crypto_shadow_research_stats`.

Pins down four invariants:

1. **Bucketing** — closed trades are split correctly by
   ``web_research_shadow_verdict.agreement`` and trades without a
   verdict (or with an invalid one) are counted into
   ``skipped_invalid_verdict``.

2. **Per-bucket stats** — ``count``, ``avg_r``, ``median_r``,
   ``win_rate`` are computed correctly, including the even/odd median
   edge cases and the empty-bucket null contract.

3. **Maturity guardrail** — ``actionable=False`` until every bucket
   has ≥ ``MIN_BUCKET_SAMPLES`` rows. This is the operational
   guardrail that prevents premature promotion to a live factor.

4. **Lift + interpretation** — derived ``lift`` and the operator-
   readable interpretation string track the documented thresholds
   (``>= 0.10`` = useful, ``<= -0.10`` = harmful).
"""
from __future__ import annotations

import pytest

from services.crypto_shadow_research_stats import (
    MIN_BUCKET_SAMPLES,
    compute_shadow_research_stats,
    fetch_shadow_research_stats,
)


def _row(agreement: str | None, r: float | None) -> dict:
    """Tiny factory — builds a closed crypto_paper_trades row with
    just the fields the stats reducer reads."""
    if agreement is None:
        return {"r_multiple": r, "web_research_shadow_verdict": None}
    return {
        "r_multiple": r,
        "web_research_shadow_verdict": {
            "agreement": agreement,
            "stance": "BULLISH" if agreement == "agree" else "BEARISH",
        },
    }


# ── Empty / no-data ───────────────────────────────────────────────────────────


def test_empty_input_returns_zeroed_payload():
    out = compute_shadow_research_stats([])
    assert out["total"] == 0
    assert out["lift"] is None
    assert out["actionable"] is False
    assert out["interpretation"] == "insufficient_data_keep_observing"
    for bucket in ("agree", "disagree", "neutral"):
        b = out["buckets"][bucket]
        assert b["count"] == 0
        assert b["avg_r"] is None
        assert b["median_r"] is None
        assert b["win_rate"] is None


# ── Bucketing ─────────────────────────────────────────────────────────────────


def test_rows_routed_to_correct_buckets():
    rows = [
        _row("agree", 0.5), _row("agree", 1.0),
        _row("disagree", -0.4),
        _row("neutral", 0.1), _row("neutral", -0.2), _row("neutral", 0.3),
    ]
    out = compute_shadow_research_stats(rows)
    assert out["total"] == 6
    assert out["buckets"]["agree"]["count"] == 2
    assert out["buckets"]["disagree"]["count"] == 1
    assert out["buckets"]["neutral"]["count"] == 3
    assert out["skipped_invalid_verdict"] == 0


def test_missing_or_unknown_agreement_is_skipped():
    rows = [
        _row("agree", 0.5),
        _row(None, 1.0),                   # no verdict
        {"r_multiple": 0.3,                # malformed verdict
         "web_research_shadow_verdict": "not-a-dict"},
        {"r_multiple": 0.2,                # unknown agreement label
         "web_research_shadow_verdict": {"agreement": "MOON"}},
    ]
    out = compute_shadow_research_stats(rows)
    assert out["total"] == 1
    assert out["skipped_invalid_verdict"] == 3


# ── Per-bucket stats ──────────────────────────────────────────────────────────


def test_avg_median_winrate_for_odd_count():
    rows = [
        _row("agree", 0.2),
        _row("agree", 0.5),
        _row("agree", -0.1),  # one loss
    ]
    b = compute_shadow_research_stats(rows)["buckets"]["agree"]
    assert b["count"] == 3
    assert b["avg_r"] == round((0.2 + 0.5 - 0.1) / 3, 4)
    assert b["median_r"] == 0.2
    assert b["win_rate"] == round(2 / 3, 4)


def test_median_for_even_count_is_average_of_two_middles():
    rows = [
        _row("disagree", -0.5),
        _row("disagree", 0.0),
        _row("disagree", 0.2),
        _row("disagree", 0.6),
    ]
    b = compute_shadow_research_stats(rows)["buckets"]["disagree"]
    # sorted: [-0.5, 0.0, 0.2, 0.6] → middles = (0.0 + 0.2)/2 = 0.1
    assert b["median_r"] == 0.1


def test_winrate_treats_zero_r_as_loss():
    """A break-even trade isn't a win — it didn't beat the entry risk
    floor. Pinned because it's an easy off-by-one."""
    rows = [
        _row("agree", 0.0),
        _row("agree", 0.0),
        _row("agree", 0.5),
    ]
    b = compute_shadow_research_stats(rows)["buckets"]["agree"]
    assert b["win_rate"] == round(1 / 3, 4)


def test_rows_with_missing_r_multiple_are_dropped_from_bucket():
    rows = [
        _row("agree", 0.5),
        _row("agree", None),     # missing R is dropped from the average
        _row("agree", "bad"),    # type-invalid R is dropped
    ]
    b = compute_shadow_research_stats(rows)["buckets"]["agree"]
    assert b["count"] == 1
    assert b["avg_r"] == 0.5


# ── Maturity guardrail ────────────────────────────────────────────────────────


def test_actionable_false_below_threshold():
    rows = (
        [_row("agree", 0.3)] * (MIN_BUCKET_SAMPLES - 1)
        + [_row("disagree", -0.3)] * MIN_BUCKET_SAMPLES
        + [_row("neutral", 0.0)] * MIN_BUCKET_SAMPLES
    )
    out = compute_shadow_research_stats(rows)
    assert out["actionable"] is False
    assert out["min_bucket_count"] == MIN_BUCKET_SAMPLES - 1
    assert out["interpretation"] == "insufficient_data_keep_observing"


def test_actionable_true_when_all_buckets_meet_threshold():
    rows = (
        [_row("agree", 0.5)] * MIN_BUCKET_SAMPLES
        + [_row("disagree", -0.5)] * MIN_BUCKET_SAMPLES
        + [_row("neutral", 0.0)] * MIN_BUCKET_SAMPLES
    )
    out = compute_shadow_research_stats(rows)
    assert out["actionable"] is True
    assert out["min_bucket_count"] == MIN_BUCKET_SAMPLES
    assert out["lift"] == 1.0  # 0.5 - (-0.5)
    assert out["interpretation"] == "research_useful_consider_promoting"


# ── Lift + interpretation thresholds ──────────────────────────────────────────


def test_lift_positive_above_threshold_flags_useful():
    rows = (
        [_row("agree", 0.4)] * MIN_BUCKET_SAMPLES
        + [_row("disagree", 0.2)] * MIN_BUCKET_SAMPLES
        + [_row("neutral", 0.0)] * MIN_BUCKET_SAMPLES
    )
    out = compute_shadow_research_stats(rows)
    assert out["lift"] == 0.2
    assert out["interpretation"] == "research_useful_consider_promoting"


def test_lift_negative_flags_harmful():
    rows = (
        [_row("agree", -0.3)] * MIN_BUCKET_SAMPLES
        + [_row("disagree", 0.2)] * MIN_BUCKET_SAMPLES
        + [_row("neutral", 0.0)] * MIN_BUCKET_SAMPLES
    )
    out = compute_shadow_research_stats(rows)
    assert out["lift"] == -0.5
    assert out["interpretation"] == "research_harmful_keep_shadow_only"


def test_lift_in_neutral_band_flags_drop_or_observe():
    rows = (
        [_row("agree", 0.05)] * MIN_BUCKET_SAMPLES
        + [_row("disagree", 0.0)] * MIN_BUCKET_SAMPLES
        + [_row("neutral", 0.0)] * MIN_BUCKET_SAMPLES
    )
    out = compute_shadow_research_stats(rows)
    assert -0.10 < out["lift"] < 0.10
    assert out["interpretation"] == "research_neutral_drop_or_observe_more"


def test_lift_is_none_when_one_bucket_is_empty():
    rows = [_row("agree", 0.5)] * MIN_BUCKET_SAMPLES + [_row("neutral", 0.0)] * MIN_BUCKET_SAMPLES
    out = compute_shadow_research_stats(rows)
    assert out["lift"] is None
    assert out["actionable"] is False


# ── Mongo glue ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_returns_empty_summary_when_db_missing():
    out = await fetch_shadow_research_stats(None)
    assert out["total"] == 0
    assert out["actionable"] is False


@pytest.mark.asyncio
async def test_fetch_passes_rows_through_to_compute():
    """Verify the Mongo glue layer queries on ``status="closed"`` AND
    a non-null shadow verdict, and that the result of the cursor is
    forwarded into the pure compute function."""
    seen_query: dict = {}

    class _Cursor:
        def __init__(self, rows): self._rows = rows
        async def to_list(self, length): return self._rows

    class _Coll:
        async def find(self, query, _proj=None):
            seen_query.update(query)
            return _Cursor([
                _row("agree", 0.5), _row("disagree", -0.4),
                _row("neutral", 0.1),
            ])

        # Find returns a cursor synchronously in real Motor — emulate.
        # (Motor's find returns a cursor synchronously, not an awaitable.)

    class _SyncCursorColl:
        def find(self, query, _proj=None):
            seen_query.update(query)
            return _Cursor([
                _row("agree", 0.5), _row("disagree", -0.4),
                _row("neutral", 0.1),
            ])

    class _DB:
        def __getitem__(self, _key): return _SyncCursorColl()

    out = await fetch_shadow_research_stats(_DB())
    assert seen_query["status"] == "closed"
    assert "web_research_shadow_verdict" in seen_query
    assert out["total"] == 3


@pytest.mark.asyncio
async def test_fetch_with_hours_window_adds_closed_at_filter():
    seen_query: dict = {}

    class _Cursor:
        async def to_list(self, length): return []

    class _Coll:
        def find(self, query, _proj=None):
            seen_query.update(query)
            return _Cursor()

    class _DB:
        def __getitem__(self, _key): return _Coll()

    out = await fetch_shadow_research_stats(_DB(), hours=168)
    assert "closed_at" in seen_query
    assert "$gte" in seen_query["closed_at"]
    assert out["window_hours"] == 168


@pytest.mark.asyncio
async def test_fetch_swallows_query_exception():
    class _Coll:
        def find(self, *_a, **_kw):
            raise RuntimeError("mongo down")

    class _DB:
        def __getitem__(self, _key): return _Coll()

    # Must not raise — the admin tile is non-critical.
    out = await fetch_shadow_research_stats(_DB())
    assert out["total"] == 0
