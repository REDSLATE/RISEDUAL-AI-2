"""Tests for the Research Shadow framework.

Coverage:

* :mod:`services.research_shadow` — pure helpers (canonicalisation,
  dissent detection, gate logic, PnL math).
* :mod:`services.research_shadow_engines` — engine wrappers + fire
  helper.
* :mod:`services.research_shadow_scorer` — pure scoring helpers.
* :mod:`services.research_shadow_stats` — pure stats reducer.

Mongo-glue tests use minimal stub objects (``_DB``, ``_Coll``,
``_Cursor``) — no live Mongo handle, no live LLM call. Keeps the
suite fast and CI-deterministic.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from services.research_shadow import (
    ENGINE_ADVERSARIAL,
    ENGINE_COUNCIL,
    ENGINE_NONE,
    FILL_COST_BPS,
    MIN_DISSENT_SAMPLES,
    ShadowDecision,
    canonicalise_action,
    detect_dissent,
    hypothetical_pnl_usd,
    should_fire_shadow,
)


# ── Action canonicalisation ───────────────────────────────────────────────────


def test_canonicalise_known_actions():
    assert canonicalise_action("BUY") == "LONG"
    assert canonicalise_action("LONG") == "LONG"
    assert canonicalise_action("SELL") == "SHORT"
    assert canonicalise_action("SHORT_OR_AVOID") == "SHORT"
    assert canonicalise_action("HOLD") == "HOLD"
    assert canonicalise_action("WAIT") == "HOLD"
    assert canonicalise_action("NO_TRADE") == "HOLD"
    assert canonicalise_action("CLOSE") == "CLOSE"
    assert canonicalise_action("EXIT") == "CLOSE"


def test_canonicalise_unknown_falls_to_hold():
    assert canonicalise_action("MOON") == "HOLD"
    assert canonicalise_action("") == "HOLD"
    assert canonicalise_action(None) == "HOLD"


def test_canonicalise_is_case_insensitive_and_trims():
    assert canonicalise_action(" long ") == "LONG"
    assert canonicalise_action("Buy") == "LONG"


# ── Dissent detection ─────────────────────────────────────────────────────────


def test_dissent_same_action():
    assert detect_dissent("LONG", "LONG") is False
    assert detect_dissent("HOLD", "WAIT") is False  # canonicalises same


def test_dissent_different_actions():
    assert detect_dissent("LONG", "HOLD") is True
    assert detect_dissent("LONG", "SHORT") is True
    assert detect_dissent("HOLD", "CLOSE") is True


def test_dissent_handles_engine_specific_spellings():
    # adversarial commander emits SHORT_OR_AVOID; council emits SHORT.
    # They should NOT register as dissents (semantic equivalence).
    assert detect_dissent("SHORT_OR_AVOID", "SHORT") is False
    # NO_TRADE vs HOLD — same canonical bucket.
    assert detect_dissent("NO_TRADE", "HOLD") is False


# ── Should-fire gate ──────────────────────────────────────────────────────────


def test_gate_rejects_invalid_engine():
    fire, reason = should_fire_shadow(
        shadow_engine=ENGINE_NONE,
        last_shadow_ts=None,
        daily_cost_usd=0.0,
        decision_phase="entry",
    )
    assert fire is False
    assert reason == "invalid_engine"


def test_gate_adversarial_always_fires():
    """Adversarial shadow is deterministic (no LLM cost) — gates
    that exist for cost control don't apply."""
    fire, reason = should_fire_shadow(
        shadow_engine=ENGINE_ADVERSARIAL,
        last_shadow_ts=datetime.now(timezone.utc),
        daily_cost_usd=999.0,  # would block a council shadow
        decision_phase="cycle",
    )
    assert fire is True
    assert reason == "ok"


def test_gate_council_rate_limited():
    now = datetime.now(timezone.utc)
    fire, reason = should_fire_shadow(
        shadow_engine=ENGINE_COUNCIL,
        last_shadow_ts=now - timedelta(seconds=5),
        daily_cost_usd=0.0,
        decision_phase="entry",
        now=now,
    )
    assert fire is False
    assert reason == "rate_limited"


def test_gate_council_full_under_threshold():
    now = datetime.now(timezone.utc)
    fire, reason = should_fire_shadow(
        shadow_engine=ENGINE_COUNCIL,
        last_shadow_ts=now - timedelta(minutes=10),
        daily_cost_usd=1.0,  # well under $5 ceiling
        decision_phase="entry",
        now=now,
    )
    assert fire is True
    assert reason == "ok"


def test_gate_council_degraded_skips_cycle_keeps_entry():
    # Set spend in the 80-100% band of the default $5 ceiling = $4-$5.
    now = datetime.now(timezone.utc)
    fire_cycle, reason_cycle = should_fire_shadow(
        shadow_engine=ENGINE_COUNCIL,
        last_shadow_ts=now - timedelta(minutes=10),
        daily_cost_usd=4.5,
        decision_phase="cycle",
        now=now,
    )
    assert fire_cycle is False
    assert reason_cycle == "cost_ceiling_degraded"

    fire_entry, reason_entry = should_fire_shadow(
        shadow_engine=ENGINE_COUNCIL,
        last_shadow_ts=now - timedelta(minutes=10),
        daily_cost_usd=4.5,
        decision_phase="entry",
        now=now,
    )
    assert fire_entry is True
    assert reason_entry == "ok_degraded"


