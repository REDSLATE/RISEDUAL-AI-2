"""Tests for ``compute_tier3_breakdown`` — pin the admin detail
card's per-component progress contract."""
from services.tier3_readiness import (
    HIGH_CONF_THRESHOLD,  # noqa: F401  (used in fixtures via stats key)
    MAX_STRONG_MISS_RATE,
    MIN_DAYS,
    MIN_HIGH_CONF_SAMPLES,
    MIN_HIGH_CONF_WIN_RATE,
    MIN_TOTAL_TRADES,
    compute_tier3_breakdown,
    compute_tier3_score,
)


# ── Shape ──────────────────────────────────────────────────────────


def test_breakdown_returns_six_rows_in_stable_order():
    stats = {}  # all zeros
    rows = compute_tier3_breakdown(stats)
    keys = [r["key"] for r in rows]
    assert keys == [
        "exposure", "volume", "high_conf_accuracy",
        "risk_control", "stability", "canary",
    ]


def test_breakdown_each_row_has_required_keys():
    rows = compute_tier3_breakdown({})
    required = {
        "key", "label", "current", "target", "unit",
        "progress_pct", "weight_pct", "earned_pts", "met", "hint",
    }
    for row in rows:
        assert required.issubset(row.keys()), (
            f"row missing keys: {required - row.keys()}"
        )


def test_breakdown_weights_sum_to_100():
    rows = compute_tier3_breakdown({})
    assert sum(r["weight_pct"] for r in rows) == 100


# ── Earned points reconcile to composite ───────────────────────────


def test_breakdown_earned_pts_match_composite_score():
    """The sum of per-component earned_pts MUST equal the composite
    score from ``compute_tier3_score``. If they diverge, the operator
    sees inconsistent numbers."""
    stats = {
        "days": 20,
        "total_trades": 50,
        "high_conf_trades": 35,
        "high_conf_win_rate": 0.70,
        "strong_miss_rate": 0.05,
        "last_7d_win_rate": 0.55,
        "clamp_total": 0,
    }
    rows = compute_tier3_breakdown(stats)
    earned_sum = sum(r["earned_pts"] for r in rows)
    composite = compute_tier3_score(stats)
    assert abs(earned_sum - composite) < 0.5, (
        f"earned_pts sum ({earned_sum}) diverged from composite "
        f"score ({composite}) — bars and headline number disagree"
    )


# ── Per-component progress ─────────────────────────────────────────


def test_exposure_progress_capped_at_100():
    rows = compute_tier3_breakdown({"days": 90})  # 3× the target
    exposure = next(r for r in rows if r["key"] == "exposure")
    assert exposure["progress_pct"] == 100.0
    assert exposure["earned_pts"] == 20  # full weight


