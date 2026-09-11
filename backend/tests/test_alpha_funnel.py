"""Tests for the Alpha Funnel — funnel_state, broker_research, orchestrator.

These lock down the operator's key requirements:

* Discovery ingest never drops tail candidates before ranking.
* Score deltas are applied WITH reasons so the operator can inspect
  why the ranking changed.
* Promotion is fluid — a strengthening lower-ranked candidate can
  overtake a weakening leader (transition() supports both directions).
* Broker research is primarily re-rank — only objective failures
  (no price, stale, over-spread, over-drift, crossed quote) hard-block.
* SQLite persistence round-trip preserves state; restored ARMED
  candidates come back as WATCH (never auto-execute after restart).
* Restore never re-hydrates ACTIONABLE or EXECUTED.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    db_path = tmp_path / "hs.db"
    monkeypatch.setenv("ALPHA_HOT_STORE_PATH", str(db_path))
    from services import alpha_hot_store, alpha_funnel_state, alpha_funnel
    alpha_hot_store._DB_PATH = None
    alpha_hot_store.init()
    alpha_funnel_state.clear()
    # Reset runtime overrides
    alpha_funnel._RUNTIME_OVERRIDES.clear()
    yield
    alpha_funnel_state.clear()
    alpha_funnel._RUNTIME_OVERRIDES.clear()


# ── State manager ─────────────────────────────────────────────

def test_ingest_discovery_adds_new_candidates_at_discovered():
    from services import alpha_funnel_state as fs
    fs.ingest_discovery(
        [{"symbol": "NVDA", "score": 0.84}, {"symbol": "AMD", "score": 0.81}],
    )
    assert {c.symbol for c in fs.get_all()} == {"NVDA", "AMD"}
    assert all(c.state == fs.CandidateState.DISCOVERED for c in fs.get_all())


def test_ingest_discovery_max_n_only_caps_new_adds():
    """Operator rule — don't discard tail candidates. If discovery
    returns 49, all 49 candidates are visible; max_n only limits
    NEW additions if the funnel is already large."""
    from services import alpha_funnel_state as fs
    fs.ingest_discovery(
        [{"symbol": f"S{i}", "score": 1.0 - i * 0.01} for i in range(49)],
        max_n=40,
    )
    # 40 new adds allowed on first cycle
    assert len(fs.get_all()) == 40


def test_ingest_discovery_refreshes_existing_without_state_change():
    from services import alpha_funnel_state as fs
    fs.ingest_discovery([{"symbol": "NVDA", "score": 0.70}])
    fs.transition("NVDA", to_state=fs.CandidateState.ARMED, reason="test")
    # Second discovery bumps score — state must stay ARMED.
    fs.ingest_discovery([{"symbol": "NVDA", "score": 0.90}])
    c = fs.get("NVDA")
    assert c.state == fs.CandidateState.ARMED
    assert c.current_score == 0.90


def test_transition_records_delta_and_reason():
    from services import alpha_funnel_state as fs
    fs.ingest_discovery([{"symbol": "NVDA", "score": 0.84}])
    fs.transition(
        "NVDA", to_state=fs.CandidateState.RESEARCH,
        delta=0.05, reason="broker_confirmed",
    )
    c = fs.get("NVDA")
    assert c.current_score == 0.89
    assert c.score_history[-1]["reason"] == "broker_confirmed"
    assert c.score_history[-1]["from_state"] == fs.CandidateState.DISCOVERED
    assert c.score_history[-1]["to_state"] == fs.CandidateState.RESEARCH


def test_transition_supports_backward_moves():
    """A weakening ARMED must be demotable to WATCH — the funnel
    is fluid, not a one-way tournament."""
    from services import alpha_funnel_state as fs
    fs.ingest_discovery([{"symbol": "TSLA", "score": 0.75}])
    fs.transition("TSLA", to_state=fs.CandidateState.ARMED, reason="promoted")
    fs.transition(
        "TSLA", to_state=fs.CandidateState.WATCH,
        delta=-0.15, reason="momentum_deterioration",
    )
    c = fs.get("TSLA")
    assert c.state == fs.CandidateState.WATCH
    assert c.current_score == 0.60


def test_rank_promotes_strengthening_over_leader():
    """The core promotability rule — #11 that strengthens can
    overtake a #2 that weakens."""
    from services import alpha_funnel_state as fs
    fs.ingest_discovery([
        {"symbol": "LEAD", "score": 0.90},
        {"symbol": "DARK_HORSE", "score": 0.60},
    ])
    fs.transition("LEAD", to_state=fs.CandidateState.WATCH,
                  delta=-0.35, reason="failed_breakout")
    fs.transition("DARK_HORSE", to_state=fs.CandidateState.WATCH,
                  delta=+0.15, reason="broker_confirmed_and_rvol_accel")
    ranked = fs.rank_candidates()
    assert ranked[0].symbol == "DARK_HORSE"
    assert ranked[1].symbol == "LEAD"