def test_gate_council_paused_at_ceiling():
    now = datetime.now(timezone.utc)
    fire, reason = should_fire_shadow(
        shadow_engine=ENGINE_COUNCIL,
        last_shadow_ts=now - timedelta(minutes=10),
        daily_cost_usd=10.0,  # well over $5 ceiling
        decision_phase="entry",
        now=now,
    )
    assert fire is False
    assert reason == "cost_ceiling_paused"


# ── ShadowDecision dataclass ──────────────────────────────────────────────────


def test_shadow_decision_canonicalises_inputs_and_flags_dissent():
    d = ShadowDecision(
        bot_id="b1", user_id="u1", symbol="BTC", asset_type="crypto",
        decision_phase="entry",
        active_engine="confluence", active_action="BUY",
        shadow_engine="adversarial", shadow_action="HOLD",
        mid_price=77_000.0,
        sim_fill_bps_round_trip=20,
    )
    assert d.active_action == "LONG"  # canonicalised
    assert d.shadow_action == "HOLD"
    assert d.is_dissent is True


def test_shadow_decision_to_doc_drops_none_optionals_and_marks_firewall():
    d = ShadowDecision(
        bot_id="b1", user_id="u1", symbol="BTC", asset_type="crypto",
        decision_phase="entry",
        active_engine="confluence", active_action="LONG",
        shadow_engine="adversarial", shadow_action="LONG",
        mid_price=77_000.0,
        sim_fill_bps_round_trip=20,
    )
    doc = d.to_doc()
    assert doc["tier3_firewall"] is True
    # Required fields present.
    assert doc["decision_id"] == d.decision_id
    assert doc["is_dissent"] is False
    # None-valued optionals omitted.
    assert "trade_id" not in doc
    assert "tactical_score" not in doc


# ── PnL math ──────────────────────────────────────────────────────────────────


def test_pnl_long_wins_after_cost():
    # Up 1% on $1000 notional → +$10 raw. Minus 20bps cost ($2) = $8.
    pnl = hypothetical_pnl_usd(
        action="LONG", entry_price=100.0, exit_price=101.0,
        notional_usd=1000.0, fill_cost_bps=20,
    )
    assert pnl == pytest.approx(8.0, abs=0.001)


def test_pnl_short_wins_after_cost():
    pnl = hypothetical_pnl_usd(
        action="SHORT", entry_price=100.0, exit_price=99.0,
        notional_usd=1000.0, fill_cost_bps=20,
    )
    assert pnl == pytest.approx(8.0, abs=0.001)


def test_pnl_hold_close_zero():
    assert hypothetical_pnl_usd(
        action="HOLD", entry_price=100.0, exit_price=110.0,
        notional_usd=1000.0, fill_cost_bps=20,
    ) == 0.0
    assert hypothetical_pnl_usd(
        action="CLOSE", entry_price=100.0, exit_price=110.0,
        notional_usd=1000.0, fill_cost_bps=20,
    ) == 0.0


def test_pnl_invalid_inputs_zero():
    assert hypothetical_pnl_usd(
        action="LONG", entry_price=0.0, exit_price=110.0,
        notional_usd=1000.0, fill_cost_bps=20,
    ) == 0.0
    assert hypothetical_pnl_usd(
        action="LONG", entry_price=100.0, exit_price=110.0,
        notional_usd=0.0, fill_cost_bps=20,
    ) == 0.0


# ── Asset-typed defaults ──────────────────────────────────────────────────────


def test_fill_cost_bps_asset_typed_defaults():
    """Pin defaults — these are used by the scorer to compute
    counterfactual P&L, so changing them silently flips the
    promotion math."""
    assert FILL_COST_BPS["stock"] == 8
    assert FILL_COST_BPS["crypto"] == 20
    assert FILL_COST_BPS["options"] == 100


def test_min_dissent_samples_threshold():
    """Pin the maturity guardrail — operator UI hardcodes this in
    its 'need N more dissents' copy."""
    assert MIN_DISSENT_SAMPLES == 30


# ── Council engine v1 (rule-based consensus) ──────────────────────────────────


@pytest.mark.asyncio
async def test_council_consensus_long_when_rsi_and_momentum_agree():
    from services.research_shadow_engines import run_council_shadow
    out = await run_council_shadow(None, {
        "rsi": 25, "momentum_5b": 0.01, "volume_ratio": 1.5,
    })
    assert out["action"] == "LONG"
    assert out["confidence"] == 0.7  # volume confirmed


@pytest.mark.asyncio
async def test_council_holds_when_rsi_and_momentum_disagree():
    from services.research_shadow_engines import run_council_shadow
    out = await run_council_shadow(None, {
        "rsi": 25, "momentum_5b": -0.01, "volume_ratio": 1.5,
    })
    assert out["action"] == "HOLD"
    assert out["confidence"] == 0.0


@pytest.mark.asyncio
async def test_council_zero_llm_cost_v1():
    """v1 council is rule-based — no LLM spend on any cycle."""
    from services.research_shadow_engines import run_council_shadow
    out = await run_council_shadow(None, {"rsi": 50, "momentum_5b": 0})
    assert out["llm_cost_usd"] == 0.0