def test_volume_progress_partial():
    rows = compute_tier3_breakdown({"total_trades": MIN_TOTAL_TRADES // 2})
    volume = next(r for r in rows if r["key"] == "volume")
    assert volume["progress_pct"] == 50.0
    assert volume["earned_pts"] == 7.5  # half of 15-pt weight
    assert volume["met"] is False


def test_high_conf_gated_by_sample_size():
    """Even at 100% win rate, fewer than MIN_HIGH_CONF_SAMPLES
    means the bar shows partial progress (sample fraction is the
    BINDING constraint) and the hint surfaces the sample shortfall.

    Earned points still mirror the composite formula
    (``high_conf_wr * 25``) so the bars reconcile to the headline
    score — the composite gives credit for the win rate even before
    samples clear; only ``check_tier3_unlock`` blocks the actual
    unlock based on samples."""
    rows = compute_tier3_breakdown({
        "high_conf_trades": MIN_HIGH_CONF_SAMPLES // 2,
        "high_conf_win_rate": 1.0,  # perfect — but not enough samples
    })
    hc = next(r for r in rows if r["key"] == "high_conf_accuracy")
    assert hc["progress_pct"] == 50.0  # 15/30 samples
    assert hc["earned_pts"] == 25.0  # 1.0 × 25 — composite credit
    assert hc["met"] is False
    assert "high-conf samples" in hc["hint"]


def test_high_conf_full_credit_when_sample_and_wr_clear():
    """When samples + wr both clear thresholds, ``met`` flips True.
    The visual bar tracks wr-as-pct (so 75% shows as 75) — that's
    the same scale the composite scoring uses."""
    rows = compute_tier3_breakdown({
        "high_conf_trades": MIN_HIGH_CONF_SAMPLES,
        "high_conf_win_rate": MIN_HIGH_CONF_WIN_RATE,
    })
    hc = next(r for r in rows if r["key"] == "high_conf_accuracy")
    assert hc["progress_pct"] == 75.0  # win rate as percentage
    assert hc["earned_pts"] == 18.75   # 0.75 × 25 weight
    assert hc["met"] is True


def test_high_conf_perfect_wr_shows_100_progress():
    rows = compute_tier3_breakdown({
        "high_conf_trades": MIN_HIGH_CONF_SAMPLES,
        "high_conf_win_rate": 1.0,
    })
    hc = next(r for r in rows if r["key"] == "high_conf_accuracy")
    assert hc["progress_pct"] == 100.0
    assert hc["earned_pts"] == 25.0  # full weight


def test_risk_control_inverts_miss_rate():
    """Lower miss rate → more progress. ``strong_miss_rate=0.05``
    means 95% of the risk-control headroom earned."""
    rows = compute_tier3_breakdown({"strong_miss_rate": 0.05})
    risk = next(r for r in rows if r["key"] == "risk_control")
    assert risk["progress_pct"] == 95.0
    assert risk["met"] is True  # 5% < 10% MAX_STRONG_MISS_RATE


def test_risk_control_fails_at_high_miss():
    rows = compute_tier3_breakdown({"strong_miss_rate": 0.20})
    risk = next(r for r in rows if r["key"] == "risk_control")
    assert risk["progress_pct"] == 80.0
    assert risk["met"] is False


def test_canary_binary():
    """Canary is 100% or 0% — any clamp event blocks Tier 3."""
    clean = compute_tier3_breakdown({"clamp_total": 0})
    tripped = compute_tier3_breakdown({"clamp_total": 1})
    canary_clean = next(r for r in clean if r["key"] == "canary")
    canary_tripped = next(r for r in tripped if r["key"] == "canary")
    assert canary_clean["progress_pct"] == 100.0
    assert canary_clean["met"] is True
    assert canary_tripped["progress_pct"] == 0.0
    assert canary_tripped["met"] is False
    assert "Tier 3 blocked" in canary_tripped["hint"]


# ── Defensive ──────────────────────────────────────────────────────


def test_breakdown_handles_missing_keys_gracefully():
    """Empty stats dict (cold-start) should not raise; every gate
    reports 0 progress except the canary (which defaults to clean)."""
    rows = compute_tier3_breakdown({})
    pct_by_key = {r["key"]: r["progress_pct"] for r in rows}
    assert pct_by_key["exposure"] == 0.0
    assert pct_by_key["volume"] == 0.0
    assert pct_by_key["high_conf_accuracy"] == 0.0
    assert pct_by_key["risk_control"] == 0.0  # default miss_rate=1.0
    assert pct_by_key["stability"] == 0.0
    # Canary defaults to clean since there are no clamp events.
    assert pct_by_key["canary"] == 100.0


def test_breakdown_clamps_overshoot_to_100():
    """``high_conf_win_rate`` of 1.0 against a 0.75 target shouldn't
    return 133%; the bar caps at 100."""
    rows = compute_tier3_breakdown({
        "high_conf_trades": MIN_HIGH_CONF_SAMPLES,
        "high_conf_win_rate": 1.0,
    })
    hc = next(r for r in rows if r["key"] == "high_conf_accuracy")
    assert hc["progress_pct"] <= 100.0


def test_breakdown_thresholds_match_unlock_logic():
    """Sanity: if every gate's ``met`` is True, the same stats dict
    must also unlock Tier 3 in ``check_tier3_unlock``."""
    from services.tier3_readiness import check_tier3_unlock
    stats = {
        "days": MIN_DAYS,
        "total_trades": MIN_TOTAL_TRADES,
        "high_conf_trades": MIN_HIGH_CONF_SAMPLES,
        "high_conf_win_rate": MIN_HIGH_CONF_WIN_RATE,
        "avg_confidence": MIN_HIGH_CONF_WIN_RATE * 100,  # calibrated
        "strong_miss_rate": MAX_STRONG_MISS_RATE,
        "last_7d_win_rate": 0.6,
        "overall_win_rate": 0.6,
        "clamp_total": 0,
    }
    rows = compute_tier3_breakdown(stats)
    # All "hard" gates met (stability is soft so it doesn't have to
    # be 100%). Unlock should match.
    hard_gates_met = all(r["met"] for r in rows if r["key"] != "stability")
    unlock = check_tier3_unlock(stats)["unlocked"]
    assert hard_gates_met == unlock