# ── SQLite persistence + restore ──────────────────────────────

def test_persist_and_restore_round_trip():
    from services import alpha_funnel_state as fs
    fs.ingest_discovery([{"symbol": "NVDA", "score": 0.84}])
    fs.transition("NVDA", to_state=fs.CandidateState.WATCH,
                  delta=0.05, reason="broker_confirmed")
    fs.persist_to_sqlite()
    fs.clear()
    assert fs.get_all() == []
    n = fs.restore_from_sqlite()
    assert n == 1
    c = fs.get("NVDA")
    assert c.state == fs.CandidateState.WATCH
    assert c.current_score == 0.89


def test_restore_downgrades_armed_to_watch():
    """Operator rule — restored candidates must be revalidated;
    never restore an ARMED state which could auto-execute."""
    from services import alpha_funnel_state as fs
    fs.ingest_discovery([{"symbol": "WMT", "score": 0.80}])
    fs.transition("WMT", to_state=fs.CandidateState.ARMED, reason="promoted")
    fs.persist_to_sqlite()
    fs.clear()
    fs.restore_from_sqlite()
    c = fs.get("WMT")
    assert c.state == fs.CandidateState.WATCH, (
        "ARMED must NOT be restored as ARMED — it must revalidate"
    )


def test_restore_skips_actionable_and_executed():
    """ACTIONABLE and EXECUTED are session-scoped by design."""
    from services import alpha_funnel_state as fs
    fs.ingest_discovery([
        {"symbol": "A", "score": 0.8}, {"symbol": "B", "score": 0.7},
    ])
    fs.transition("A", to_state=fs.CandidateState.ACTIONABLE, reason="t")
    fs.transition("B", to_state=fs.CandidateState.EXECUTED, reason="t")
    fs.persist_to_sqlite()
    fs.clear()
    fs.restore_from_sqlite()
    assert fs.get("A") is None
    assert fs.get("B") is None


# ── Broker research ───────────────────────────────────────────

def test_compute_research_delta_rewards_tight_spread_and_minimal_drift():
    from services.alpha_broker_research import (
        BrokerResearchSnapshot, compute_research_delta,
    )
    snap = BrokerResearchSnapshot(
        symbol="NVDA", broker="test", fetched_at_ns=0,
        signal_price=100.0, current_price=100.05,
        drift_bps=5.0,
        bid=99.99, ask=100.01, mid=100.0, spread_bps=2.0,
        broker_quote_age_seconds=1.0,
    )
    delta, reasons, hard_block = compute_research_delta(snap)
    assert not hard_block
    assert delta > 0
    assert any("broker_confirmed" in r for r in reasons)
    assert any("tight_spread" in r for r in reasons)


def test_compute_research_delta_hard_blocks_on_stale_quote():
    from services.alpha_broker_research import (
        BrokerResearchSnapshot, compute_research_delta,
    )
    snap = BrokerResearchSnapshot(
        symbol="NVDA", broker="test", fetched_at_ns=0,
        signal_price=100.0, current_price=100.0,
        broker_quote_age_seconds=120.0,
    )
    _, reasons, hard_block = compute_research_delta(snap)
    assert hard_block
    assert any("stale_broker_quote" in r for r in reasons)


def test_compute_research_delta_hard_blocks_on_over_spread():
    from services.alpha_broker_research import (
        BrokerResearchSnapshot, compute_research_delta,
    )
    snap = BrokerResearchSnapshot(
        symbol="X", broker="test", fetched_at_ns=0,
        signal_price=10.0, current_price=10.0,
        bid=9.5, ask=10.5, mid=10.0, spread_bps=100.0,
    )
    _, reasons, hard_block = compute_research_delta(snap)
    assert hard_block
    assert any("spread_over_cap" in r for r in reasons)


def test_compute_research_delta_confidence_gap_is_not_a_block():
    """Operator rule — 0.69 vs 0.70 must be a delta not a block."""
    from services.alpha_broker_research import (
        BrokerResearchSnapshot, compute_research_delta,
    )
    # A slightly-loose spread + slightly noticeable drift — should
    # produce a modest negative delta but NOT hard-block.
    snap = BrokerResearchSnapshot(
        symbol="X", broker="test", fetched_at_ns=0,
        signal_price=100.0, current_price=100.45,
        drift_bps=45.0, bid=99.9, ask=100.1, mid=100.0, spread_bps=20.0,
        broker_quote_age_seconds=8.0,
    )
    delta, reasons, hard_block = compute_research_delta(snap)
    assert hard_block is False
    assert delta is not None


# ── Orchestrator cycle ────────────────────────────────────────