# ── Stats reducer ─────────────────────────────────────────────────────────────


def test_stats_empty_returns_empty_buckets():
    from services.research_shadow_stats import compute_shadow_stats
    out = compute_shadow_stats([])
    assert out["buckets"] == []
    assert out["min_dissent_samples_required"] == MIN_DISSENT_SAMPLES


def test_stats_buckets_by_engine_and_asset_type():
    from services.research_shadow_stats import compute_shadow_stats
    rows = [
        {"shadow_engine": "council", "asset_type": "crypto",
         "is_dissent": True, "tactical_score": {"delta_usd": 5.0}},
        {"shadow_engine": "council", "asset_type": "crypto",
         "is_dissent": True, "tactical_score": {"delta_usd": -2.0}},
        {"shadow_engine": "council", "asset_type": "stock",
         "is_dissent": True, "tactical_score": {"delta_usd": 3.0}},
        # Agreement row counted in total_decisions, NOT in dissent metrics.
        {"shadow_engine": "council", "asset_type": "crypto",
         "is_dissent": False, "tactical_score": {"delta_usd": 100.0}},
    ]
    out = compute_shadow_stats(rows)
    assert len(out["buckets"]) == 2
    crypto = next(b for b in out["buckets"] if b["asset_type"] == "crypto")
    assert crypto["total_decisions"] == 3  # 2 dissents + 1 agreement
    assert crypto["dissent_count"] == 2
    assert crypto["scored_dissent_count"] == 2
    assert crypto["win_count"] == 1
    assert crypto["win_rate"] == 0.5
    assert crypto["total_delta_usd"] == 3.0


def test_stats_skips_pending_dissents():
    """Dissents without tactical_score are still pending — they
    count toward dissent_count but not toward scored_dissent_count
    or the win rate."""
    from services.research_shadow_stats import compute_shadow_stats
    rows = [
        {"shadow_engine": "council", "asset_type": "crypto",
         "is_dissent": True, "tactical_score": {"delta_usd": 5.0}},
        {"shadow_engine": "council", "asset_type": "crypto",
         "is_dissent": True, "tactical_score": None},  # pending
    ]
    out = compute_shadow_stats(rows)
    bucket = out["buckets"][0]
    assert bucket["dissent_count"] == 2
    assert bucket["scored_dissent_count"] == 1
    assert bucket["scored_dissent_pct"] == 0.5


def test_stats_actionable_only_at_threshold():
    from services.research_shadow_stats import compute_shadow_stats
    base = {"shadow_engine": "council", "asset_type": "crypto",
            "is_dissent": True, "tactical_score": {"delta_usd": 1.0}}
    rows_under = [base.copy() for _ in range(MIN_DISSENT_SAMPLES - 1)]
    out = compute_shadow_stats(rows_under)
    assert out["buckets"][0]["actionable"] is False

    rows_at = [base.copy() for _ in range(MIN_DISSENT_SAMPLES)]
    out = compute_shadow_stats(rows_at)
    assert out["buckets"][0]["actionable"] is True


# ── Scorer pure helpers ───────────────────────────────────────────────────────


def test_tactical_score_shadow_was_right_when_delta_positive():
    from services.research_shadow_scorer import compute_tactical_score
    # Active=HOLD ($0 PnL), Shadow=LONG @ +1% on $1000 = +$8 after cost.
    out = compute_tactical_score(
        active_action="HOLD", shadow_action="LONG",
        entry_price=100.0, later_price=101.0,
        fill_cost_bps=20, lookahead_used_s=1800,
    )
    assert out["shadow_was_right"] is True
    assert out["delta_usd"] == pytest.approx(8.0, abs=0.001)


def test_tactical_score_shadow_was_wrong_when_delta_negative():
    from services.research_shadow_scorer import compute_tactical_score
    # Active=LONG won big, shadow=HOLD missed it.
    out = compute_tactical_score(
        active_action="LONG", shadow_action="HOLD",
        entry_price=100.0, later_price=105.0,
        fill_cost_bps=20, lookahead_used_s=1800,
    )
    assert out["shadow_was_right"] is False
    assert out["delta_usd"] < 0


# ── Logger Mongo glue ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insert_shadow_decision_returns_id_on_success():
    from services.research_shadow_logger import insert_shadow_decision

    seen_doc = {}

    class _Coll:
        async def insert_one(self, doc):
            seen_doc.update(doc)
            return type("_R", (), {"inserted_id": "x"})()

    class _DB:
        def __getitem__(self, _key): return _Coll()

    decision = ShadowDecision(
        bot_id="b1", user_id="u1", symbol="BTC", asset_type="crypto",
        decision_phase="entry", active_engine="confluence",
        active_action="LONG", shadow_engine="adversarial",
        shadow_action="HOLD", mid_price=77_000.0,
        sim_fill_bps_round_trip=20,
    )
    out = await insert_shadow_decision(_DB(), decision)
    assert out == decision.decision_id
    assert seen_doc["tier3_firewall"] is True
    assert seen_doc["is_dissent"] is True


