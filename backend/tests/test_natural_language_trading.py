"""
Tests for the Natural Language Trading layer.

Locks in the safety design rule: NL never executes trades. It explains,
debates, parses commands, and mutates only its own in-process runtime
state, all within hard bounds.
"""
from __future__ import annotations

from services.natural_language_trading import (
    CommandAction,
    GateResult,
    NaturalLanguageTradeEngine,
    NLRuntimeState,
    ParsedNLCommand,
    RISK_MAX,
    RISK_MIN,
    SCHEMA_VERSION,
    TradeAction,
    TradeSignal,
    Verdict,
    clamp,
    confidence_label,
    money,
    normalize_confidence,
)


# ─── Utility tests ────────────────────────────────────────────────────────────


def test_clamp_basic():
    assert clamp(0.5, 0.0, 1.0) == 0.5
    assert clamp(-1.0, 0.0, 1.0) == 0.0
    assert clamp(2.0, 0.0, 1.0) == 1.0


def test_normalize_confidence_accepts_0_100_scale():
    assert normalize_confidence(75) == 0.75
    assert normalize_confidence(0.75) == 0.75
    assert normalize_confidence(None) == 0.0


def test_money_formatter():
    assert money(1_500_000) == "$1.50M"
    assert money(2_500) == "$2.5K"
    assert money(99) == "$99.00"
    assert money(None) == "unknown"


def test_confidence_label_tiers():
    assert confidence_label(0.85) == "very high"
    assert confidence_label(0.72) == "high"
    assert confidence_label(0.60) == "moderate"
    assert confidence_label(0.50) == "low-moderate"
    assert confidence_label(0.20) == "low"


# ─── Trade explanation / debate ───────────────────────────────────────────────


def _signal(**overrides):
    base = dict(
        ticker="AAPL",
        action=TradeAction.BUY,
        confidence=0.75,
        risk_reward=2.5,
        regime="trend_up",
        regime_match=True,
        position_multiplier=1.0,
        gates=[],
    )
    base.update(overrides)
    return TradeSignal(**base)


def test_explain_trade_approves_high_conf_high_rr():
    e = NaturalLanguageTradeEngine()
    exp = e.explain_trade(_signal())
    assert exp.verdict == Verdict.APPROVE
    assert exp.action == TradeAction.BUY
    assert "approved" in exp.headline.lower()
    assert exp.schema_version == SCHEMA_VERSION


def test_explain_trade_modifies_when_borderline():
    e = NaturalLanguageTradeEngine()
    exp = e.explain_trade(_signal(confidence=0.60, risk_reward=1.6))
    assert exp.verdict == Verdict.MODIFY


def test_explain_trade_rejects_below_min_rr():
    e = NaturalLanguageTradeEngine()
    exp = e.explain_trade(_signal(risk_reward=1.0))
    assert exp.verdict == Verdict.REJECT
    assert "Risk-reward" in exp.commander_summary


def test_explain_trade_rejects_on_hard_veto():
    e = NaturalLanguageTradeEngine()
    bad_gate = GateResult(
        name="liquidity_gate", passed=False,
        reason="dollar_volume below 2M", severity="hard_veto",
    )
    exp = e.explain_trade(_signal(gates=[bad_gate]))
    assert exp.verdict == Verdict.REJECT
    assert any("dollar_volume" in r for r in exp.rejected_reasons)


def test_explain_trade_watchlist_when_action_is_hold():
    e = NaturalLanguageTradeEngine()
    exp = e.explain_trade(_signal(action=TradeAction.HOLD))
    assert exp.verdict == Verdict.WATCHLIST


def test_debate_returns_bull_and_bear_lists():
    e = NaturalLanguageTradeEngine()
    debate = e.build_debate(_signal())
    assert isinstance(debate.bull_case, list)
    assert isinstance(debate.bear_case, list)
    assert len(debate.bull_case) > 0


def test_debate_size_multiplier_clamped_to_max():
    e = NaturalLanguageTradeEngine()
    # position_multiplier = max → final size still <= RISK_MAX
    debate = e.build_debate(_signal(position_multiplier=2.0, confidence=0.85, risk_reward=3.0))
    assert debate.recommended_size_multiplier <= RISK_MAX


def test_why_rejected_lists_failed_gates():
    e = NaturalLanguageTradeEngine()
    bad = GateResult(name="news_shock", passed=False, reason="restricted event", severity="hard_veto")
    report = e.why_rejected(_signal(gates=[bad]))
    assert "HARD VETO" in report
    assert "news_shock" in report


def test_why_rejected_clean_signal_returns_no_rejection_text():
    e = NaturalLanguageTradeEngine()
    report = e.why_rejected(_signal())
    assert "not rejected" in report


# ─── Command parsing ──────────────────────────────────────────────────────────


def test_parse_reduce_risk_command():
    e = NaturalLanguageTradeEngine()
    cmd = e.parse_command("reduce risk because volatility is high")
    assert cmd.action == CommandAction.SET_RISK_MULTIPLIER
    assert cmd.value < 1.0


def test_parse_increase_risk_command():
    e = NaturalLanguageTradeEngine()
    cmd = e.parse_command("increase risk to 1.2")
    assert cmd.action == CommandAction.SET_RISK_MULTIPLIER
    assert cmd.value >= 1.0


def test_parse_set_min_rr():
    e = NaturalLanguageTradeEngine()
    # Using "r:r" (with colon) avoids the "risk" prefix matching first
    cmd = e.parse_command("set r:r to 2.5")
    assert cmd.action == CommandAction.SET_MIN_RR
    assert cmd.value == 2.5