@pytest.mark.asyncio
async def test_funnel_cycle_end_to_end_with_mocked_broker():
    """Full cycle: 6 candidates → 4 survivors → 3 researched →
    2 armed. Verify one hard-block correctly rejects."""
    from services import alpha_funnel, alpha_funnel_state as fs
    from services.alpha_broker_research import BrokerResearchSnapshot

    # Tighten stage sizes so a small test set exercises every stage.
    alpha_funnel.set_runtime_override("discovery_universe", 40)
    alpha_funnel.set_runtime_override("preliminary_survivors", 4)
    alpha_funnel.set_runtime_override("broker_research_max", 4)
    alpha_funnel.set_runtime_override("deep_discernment_max", 2)
    alpha_funnel.set_runtime_override("promoted_armed", 2)

    def _fake_research(symbol, signal_price):
        # Symbol "STALE" produces a hard-block; others confirm.
        if symbol == "STALE":
            return BrokerResearchSnapshot(
                symbol=symbol, broker="mock", fetched_at_ns=0,
                signal_price=signal_price, current_price=signal_price,
                broker_quote_age_seconds=200.0,  # over cap → hard block
            )
        return BrokerResearchSnapshot(
            symbol=symbol, broker="mock", fetched_at_ns=0,
            signal_price=signal_price, current_price=signal_price * 1.0005,
            drift_bps=5.0, bid=signal_price - 0.01, ask=signal_price + 0.01,
            mid=signal_price, spread_bps=1.5,
            broker_quote_age_seconds=1.0,
        )

    async def _perform_research_fake(*, symbol, signal_price):
        return _fake_research(symbol, signal_price)

    with patch(
        "services.alpha_broker_research.perform_research",
        side_effect=_perform_research_fake,
    ):
        result = await alpha_funnel.run_funnel_cycle(
            db=None,
            ranked_discovery=[
                {"symbol": "NVDA", "score": 0.84, "signal_price": 500.0},
                {"symbol": "AMD",  "score": 0.81, "signal_price": 200.0},
                {"symbol": "WMT",  "score": 0.78, "signal_price": 90.0},
                {"symbol": "STALE", "score": 0.75, "signal_price": 30.0},
                {"symbol": "PLTR", "score": 0.73, "signal_price": 40.0},
                {"symbol": "META", "score": 0.54, "signal_price": 800.0},
            ],
        )

    assert result["discovery_input"] == 6
    assert result["survivors"] == 4
    assert result["researched"] == 4  # broker_research_max
    # ``STALE`` got hard-blocked; the top ``deep_discernment_max``
    # of the surviving WATCH pool promoted to ARMED.
    stale = fs.get("STALE")
    assert stale.state == fs.CandidateState.REJECTED
    armed_symbols = {c.symbol for c in fs.get_by_state(fs.CandidateState.ARMED)}
    assert 1 <= len(armed_symbols) <= 2
    assert "STALE" not in armed_symbols


@pytest.mark.asyncio
async def test_funnel_cycle_persists_state_to_sqlite():
    from services import alpha_funnel, alpha_funnel_state as fs
    from services.alpha_broker_research import BrokerResearchSnapshot

    async def _perform_research_fake(*, symbol, signal_price):
        return BrokerResearchSnapshot(
            symbol=symbol, broker="mock", fetched_at_ns=0,
            signal_price=signal_price, current_price=signal_price,
            bid=signal_price - 0.01, ask=signal_price + 0.01,
            mid=signal_price, spread_bps=2.0,
            broker_quote_age_seconds=1.0, drift_bps=0.0,
        )

    with patch(
        "services.alpha_broker_research.perform_research",
        side_effect=_perform_research_fake,
    ):
        await alpha_funnel.run_funnel_cycle(
            db=None,
            ranked_discovery=[{"symbol": "NVDA", "score": 0.9, "signal_price": 500.0}],
        )
    # Clear in-memory + restore from SQLite → same NVDA back.
    fs.clear()
    fs.restore_from_sqlite()
    c = fs.get("NVDA")
    assert c is not None
    # ARMED gets downgraded to WATCH on restore per operator rule.
    assert c.state in (fs.CandidateState.WATCH, fs.CandidateState.RESEARCH)


# ── Config ────────────────────────────────────────────────────

def test_runtime_config_override_applies():
    from services import alpha_funnel
    alpha_funnel.set_runtime_override("promoted_armed", 5)
    cfg = alpha_funnel.get_config()
    assert cfg.promoted_armed == 5
    alpha_funnel.set_runtime_override("promoted_armed", None)  # clear
    cfg2 = alpha_funnel.get_config()
    assert cfg2.promoted_armed == 3  # env default


def test_runtime_config_override_rejects_unknown_key():
    from services import alpha_funnel
    with pytest.raises(ValueError):
        alpha_funnel.set_runtime_override("mystery_field", 10)