@pytest.mark.asyncio
async def test_insert_swallows_exception():
    from services.research_shadow_logger import insert_shadow_decision

    class _Coll:
        async def insert_one(self, _doc):
            raise RuntimeError("mongo down")

    class _DB:
        def __getitem__(self, _key): return _Coll()

    decision = ShadowDecision(
        bot_id="b1", user_id="u1", symbol="BTC", asset_type="crypto",
        decision_phase="entry", active_engine="confluence",
        active_action="LONG", shadow_engine="adversarial",
        shadow_action="HOLD", mid_price=77_000.0,
        sim_fill_bps_round_trip=20,
    )
    out = await insert_shadow_decision(_DB(), decision)
    assert out is None  # swallowed, never raises


# ── fire_shadow defensive behaviour ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_fire_shadow_noop_on_engine_none():
    from services.research_shadow_engines import fire_shadow
    out = await fire_shadow(
        None, bot_id="b1", user_id="u1", symbol="BTC", asset_type="crypto",
        decision_phase="entry", active_engine="confluence",
        active_action="LONG", shadow_engine="none",
        signal={}, mid_price=77_000.0,
    )
    assert out is None


@pytest.mark.asyncio
async def test_fire_shadow_respects_shadow_paused():
    from services.research_shadow_engines import fire_shadow
    out = await fire_shadow(
        None, bot_id="b1", user_id="u1", symbol="BTC", asset_type="crypto",
        decision_phase="entry", active_engine="confluence",
        active_action="LONG", shadow_engine="council",
        signal={"rsi": 25, "momentum_5b": 0.01}, mid_price=77_000.0,
        shadow_paused=True,
    )
    assert out is None


@pytest.mark.asyncio
async def test_fire_shadow_swallows_engine_exception():
    """If the shadow engine itself throws, fire_shadow must NOT
    propagate — active trade path must continue."""
    from services.research_shadow_engines import fire_shadow

    async def _broken_engine(*_a, **_kw):
        raise RuntimeError("engine boom")

    with patch(
        "services.research_shadow_engines._run_engine", _broken_engine,
    ):
        out = await fire_shadow(
            None, bot_id="b1", user_id="u1", symbol="BTC",
            asset_type="crypto", decision_phase="entry",
            active_engine="confluence", active_action="LONG",
            shadow_engine="adversarial",
            signal={}, mid_price=77_000.0,
        )
    assert out is None



# ── Volume-conditional fill costs (Phase 2 P2) ────────────────────────────────


def test_volume_conditional_returns_base_when_ratio_missing():
    from services.research_shadow import volume_conditional_fill_bps
    # None / missing → behaves like v1 (no scaling).
    assert volume_conditional_fill_bps(
        base_bps=20, volume_ratio=None, asset_type="crypto",
    ) == 20


def test_volume_conditional_low_vol_penalty_crypto():
    from services.research_shadow import volume_conditional_fill_bps
    # Low-vol regime: 1.5× → 30bps.
    assert volume_conditional_fill_bps(
        base_bps=20, volume_ratio=0.3, asset_type="crypto",
    ) == 30


def test_volume_conditional_normal_vol_unchanged():
    from services.research_shadow import volume_conditional_fill_bps
    assert volume_conditional_fill_bps(
        base_bps=20, volume_ratio=1.0, asset_type="crypto",
    ) == 20


def test_volume_conditional_high_vol_compresses_crypto():
    from services.research_shadow import volume_conditional_fill_bps
    # >=3× normal → 0.5× → 10bps.
    assert volume_conditional_fill_bps(
        base_bps=20, volume_ratio=4.0, asset_type="crypto",
    ) == 10


def test_volume_conditional_equity_attenuated():
    """Equity envelope is 75% of crypto's swing — a low-vol crypto
    1.5× becomes ~1.375× on stocks (same direction, smaller mag)."""
    from services.research_shadow import volume_conditional_fill_bps
    crypto_low = volume_conditional_fill_bps(
        base_bps=20, volume_ratio=0.3, asset_type="crypto",
    )
    stock_low = volume_conditional_fill_bps(
        base_bps=20, volume_ratio=0.3, asset_type="stock",
    )
    # Crypto moves further from base than stock at same regime.
    assert crypto_low > stock_low > 20


def test_volume_conditional_options_flat():
    """Options spread is structural, not volume-driven at retail.
    Stays flat regardless of volume_ratio."""
    from services.research_shadow import volume_conditional_fill_bps
    for vr in (0.1, 1.0, 10.0):
        assert volume_conditional_fill_bps(
            base_bps=100, volume_ratio=vr, asset_type="options",
        ) == 100


def test_volume_conditional_invalid_ratio_falls_back():
    from services.research_shadow import volume_conditional_fill_bps
    assert volume_conditional_fill_bps(
        base_bps=20, volume_ratio="not_a_number", asset_type="crypto",
    ) == 20
    assert volume_conditional_fill_bps(
        base_bps=20, volume_ratio=-1.0, asset_type="crypto",
    ) == 20


def test_volume_conditional_minimum_floor():
    """Multiplier never produces sub-1bps — math floor protects
    against degenerate input."""
    from services.research_shadow import volume_conditional_fill_bps
    out = volume_conditional_fill_bps(
        base_bps=1, volume_ratio=10.0, asset_type="crypto",
    )
    assert out >= 1