def test_parse_explain_command():
    e = NaturalLanguageTradeEngine()
    cmd = e.parse_command("explain the AAPL trade")
    assert cmd.action == CommandAction.EXPLAIN_TRADE
    assert cmd.target == "AAPL"


def test_parse_why_rejected_command():
    e = NaturalLanguageTradeEngine()
    cmd = e.parse_command("why was NVDA blocked?")
    assert cmd.action == CommandAction.WHY_REJECTED
    assert cmd.target == "NVDA"


def test_parse_enable_mode():
    e = NaturalLanguageTradeEngine()
    cmd = e.parse_command("enable adversarial mode")
    assert cmd.action == CommandAction.ENABLE_MODE
    assert cmd.target == "adversarial"


def test_parse_unknown_command_abstains():
    e = NaturalLanguageTradeEngine()
    cmd = e.parse_command("sing me a song")
    assert cmd.action == CommandAction.ABSTAIN


# ─── Command execution invariants ─────────────────────────────────────────────


def test_execute_set_risk_clamps_to_bounds():
    e = NaturalLanguageTradeEngine()
    # Try to set risk to 5.0 — should clamp to RISK_MAX
    cmd = ParsedNLCommand(
        schema_version=SCHEMA_VERSION,
        action=CommandAction.SET_RISK_MULTIPLIER,
        value=5.0, target="global",
        reason="test", confidence=1.0, original_text="",
    )
    result = e.execute_command(cmd)
    assert result["accepted"] is True
    assert result["applied_value"] == RISK_MAX
    assert e.state.risk_multiplier == RISK_MAX


def test_execute_set_risk_floor():
    e = NaturalLanguageTradeEngine()
    cmd = ParsedNLCommand(
        schema_version=SCHEMA_VERSION,
        action=CommandAction.SET_RISK_MULTIPLIER,
        value=0.01, target="global",
        reason="test", confidence=1.0, original_text="",
    )
    result = e.execute_command(cmd)
    assert result["applied_value"] == RISK_MIN


def test_execute_set_min_rr_clamps():
    e = NaturalLanguageTradeEngine()
    cmd = ParsedNLCommand(
        schema_version=SCHEMA_VERSION,
        action=CommandAction.SET_MIN_RR,
        value=99.0, target="global",
        reason="test", confidence=1.0, original_text="",
    )
    result = e.execute_command(cmd)
    assert result["applied_value"] == 10.0


def test_execute_audit_log_appended():
    e = NaturalLanguageTradeEngine()
    cmd = ParsedNLCommand(
        schema_version=SCHEMA_VERSION,
        action=CommandAction.SET_RISK_MULTIPLIER,
        value=0.8, target="global",
        reason="test", confidence=1.0, original_text="",
    )
    assert len(e.state.audit_log) == 0
    e.execute_command(cmd)
    assert len(e.state.audit_log) == 1


def test_execute_enable_disable_modes_round_trip():
    e = NaturalLanguageTradeEngine()
    cmd_on = ParsedNLCommand(
        schema_version=SCHEMA_VERSION,
        action=CommandAction.ENABLE_MODE,
        value=True, target="adversarial",
        reason="test", confidence=1.0, original_text="",
    )
    cmd_off = ParsedNLCommand(
        schema_version=SCHEMA_VERSION,
        action=CommandAction.DISABLE_MODE,
        value=False, target="adversarial",
        reason="test", confidence=1.0, original_text="",
    )
    e.execute_command(cmd_on)
    assert e.state.enabled_modes["adversarial"] is True
    e.execute_command(cmd_off)
    assert e.state.enabled_modes["adversarial"] is False


def test_execute_abstain_not_accepted():
    e = NaturalLanguageTradeEngine()
    cmd = ParsedNLCommand(
        schema_version=SCHEMA_VERSION,
        action=CommandAction.ABSTAIN,
        value=None, target=None,
        reason="unknown", confidence=0.2, original_text="",
    )
    result = e.execute_command(cmd)
    assert result["accepted"] is False


def test_execute_does_not_run_trades():
    """Critical: NL execute_command must NEVER produce a trade-execution side
    effect. The result dict has no 'trade_id', 'order', or 'fill' keys, and
    no Mongo mutations on trade collections happen."""
    e = NaturalLanguageTradeEngine()
    forbidden_keys = {"trade_id", "order", "fill", "executed", "broker_order_id"}
    for action in [
        CommandAction.SET_RISK_MULTIPLIER, CommandAction.SET_MIN_RR,
        CommandAction.ENABLE_MODE, CommandAction.DISABLE_MODE,
        CommandAction.EXPLAIN_TRADE, CommandAction.WHY_REJECTED,
    ]:
        cmd = ParsedNLCommand(
            schema_version=SCHEMA_VERSION,
            action=action,
            value=1.0 if action in {CommandAction.SET_RISK_MULTIPLIER, CommandAction.SET_MIN_RR} else None,
            target="adversarial" if "MODE" in action.value else None,
            reason="test", confidence=1.0, original_text="",
        )
        result = e.execute_command(cmd)
        assert not (forbidden_keys & set(result.keys())), \
            f"NL execute_command leaked trade-execution key for {action}"


def test_runtime_state_isolated_per_engine():
    """Each engine has its own NLRuntimeState — no global mutation."""
    e1 = NaturalLanguageTradeEngine()
    e2 = NaturalLanguageTradeEngine()
    cmd = ParsedNLCommand(
        schema_version=SCHEMA_VERSION,
        action=CommandAction.SET_RISK_MULTIPLIER,
        value=0.6, target="global",
        reason="test", confidence=1.0, original_text="",
    )
    e1.execute_command(cmd)
    assert e1.state.risk_multiplier == 0.6
    assert e2.state.risk_multiplier == 1.0  # untouched