# ── Phase breakdown (enhancement) ─────────────────────────────────────────────


def test_stats_phase_breakdown_tracks_per_phase_dissents():
    from services.research_shadow_stats import compute_shadow_stats
    rows = [
        # Entry phase: 2 dissents, 1 scored win, 1 scored loss.
        {"shadow_engine": "council", "asset_type": "crypto",
         "decision_phase": "entry", "is_dissent": True,
         "tactical_score": {"delta_usd": 5.0}},
        {"shadow_engine": "council", "asset_type": "crypto",
         "decision_phase": "entry", "is_dissent": True,
         "tactical_score": {"delta_usd": -3.0}},
        # Exit phase: 1 dissent, scored win.
        {"shadow_engine": "council", "asset_type": "crypto",
         "decision_phase": "exit", "is_dissent": True,
         "tactical_score": {"delta_usd": 12.0}},
    ]
    out = compute_shadow_stats(rows)
    bucket = out["buckets"][0]
    pb = bucket["phase_breakdown"]
    assert pb["entry"]["dissents"] == 2
    assert pb["entry"]["scored"] == 2
    assert pb["entry"]["wins"] == 1
    assert pb["entry"]["win_rate"] == 0.5
    assert pb["exit"]["dissents"] == 1
    assert pb["exit"]["wins"] == 1
    assert pb["exit"]["win_rate"] == 1.0
    # Cycle untouched in this fixture.
    assert pb["cycle"]["dissents"] == 0
    assert pb["cycle"]["win_rate"] is None


def test_stats_phase_unknown_falls_to_cycle():
    """Defensive — unknown decision_phase strings bucket under 'cycle'."""
    from services.research_shadow_stats import compute_shadow_stats
    rows = [
        {"shadow_engine": "council", "asset_type": "crypto",
         "decision_phase": "unknown_phase_xyz", "is_dissent": True,
         "tactical_score": {"delta_usd": 1.0}},
    ]
    out = compute_shadow_stats(rows)
    pb = out["buckets"][0]["phase_breakdown"]
    assert pb["cycle"]["dissents"] == 1


# ── Volume-ratio capture flows through the system ─────────────────────────────


@pytest.mark.asyncio
async def test_volume_ratio_persisted_on_shadow_decision():
    """Smoke test: signal carrying volume_ratio results in a
    persisted ShadowDecision with the field populated."""
    from services.research_shadow_engines import fire_shadow

    seen_doc = {}

    class _Coll:
        async def insert_one(self, doc):
            seen_doc.update(doc)
            return type("_R", (), {})()

    class _DB:
        def __getitem__(self, _key): return _Coll()
        async def __aiter__(self): return self
        def aggregate(self, _p):
            class _C:
                async def to_list(self, _l): return []
            return _C()

        def find_one(self, *_a, **_kw):
            class _Q:
                def __await__(self): return iter([None])
            return _Q()

    # Patch the gate-side queries to skip rate-limit / cost checks.
    with patch(
        "services.research_shadow_engines.get_last_shadow_ts",
        return_value=None,
    ), patch(
        "services.research_shadow_engines.get_daily_cost_usd",
        return_value=0.0,
    ), patch(
        "services.research_shadow_engines.count_recent_agreement_run",
        return_value=0,
    ):
        await fire_shadow(
            _DB(), bot_id="b1", user_id="u1", symbol="BTC",
            asset_type="crypto", decision_phase="entry",
            active_engine="confluence", active_action="LONG",
            shadow_engine="adversarial",
            signal={"volume_ratio": 2.5, "rsi": 50, "regime": "trending"},
            mid_price=77_000.0,
        )

    assert seen_doc.get("volume_ratio_at_decision") == 2.5
    # Regime tag also captured for future regime-conditional weighting.
    assert seen_doc.get("regime_at_decision") == "trending"


# ── Disagreement-triggered cycle frequency (P2) ───────────────────────────────


def test_cycle_skip_adversarial_never_skipped():
    """Adversarial is deterministic + free — never skip even on
    a long agreement run."""
    from services.research_shadow import should_skip_cycle_for_agreement_run
    assert should_skip_cycle_for_agreement_run(
        shadow_engine="adversarial",
        decision_phase="cycle",
        recent_agreement_run=999,
    ) is False


def test_cycle_skip_entry_exit_never_skipped():
    """Entry + exit are highest-signal moments — never skip."""
    from services.research_shadow import should_skip_cycle_for_agreement_run
    for phase in ("entry", "exit"):
        assert should_skip_cycle_for_agreement_run(
            shadow_engine="council",
            decision_phase=phase,
            recent_agreement_run=999,
        ) is False


def test_cycle_skip_below_threshold_fires():
    from services.research_shadow import should_skip_cycle_for_agreement_run
    assert should_skip_cycle_for_agreement_run(
        shadow_engine="council",
        decision_phase="cycle",
        recent_agreement_run=3,
    ) is False


def test_cycle_skip_at_threshold_skips():
    from services.research_shadow import (
        CYCLE_SKIP_AGREEMENT_RUN, should_skip_cycle_for_agreement_run,
    )
    assert should_skip_cycle_for_agreement_run(
        shadow_engine="council",
        decision_phase="cycle",
        recent_agreement_run=CYCLE_SKIP_AGREEMENT_RUN,
    ) is True


# ── count_recent_agreement_run (Mongo glue) ───────────────────────────────────


@pytest.mark.asyncio
async def test_count_recent_agreement_run_walks_until_first_dissent():
    from services.research_shadow_logger import count_recent_agreement_run

    rows = [
        # Newest first. Three cycle agreements, then a dissent, then more.
        {"is_dissent": False, "decision_phase": "cycle"},
        {"is_dissent": False, "decision_phase": "cycle"},
        {"is_dissent": False, "decision_phase": "cycle"},
        {"is_dissent": True,  "decision_phase": "cycle"},
        {"is_dissent": False, "decision_phase": "cycle"},
        {"is_dissent": False, "decision_phase": "cycle"},
    ]

    class _Cursor:
        def sort(self, *_a, **_kw): return self
        def limit(self, _n): return self
        async def to_list(self, length=None): return list(rows)

    class _Coll:
        def find(self, *_a, **_kw): return _Cursor()

    class _DB:
        def __getitem__(self, _key): return _Coll()

    out = await count_recent_agreement_run(_DB(), "bot1")
    assert out == 3  # walks back 3 agreements before hitting the dissent


@pytest.mark.asyncio
async def test_count_recent_agreement_run_ignores_non_cycle_phases():
    """Entry/exit dissents shouldn't reset the cycle agreement run."""
    from services.research_shadow_logger import count_recent_agreement_run

    rows = [
        {"is_dissent": False, "decision_phase": "cycle"},
        {"is_dissent": True,  "decision_phase": "entry"},  # non-cycle, ignored
        {"is_dissent": False, "decision_phase": "cycle"},
        {"is_dissent": False, "decision_phase": "cycle"},
    ]

    class _Cursor:
        def sort(self, *_a, **_kw): return self
        def limit(self, _n): return self
        async def to_list(self, length=None): return list(rows)

    class _Coll:
        def find(self, *_a, **_kw): return _Cursor()

    class _DB:
        def __getitem__(self, _key): return _Coll()

    out = await count_recent_agreement_run(_DB(), "bot1")
    # All 3 cycle rows agreed; entry-dissent skipped.
    assert out == 3


# ── Regime-conditional stats reducer (P2 scaffolding) ─────────────────────────


def test_regime_stats_skips_untagged_dissents():
    """Dissents without regime_at_decision shouldn't pollute the
    breakdown — they'd all bucket together and confuse the math."""
    from services.research_shadow_stats import compute_regime_stats
    rows = [
        {"is_dissent": True, "shadow_engine": "council", "asset_type": "crypto",
         "regime_at_decision": "trending",
         "tactical_score": {"delta_usd": 5.0}},
        {"is_dissent": True, "shadow_engine": "council", "asset_type": "crypto",
         "regime_at_decision": None,  # legacy row, skipped
         "tactical_score": {"delta_usd": -100.0}},
    ]
    out = compute_regime_stats(rows)
    assert len(out["buckets"]) == 1
    assert out["buckets"][0]["regime"] == "trending"


def test_regime_stats_buckets_by_regime_x_engine_x_asset_type():
    from services.research_shadow_stats import compute_regime_stats
    rows = [
        # (regime, engine, asset_type) — expect 3 distinct buckets.
        {"is_dissent": True, "shadow_engine": "council",     "asset_type": "crypto",
         "regime_at_decision": "trending",
         "tactical_score": {"delta_usd": 5.0}},
        {"is_dissent": True, "shadow_engine": "council",     "asset_type": "crypto",
         "regime_at_decision": "parabolic",
         "tactical_score": {"delta_usd": -3.0}},
        {"is_dissent": True, "shadow_engine": "adversarial", "asset_type": "crypto",
         "regime_at_decision": "trending",
         "tactical_score": {"delta_usd": 7.0}},
    ]
    out = compute_regime_stats(rows)
    assert len(out["buckets"]) == 3


def test_regime_stats_actionable_at_threshold():
    from services.research_shadow import MIN_DISSENT_SAMPLES
    from services.research_shadow_stats import compute_regime_stats
    base = {"is_dissent": True, "shadow_engine": "council",
            "asset_type": "crypto", "regime_at_decision": "trending",
            "tactical_score": {"delta_usd": 1.0}}
    out = compute_regime_stats([dict(base) for _ in range(MIN_DISSENT_SAMPLES - 1)])
    assert out["buckets"][0]["actionable"] is False

    out = compute_regime_stats([dict(base) for _ in range(MIN_DISSENT_SAMPLES)])
    assert out["buckets"][0]["actionable"] is True


def test_regime_stats_skips_agreement_rows():
    """Only dissents count toward regime buckets — agreement rows
    are noise (they don't carry the disagreement-conditional signal)."""
    from services.research_shadow_stats import compute_regime_stats
    rows = [
        {"is_dissent": False, "shadow_engine": "council", "asset_type": "crypto",
         "regime_at_decision": "trending"},
        {"is_dissent": True, "shadow_engine": "council", "asset_type": "crypto",
         "regime_at_decision": "trending",
         "tactical_score": {"delta_usd": 1.0}},
    ]
    out = compute_regime_stats(rows)
    assert len(out["buckets"]) == 1
    assert out["buckets"][0]["dissent_count"] == 1


@pytest.mark.asyncio
async def test_regime_stats_empty_db_safe():
    from services.research_shadow_stats import fetch_regime_stats
    out = await fetch_regime_stats(None)
    assert out["buckets"] == []


# ── Tier-readiness aggregator ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tier_readiness_empty_db_returns_default_closed():
    """No DB → all gates closed, no buckets, ready=False."""
    from services.research_shadow_stats import fetch_tier_readiness
    out = await fetch_tier_readiness(None)
    assert out["ready_to_enable_council"] is False
    assert out["council_buckets"] == []
    assert out["tier3_progress_pct"] == 0.0
    assert out["tier3_unlocked"] is False
    # Adversarial phase comes from env (defaults to "shadow"); no DB
    # dependency.
    assert out["adversarial_phase"] in ("shadow", "risk_only", "veto", "full")


@pytest.mark.asyncio
async def test_tier_readiness_reports_all_three_blockers(monkeypatch):
    """When tier3 closed, phase != full, and no bucket open, the
    next_steps list should call out all three blockers separately."""
    import services.research_shadow_stats as rss
    from services.research_shadow_stats import fetch_tier_readiness

    async def _fake_t3_stats(*_a, **_kw):
        return {"days": 5, "total_trades": 20}  # well under thresholds

    async def _fake_council_stats(*_a, **_kw):
        return {"buckets": []}

    monkeypatch.setattr(
        "services.tier3_readiness.build_tier3_stats", _fake_t3_stats,
    )
    monkeypatch.setattr(
        "services.council_tier_gate.get_cached_council_stats",
        _fake_council_stats,
    )
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "shadow")
    # 2026-06-01: live ``.env`` now sets ``TIER3_BYPASS_UNLOCKED=1`` which
    # would short-circuit the gate to unlocked and silence the tier3
    # blocker in next_steps. This test validates the ORIGINAL 3-blocker
    # reporting path, so we clear the bypass for this assertion.
    monkeypatch.delenv("TIER3_BYPASS_UNLOCKED", raising=False)

    out = await fetch_tier_readiness(object())  # any non-None db
    assert out["ready_to_enable_council"] is False
    assert len(out["next_steps"]) >= 3  # tier3 + phase + no buckets
    # At least one next-step should mention each blocker.
    joined = " ".join(out["next_steps"]).lower()
    assert "tier 3" in joined
    assert "phase" in joined
    assert "council bucket" in joined


@pytest.mark.asyncio
async def test_tier_readiness_open_bucket_counts(monkeypatch):
    """Bucket meeting all 3 thresholds should report open=True with
    needed_dissents=0 and 'actionable' reason."""
    from services.research_shadow_stats import fetch_tier_readiness

    async def _fake_t3_stats(*_a, **_kw):
        # Synthetic Tier-3-unlocked stats.
        return {
            "days": 35, "total_trades": 150, "high_conf_trades": 60,
            "high_conf_win_rate": 0.80, "avg_confidence": 70.0,
            "strong_miss_rate": 0.05, "last_7d_win_rate": 0.65,
            "overall_win_rate": 0.62, "clamp_total": 0,
        }

    async def _fake_council_stats(*_a, **_kw):
        return {"buckets": [{
            "shadow_engine": "council",
            "asset_type": "crypto",
            "dissent_count": 50,
            "win_rate": 0.65,
            "total_delta_usd": 25.5,
            "scored_dissent_count": 50,
            "actionable": True,
        }]}

    monkeypatch.setattr(
        "services.tier3_readiness.build_tier3_stats", _fake_t3_stats,
    )
    monkeypatch.setattr(
        "services.council_tier_gate.get_cached_council_stats",
        _fake_council_stats,
    )
    monkeypatch.setenv("CRYPTO_ADVERSARIAL_PHASE", "full")

    out = await fetch_tier_readiness(object())
    assert out["tier3_unlocked"] is True
    assert out["adversarial_phase"] == "full"
    assert len(out["council_buckets"]) == 1
    bucket = out["council_buckets"][0]
    assert bucket["open"] is True
    assert bucket["needed_dissents"] == 0
    assert "actionable" in bucket["reason"]
    assert out["ready_to_enable_council"] is True


@pytest.mark.asyncio
async def test_tier_readiness_reason_strings_helpful_for_each_blocker(monkeypatch):
    """Each unmet threshold should produce a distinct, actionable
    'reason' string — no generic 'closed' fallback."""
    from services.research_shadow_stats import fetch_tier_readiness

    async def _fake_t3_stats(*_a, **_kw):
        return {"days": 5}

    async def _fake_council_stats(*_a, **_kw):
        return {"buckets": [
            # Bucket A: just dissent count low.
            {"shadow_engine": "council", "asset_type": "crypto",
             "dissent_count": 18, "win_rate": 0.7, "total_delta_usd": 50},
            # Bucket B: dissents enough, win rate at floor.
            {"shadow_engine": "council", "asset_type": "stock",
             "dissent_count": 35, "win_rate": 0.50, "total_delta_usd": 50},
        ]}

    monkeypatch.setattr(
        "services.tier3_readiness.build_tier3_stats", _fake_t3_stats,
    )
    monkeypatch.setattr(
        "services.council_tier_gate.get_cached_council_stats",
        _fake_council_stats,
    )

    out = await fetch_tier_readiness(object())
    crypto = next(b for b in out["council_buckets"] if b["asset_type"] == "crypto")
    stock = next(b for b in out["council_buckets"] if b["asset_type"] == "stock")
    assert crypto["needed_dissents"] == 12  # 30-18
    assert "12 more dissents" in crypto["reason"]
    assert stock["needed_dissents"] == 0
    assert "win rate" in stock["reason"]


@pytest.mark.asyncio
async def test_tier_readiness_ignores_non_council_buckets(monkeypatch):
    """Adversarial-shadow buckets (engine=adversarial) shouldn't
    appear in council_buckets — they're not relevant to the
    Council flip decision."""
    from services.research_shadow_stats import fetch_tier_readiness

    async def _fake_t3_stats(*_a, **_kw):
        return {}

    async def _fake_council_stats(*_a, **_kw):
        return {"buckets": [
            {"shadow_engine": "adversarial", "asset_type": "crypto",
             "dissent_count": 100, "win_rate": 0.9, "total_delta_usd": 500},
            {"shadow_engine": "council", "asset_type": "crypto",
             "dissent_count": 5, "win_rate": 0.0, "total_delta_usd": 0},
        ]}

    monkeypatch.setattr(
        "services.tier3_readiness.build_tier3_stats", _fake_t3_stats,
    )
    monkeypatch.setattr(
        "services.council_tier_gate.get_cached_council_stats",
        _fake_council_stats,
    )

    out = await fetch_tier_readiness(object())
    engines = [b["engine"] for b in out["council_buckets"]]
    assert engines == ["council"]  # adversarial filtered out


# ── Council LLM consensus (v2) ────────────────────────────────────────────────


def test_council_llm_consensus_unanimous_long():
    from services.research_shadow_engines import _council_llm_consensus
    out = _council_llm_consensus([
        {"provider": "openai", "action": "LONG", "confidence": 0.7},
        {"provider": "anthropic", "action": "LONG", "confidence": 0.6},
        {"provider": "gemini", "action": "LONG", "confidence": 0.5},
    ])
    assert out["action"] == "LONG"
    assert out["confidence"] > 0.5


def test_council_llm_consensus_majority_short_with_dissenter():
    from services.research_shadow_engines import _council_llm_consensus
    out = _council_llm_consensus([
        {"provider": "openai", "action": "SHORT", "confidence": 0.7},
        {"provider": "anthropic", "action": "SHORT", "confidence": 0.7},
        {"provider": "gemini", "action": "LONG", "confidence": 0.5},
    ])
    assert out["action"] == "SHORT"


def test_council_llm_consensus_tie_favours_hold():
    """Tie-break rule: HOLD wins over LONG/SHORT to avoid coin-flip dissents."""
    from services.research_shadow_engines import _council_llm_consensus
    out = _council_llm_consensus([
        {"provider": "openai", "action": "LONG", "confidence": 0.5},
        {"provider": "anthropic", "action": "SHORT", "confidence": 0.5},
        {"provider": "gemini", "action": "HOLD", "confidence": 0.5},
    ])
    assert out["action"] == "HOLD"


def test_council_llm_consensus_empty_votes_holds():
    from services.research_shadow_engines import _council_llm_consensus
    out = _council_llm_consensus([])
    assert out["action"] == "HOLD"
    assert out["confidence"] == 0.0


@pytest.mark.asyncio
async def test_council_llm_no_api_key_returns_hold():
    """Missing EMERGENT_LLM_KEY → HOLD with zero cost. No raise."""
    from services.research_shadow_engines import _run_council_llm
    import os
    saved = os.environ.pop("EMERGENT_LLM_KEY", None)
    try:
        out = await _run_council_llm({"rsi": 50})
    finally:
        if saved is not None:
            os.environ["EMERGENT_LLM_KEY"] = saved
    assert out["action"] == "HOLD"
    assert out["llm_cost_usd"] == 0.0
    assert "no_api_key" in out["thesis"]


@pytest.mark.asyncio
async def test_council_router_dispatches_on_env_flag():
    """COUNCIL_SHADOW_MODE=rule routes to v1, =llm routes to v2."""
    from services.research_shadow_engines import run_council_shadow
    import os

    saved = os.environ.get("COUNCIL_SHADOW_MODE")
    try:
        # Rule mode → produces deterministic output, zero cost.
        os.environ["COUNCIL_SHADOW_MODE"] = "rule"
        out_rule = await run_council_shadow(None, {"rsi": 25, "momentum_5b": 0.01})
        assert out_rule["llm_cost_usd"] == 0.0

        # LLM mode without EMERGENT_LLM_KEY → falls through to no-key HOLD.
        os.environ["COUNCIL_SHADOW_MODE"] = "llm"
        os.environ.pop("EMERGENT_LLM_KEY", None)
        out_llm = await run_council_shadow(None, {"rsi": 25, "momentum_5b": 0.01})
        assert out_llm["thesis"] == "council_llm_no_api_key"
    finally:
        if saved is not None:
            os.environ["COUNCIL_SHADOW_MODE"] = saved
        else:
            os.environ.pop("COUNCIL_SHADOW_MODE", None)


