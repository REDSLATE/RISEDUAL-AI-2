"""
RISEDUAL AI — Natural Language Trading Layer
Single-file drop-in module.

What this gives you:
1. Natural language trade explanations
2. Bull/Bear/Commander debate text
3. "Why was this rejected?" debug narratives
4. Natural language command parsing
5. Hard-gated command execution
6. Optional FastAPI router

Design rule:
LLM/natural language NEVER executes trades directly.
It only explains or converts text into structured commands.
Execution remains hard-gated and deterministic.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

try:
    from fastapi import APIRouter, HTTPException
    from pydantic import BaseModel, Field, ValidationError
except Exception:  # Keeps module import-safe in non-FastAPI tests
    APIRouter = None
    HTTPException = Exception
    BaseModel = object
    Field = lambda default=None, **kwargs: default  # noqa: E731
    ValidationError = Exception


# ============================================================
# Constants / Safety Bounds
# ============================================================

RISK_MIN = 0.50
RISK_MAX = 1.25

DEFAULT_MIN_RR = 1.50
DEFAULT_STRONG_RR = 2.00
DEFAULT_MIN_CONFIDENCE = 0.55
DEFAULT_HIGH_CONFIDENCE = 0.72

SCHEMA_VERSION = "risedual.nl_trading.v1"


# ============================================================
# Enums
# ============================================================

class TradeAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    WATCHLIST = "WATCHLIST"
    REJECT = "REJECT"


class Verdict(str, Enum):
    APPROVE = "APPROVE"
    MODIFY = "MODIFY"
    REJECT = "REJECT"
    WATCHLIST = "WATCHLIST"


class CommandAction(str, Enum):
    SET_RISK_MULTIPLIER = "SET_RISK_MULTIPLIER"
    SET_MIN_RR = "SET_MIN_RR"
    ENABLE_MODE = "ENABLE_MODE"
    DISABLE_MODE = "DISABLE_MODE"
    EXPLAIN_TRADE = "EXPLAIN_TRADE"
    WHY_REJECTED = "WHY_REJECTED"
    ABSTAIN = "ABSTAIN"


# ============================================================
# Data Contracts
# ============================================================

@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    reason: str
    severity: Literal["info", "warning", "hard_veto"] = "info"


@dataclass(frozen=True)
class TradeSignal:
    ticker: str
    action: TradeAction
    confidence: float
    risk_reward: Optional[float] = None
    regime: Optional[str] = None
    regime_match: Optional[bool] = None
    sentiment_score: Optional[float] = None
    flow_score: Optional[float] = None
    whale_score: Optional[float] = None
    liquidity_score: Optional[float] = None
    dollar_volume: Optional[float] = None
    spread_bps: Optional[float] = None
    atr_pct: Optional[float] = None
    position_multiplier: float = 1.0
    gates: List[GateResult] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DebateNarrative:
    bull_case: List[str]
    bear_case: List[str]
    commander_summary: str
    commander_verdict: Verdict
    recommended_action: TradeAction
    recommended_size_multiplier: float
    risk_notes: List[str]


@dataclass(frozen=True)
class TradeExplanation:
    schema_version: str
    ticker: str
    action: TradeAction
    verdict: Verdict
    headline: str
    summary: str
    bull_case: List[str]
    bear_case: List[str]
    commander_summary: str
    risk_notes: List[str]
    rejected_reasons: List[str]
    size_explanation: str
    confidence_label: str
    raw: Dict[str, Any]


@dataclass(frozen=True)
class ParsedNLCommand:
    schema_version: str
    action: CommandAction
    value: Optional[Any]
    target: Optional[str]
    reason: str
    confidence: float
    original_text: str


@dataclass
class NLRuntimeState:
    risk_multiplier: float = 1.0
    min_rr: float = DEFAULT_MIN_RR
    enabled_modes: Dict[str, bool] = field(default_factory=dict)
    audit_log: List[Dict[str, Any]] = field(default_factory=list)


# ============================================================
# Utilities
# ============================================================

def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize_confidence(value: float) -> float:
    """
    Accepts either 0-1 or 0-100 confidence.
    Returns 0-1.
    """
    if value is None:
        return 0.0
    value = float(value)
    if value > 1.0:
        value = value / 100.0
    return clamp(value, 0.0, 1.0)


def money(value: Optional[float]) -> str:
    if value is None:
        return "unknown"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if abs(value) >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:.2f}"


def confidence_label(confidence: float) -> str:
    confidence = normalize_confidence(confidence)
    if confidence >= 0.80:
        return "very high"
    if confidence >= 0.70:
        return "high"
    if confidence >= 0.58:
        return "moderate"
    if confidence >= 0.45:
        return "low-moderate"
    return "low"


def bool_phrase(value: Optional[bool]) -> str:
    if value is True:
        return "aligned"
    if value is False:
        return "not aligned"
    return "unknown"


def has_hard_veto(signal: TradeSignal) -> bool:
    return any(g.severity == "hard_veto" and not g.passed for g in signal.gates)


def failed_gates(signal: TradeSignal) -> List[GateResult]:
    return [g for g in signal.gates if not g.passed]


def passed_gates(signal: TradeSignal) -> List[GateResult]:
    return [g for g in signal.gates if g.passed]


# ============================================================
# Deterministic Debate Engine
# ============================================================

class NaturalLanguageTradeEngine:
    """
    Main deterministic NL engine.

    This does not place trades.
    It explains, debates, parses, and safely gates natural-language commands.
    """

    def __init__(
        self,
        state: Optional[NLRuntimeState] = None,
        llm_json_parser: Optional[Callable[[str], str]] = None,
        llm_polisher: Optional[Callable[[Dict[str, Any]], str]] = None,
    ) -> None:
        self.state = state or NLRuntimeState()
        self.llm_json_parser = llm_json_parser
        self.llm_polisher = llm_polisher

    # ------------------------------------------------------------
    # Public: explain signal
    # ------------------------------------------------------------

    def explain_trade(self, signal: TradeSignal, use_llm_polish: bool = False) -> TradeExplanation:
        signal = self._normalize_signal(signal)
        debate = self.build_debate(signal)
        rejected = [g.reason for g in failed_gates(signal) if g.severity == "hard_veto"]

        verdict = debate.commander_verdict

        headline = self._headline(signal, verdict)
        summary = self._summary(signal, verdict)
        size_explanation = self._size_explanation(signal, debate)

        explanation = TradeExplanation(
            schema_version=SCHEMA_VERSION,
            ticker=signal.ticker,
            action=signal.action,
            verdict=verdict,
            headline=headline,
            summary=summary,
            bull_case=debate.bull_case,
            bear_case=debate.bear_case,
            commander_summary=debate.commander_summary,
            risk_notes=debate.risk_notes,
            rejected_reasons=rejected,
            size_explanation=size_explanation,
            confidence_label=confidence_label(signal.confidence),
            raw={
                "signal": asdict(signal),
                "debate": asdict(debate),
                "runtime_state": asdict(self.state),
            },
        )

        if use_llm_polish and self.llm_polisher is not None:
            polished = self.llm_polisher(asdict(explanation))
            return TradeExplanation(
                **{
                    **asdict(explanation),
                    "summary": polished,
                }
            )

        return explanation

    # ------------------------------------------------------------
    # Public: debate
    # ------------------------------------------------------------

    def build_debate(self, signal: TradeSignal) -> DebateNarrative:
        signal = self._normalize_signal(signal)

        bull_case = self._bull_case(signal)
        bear_case = self._bear_case(signal)
        risk_notes = self._risk_notes(signal)

        hard_veto = has_hard_veto(signal)
        rr = signal.risk_reward
        conf = signal.confidence

        if hard_veto:
            verdict = Verdict.REJECT
            recommended_action = TradeAction.REJECT
            size = 0.0
            commander = (
                f"Commander rejects {signal.ticker}. One or more hard gates failed, "
                f"so the trade is not eligible regardless of model confidence."
            )
        elif signal.action == TradeAction.HOLD:
            verdict = Verdict.WATCHLIST
            recommended_action = TradeAction.WATCHLIST
            size = 0.0
            commander = (
                f"Commander keeps {signal.ticker} on watch. The setup does not justify "
                f"execution yet."
            )
        elif rr is not None and rr < self.state.min_rr:
            verdict = Verdict.REJECT
            recommended_action = TradeAction.REJECT
            size = 0.0
            commander = (
                f"Commander rejects {signal.ticker}. Risk-reward is {rr:.2f}:1, "
                f"below the active minimum of {self.state.min_rr:.2f}:1."
            )
        elif conf >= DEFAULT_HIGH_CONFIDENCE and (rr is None or rr >= DEFAULT_STRONG_RR):
            verdict = Verdict.APPROVE
            recommended_action = signal.action
            size = clamp(signal.position_multiplier * self.state.risk_multiplier, RISK_MIN, RISK_MAX)
            commander = (
                f"Commander approves {signal.ticker}. Confidence is {confidence_label(conf)} "
                f"and the risk profile is acceptable."
            )
        else:
            verdict = Verdict.MODIFY
            recommended_action = signal.action
            size = clamp(0.50 * signal.position_multiplier * self.state.risk_multiplier, 0.0, 1.0)
            commander = (
                f"Commander modifies {signal.ticker}. The setup is tradable, but confidence, "
                f"risk-reward, or regime quality does not justify full size."
            )

        return DebateNarrative(
            bull_case=bull_case,
            bear_case=bear_case,
            commander_summary=commander,
            commander_verdict=verdict,
            recommended_action=recommended_action,
            recommended_size_multiplier=round(size, 4),
            risk_notes=risk_notes,
        )

    # ------------------------------------------------------------
    # Public: rejection debug
    # ------------------------------------------------------------

    def why_rejected(self, signal: TradeSignal) -> str:
        signal = self._normalize_signal(signal)

        failures = failed_gates(signal)
        if not failures:
            return (
                f"{signal.ticker} was not rejected by the gate layer. "
                f"Current action is {signal.action.value} with "
                f"{confidence_label(signal.confidence)} confidence."
            )

        lines = [f"{signal.ticker} rejection/debug report:"]
        for g in failures:
            marker = "HARD VETO" if g.severity == "hard_veto" else "SOFT WARNING"
            lines.append(f"- {marker}: {g.name} — {g.reason}")

        if signal.risk_reward is not None:
            lines.append(f"- Risk-reward: {signal.risk_reward:.2f}:1")
        lines.append(f"- Confidence: {signal.confidence:.2f} ({confidence_label(signal.confidence)})")
        lines.append(f"- Regime: {signal.regime or 'unknown'} / {bool_phrase(signal.regime_match)}")

        return "\n".join(lines)

    # ------------------------------------------------------------
    # Public: parse natural language command
    # ------------------------------------------------------------

    def parse_command(self, text: str) -> ParsedNLCommand:
        """
        Converts user text into a structured command.

        If an LLM JSON parser is supplied, it is tried first.
        If it fails, deterministic fallback parser runs.
        """
        clean = text.strip()

        if self.llm_json_parser is not None:
            try:
                raw = self.llm_json_parser(clean)
                data = self._safe_json(raw)
                cmd = ParsedNLCommand(
                    schema_version=SCHEMA_VERSION,
                    action=CommandAction(data["action"]),
                    value=data.get("value"),
                    target=data.get("target"),
                    reason=str(data.get("reason", "Parsed from natural language.")),
                    confidence=normalize_confidence(float(data.get("confidence", 0.75))),
                    original_text=clean,
                )
                return self._validate_command(cmd)
            except Exception:
                pass

        return self._fallback_parse_command(clean)

    # ------------------------------------------------------------
    # Public: execute parsed command safely
    # ------------------------------------------------------------

    def execute_command(self, command: ParsedNLCommand) -> Dict[str, Any]:
        """
        Hard-gated deterministic command executor.

        This does NOT execute trades.
        It only adjusts NL/runtime config or returns explanation intent.
        """
        command = self._validate_command(command)

        result: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "accepted": False,
            "action": command.action.value,
            "target": command.target,
            "value": command.value,
            "reason": command.reason,
            "timestamp": time.time(),
        }

        if command.action == CommandAction.SET_RISK_MULTIPLIER:
            value = float(command.value)
            clamped = clamp(value, RISK_MIN, RISK_MAX)
            self.state.risk_multiplier = clamped
            result.update(
                accepted=True,
                applied_value=clamped,
                note=f"Risk multiplier set to {clamped:.2f}x.",
            )

        elif command.action == CommandAction.SET_MIN_RR:
            value = float(command.value)
            clamped = clamp(value, 0.50, 10.0)
            self.state.min_rr = clamped
            result.update(
                accepted=True,
                applied_value=clamped,
                note=f"Minimum risk-reward set to {clamped:.2f}:1.",
            )

        elif command.action == CommandAction.ENABLE_MODE:
            mode = str(command.target or command.value or "").strip().lower()
            if not mode:
                result.update(accepted=False, note="No mode specified.")
            else:
                self.state.enabled_modes[mode] = True
                result.update(accepted=True, applied_value=True, note=f"Mode enabled: {mode}")

        elif command.action == CommandAction.DISABLE_MODE:
            mode = str(command.target or command.value or "").strip().lower()
            if not mode:
                result.update(accepted=False, note="No mode specified.")
            else:
                self.state.enabled_modes[mode] = False
                result.update(accepted=True, applied_value=False, note=f"Mode disabled: {mode}")

        elif command.action in {CommandAction.EXPLAIN_TRADE, CommandAction.WHY_REJECTED}:
            result.update(
                accepted=True,
                note="Command is informational. Provide a TradeSignal to explain/debug.",
            )

        else:
            result.update(
                accepted=False,
                note="Command abstained or unsupported.",
            )

        self.state.audit_log.append(result)
        return result

    # ============================================================
    # Internal narrative builders
    # ============================================================

    def _normalize_signal(self, signal: TradeSignal) -> TradeSignal:
        return TradeSignal(
            ticker=signal.ticker.upper().strip(),
            action=TradeAction(signal.action),
            confidence=normalize_confidence(signal.confidence),
            risk_reward=signal.risk_reward,
            regime=signal.regime,
            regime_match=signal.regime_match,
            sentiment_score=signal.sentiment_score,
            flow_score=signal.flow_score,
            whale_score=signal.whale_score,
            liquidity_score=signal.liquidity_score,
            dollar_volume=signal.dollar_volume,
            spread_bps=signal.spread_bps,
            atr_pct=signal.atr_pct,
            position_multiplier=clamp(float(signal.position_multiplier), 0.0, RISK_MAX),
            gates=signal.gates,
            metadata=signal.metadata,
        )

    def _bull_case(self, signal: TradeSignal) -> List[str]:
        points: List[str] = []

        if signal.confidence >= DEFAULT_HIGH_CONFIDENCE:
            points.append(f"Model confidence is {confidence_label(signal.confidence)} at {signal.confidence:.2f}.")
        elif signal.confidence >= DEFAULT_MIN_CONFIDENCE:
            points.append(f"Model confidence is acceptable at {signal.confidence:.2f}.")

        if signal.risk_reward is not None and signal.risk_reward >= DEFAULT_STRONG_RR:
            points.append(f"Risk-reward is attractive at {signal.risk_reward:.2f}:1.")
        elif signal.risk_reward is not None and signal.risk_reward >= DEFAULT_MIN_RR:
            points.append(f"Risk-reward clears the minimum threshold at {signal.risk_reward:.2f}:1.")

        if signal.regime_match is True:
            points.append(f"The trade aligns with the current {signal.regime or 'market'} regime.")

        if signal.sentiment_score is not None and signal.sentiment_score > 0.15:
            points.append(f"Sentiment is supportive with a score of {signal.sentiment_score:.2f}.")

        if signal.flow_score is not None and signal.flow_score > 0.15:
            points.append(f"Flow data supports the setup with a score of {signal.flow_score:.2f}.")

        if signal.whale_score is not None and signal.whale_score > 0.15:
            points.append(f"Large-player activity is supportive with a whale score of {signal.whale_score:.2f}.")

        if not points:
            points.append("No dominant bullish edge was detected from the available structured inputs.")

        return points

    def _bear_case(self, signal: TradeSignal) -> List[str]:
        points: List[str] = []

        if signal.confidence < DEFAULT_MIN_CONFIDENCE:
            points.append(f"Model confidence is weak at {signal.confidence:.2f}.")

        if signal.risk_reward is not None and signal.risk_reward < self.state.min_rr:
            points.append(
                f"Risk-reward is only {signal.risk_reward:.2f}:1, below the active threshold of "
                f"{self.state.min_rr:.2f}:1."
            )

        if signal.regime_match is False:
            points.append(f"The setup does not align with the current {signal.regime or 'market'} regime.")

        if signal.sentiment_score is not None and signal.sentiment_score < -0.15:
            points.append(f"Sentiment is negative with a score of {signal.sentiment_score:.2f}.")

        if signal.flow_score is not None and signal.flow_score < -0.15:
            points.append(f"Flow data is unfavorable with a score of {signal.flow_score:.2f}.")

        if signal.spread_bps is not None and signal.spread_bps >= 75:
            points.append(f"Spread is wide at {signal.spread_bps:.1f} bps, indicating liquidity risk.")

        if signal.dollar_volume is not None and signal.dollar_volume < 2_000_000:
            points.append(f"Dollar volume is thin at {money(signal.dollar_volume)}, creating liquidity risk.")

        for g in failed_gates(signal):
            points.append(f"Gate concern: {g.name} — {g.reason}")

        if not points:
            points.append("No major bearish objection was detected from the available structured inputs.")

        return points

    def _risk_notes(self, signal: TradeSignal) -> List[str]:
        notes: List[str] = []

        if signal.risk_reward is not None:
            notes.append(f"Risk-reward: {signal.risk_reward:.2f}:1.")

        if signal.atr_pct is not None:
            notes.append(f"ATR volatility estimate: {signal.atr_pct:.2%}.")

        if signal.liquidity_score is not None:
            notes.append(f"Liquidity score: {signal.liquidity_score:.2f}.")

        if signal.dollar_volume is not None:
            notes.append(f"Dollar volume: {money(signal.dollar_volume)}.")

        if signal.spread_bps is not None:
            notes.append(f"Spread: {signal.spread_bps:.1f} bps.")

        if self.state.risk_multiplier != 1.0:
            notes.append(f"Runtime risk multiplier is active at {self.state.risk_multiplier:.2f}x.")

        if not notes:
            notes.append("No additional risk telemetry was provided.")

        return notes

    def _headline(self, signal: TradeSignal, verdict: Verdict) -> str:
        if verdict == Verdict.REJECT:
            return f"{signal.ticker}: rejected by risk governance."
        if verdict == Verdict.APPROVE:
            return f"{signal.ticker}: {signal.action.value} approved."
        if verdict == Verdict.MODIFY:
            return f"{signal.ticker}: {signal.action.value} allowed at reduced/modified size."
        return f"{signal.ticker}: watchlist only."

    def _summary(self, signal: TradeSignal, verdict: Verdict) -> str:
        rr_text = "unknown" if signal.risk_reward is None else f"{signal.risk_reward:.2f}:1"
        return (
            f"{signal.ticker} produced a {signal.action.value} signal with "
            f"{confidence_label(signal.confidence)} confidence ({signal.confidence:.2f}). "
            f"Regime alignment is {bool_phrase(signal.regime_match)}. "
            f"Risk-reward is {rr_text}. Commander verdict: {verdict.value}."
        )

    def _size_explanation(self, signal: TradeSignal, debate: DebateNarrative) -> str:
        if debate.recommended_size_multiplier <= 0:
            return "No position size is recommended because the trade is rejected or watchlist-only."
        return (
            f"Recommended size multiplier is {debate.recommended_size_multiplier:.2f}x, "
            f"after applying signal sizing, runtime risk multiplier, and hard bounds."
        )

    # ============================================================
    # Internal command parsing
    # ============================================================

    def _fallback_parse_command(self, text: str) -> ParsedNLCommand:
        lower = text.lower().strip()

        # Explain / why rejected
        if "why" in lower and ("reject" in lower or "blocked" in lower or "veto" in lower):
            ticker = self._extract_ticker(text)
            return ParsedNLCommand(
                schema_version=SCHEMA_VERSION,
                action=CommandAction.WHY_REJECTED,
                value=None,
                target=ticker,
                reason="User asked why a trade was rejected.",
                confidence=0.90,
                original_text=text,
            )

        if "explain" in lower or "break down" in lower or "tell me why" in lower:
            ticker = self._extract_ticker(text)
            return ParsedNLCommand(
                schema_version=SCHEMA_VERSION,
                action=CommandAction.EXPLAIN_TRADE,
                value=None,
                target=ticker,
                reason="User asked for a trade explanation.",
                confidence=0.85,
                original_text=text,
            )

        # Risk multiplier
        risk_number = self._extract_number(text)
        if "risk" in lower and any(w in lower for w in ["reduce", "lower", "cut", "decrease"]):
            value = risk_number if risk_number is not None else 0.75
            if value > 2:
                value = value / 100.0
            return ParsedNLCommand(
                schema_version=SCHEMA_VERSION,
                action=CommandAction.SET_RISK_MULTIPLIER,
                value=value,
                target="global",
                reason="User requested reduced risk exposure.",
                confidence=0.80,
                original_text=text,
            )

        if "risk" in lower and any(w in lower for w in ["increase", "raise", "boost"]):
            value = risk_number if risk_number is not None else 1.10
            if value > 2:
                value = value / 100.0
            return ParsedNLCommand(
                schema_version=SCHEMA_VERSION,
                action=CommandAction.SET_RISK_MULTIPLIER,
                value=value,
                target="global",
                reason="User requested increased risk exposure within hard bounds.",
                confidence=0.80,
                original_text=text,
            )

        if "risk" in lower and risk_number is not None:
            value = risk_number
            if value > 2:
                value = value / 100.0
            return ParsedNLCommand(
                schema_version=SCHEMA_VERSION,
                action=CommandAction.SET_RISK_MULTIPLIER,
                value=value,
                target="global",
                reason="User specified a risk multiplier.",
                confidence=0.75,
                original_text=text,
            )

        # Minimum R:R
        if any(token in lower for token in ["risk reward", "risk-reward", "r:r", "rr"]):
            number = risk_number if risk_number is not None else DEFAULT_MIN_RR
            return ParsedNLCommand(
                schema_version=SCHEMA_VERSION,
                action=CommandAction.SET_MIN_RR,
                value=number,
                target="global",
                reason="User adjusted minimum risk-reward threshold.",
                confidence=0.75,
                original_text=text,
            )

        # Modes
        if any(w in lower for w in ["enable", "turn on", "activate"]):
            mode = self._extract_mode(lower)
            return ParsedNLCommand(
                schema_version=SCHEMA_VERSION,
                action=CommandAction.ENABLE_MODE,
                value=True,
                target=mode,
                reason="User requested enabling a mode.",
                confidence=0.70,
                original_text=text,
            )

        if any(w in lower for w in ["disable", "turn off", "deactivate"]):
            mode = self._extract_mode(lower)
            return ParsedNLCommand(
                schema_version=SCHEMA_VERSION,
                action=CommandAction.DISABLE_MODE,
                value=False,
                target=mode,
                reason="User requested disabling a mode.",
                confidence=0.70,
                original_text=text,
            )

        return ParsedNLCommand(
            schema_version=SCHEMA_VERSION,
            action=CommandAction.ABSTAIN,
            value=None,
            target=None,
            reason="Could not confidently map natural language to a safe structured command.",
            confidence=0.25,
            original_text=text,
        )

    def _validate_command(self, cmd: ParsedNLCommand) -> ParsedNLCommand:
        if cmd.action == CommandAction.SET_RISK_MULTIPLIER:
            if cmd.value is None:
                raise ValueError("SET_RISK_MULTIPLIER requires value.")
            value = float(cmd.value)
            return ParsedNLCommand(
                schema_version=SCHEMA_VERSION,
                action=cmd.action,
                value=clamp(value, RISK_MIN, RISK_MAX),
                target=cmd.target or "global",
                reason=cmd.reason,
                confidence=normalize_confidence(cmd.confidence),
                original_text=cmd.original_text,
            )

        if cmd.action == CommandAction.SET_MIN_RR:
            if cmd.value is None:
                raise ValueError("SET_MIN_RR requires value.")
            value = clamp(float(cmd.value), 0.50, 10.0)
            return ParsedNLCommand(
                schema_version=SCHEMA_VERSION,
                action=cmd.action,
                value=value,
                target=cmd.target or "global",
                reason=cmd.reason,
                confidence=normalize_confidence(cmd.confidence),
                original_text=cmd.original_text,
            )

        return ParsedNLCommand(
            schema_version=SCHEMA_VERSION,
            action=cmd.action,
            value=cmd.value,
            target=cmd.target,
            reason=cmd.reason,
            confidence=normalize_confidence(cmd.confidence),
            original_text=cmd.original_text,
        )

    def _safe_json(self, raw: str) -> Dict[str, Any]:
        raw = raw.strip()
        raw = re.sub(r"^```(?:json)?", "", raw)
        raw = re.sub(r"```$", "", raw).strip()

        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]

        return json.loads(raw)

    def _extract_number(self, text: str) -> Optional[float]:
        match = re.search(r"(\d+(?:\.\d+)?)", text)
        if not match:
            return None
        return float(match.group(1))

    def _extract_ticker(self, text: str) -> Optional[str]:
        candidates = re.findall(r"\b[A-Z]{1,6}\b", text)
        stop = {"BUY", "SELL", "HOLD", "WHY", "THE", "AND", "FOR"}
        for c in candidates:
            if c not in stop:
                return c
        return None

    def _extract_mode(self, lower: str) -> Optional[str]:
        known = [
            "adversarial",
            "commander",
            "sovereign",
            "shadow",
            "web_research",
            "liquidity_gate",
            "strict_risk",
            "debug",
        ]
        for mode in known:
            if mode.replace("_", " ") in lower or mode in lower:
                return mode
        cleaned = lower
        for word in ["enable", "disable", "turn on", "turn off", "activate", "deactivate", "mode"]:
            cleaned = cleaned.replace(word, "")
        cleaned = cleaned.strip()
        return cleaned or None


# ============================================================
# Optional FastAPI Models / Router
# ============================================================

if BaseModel is not object:

    class GateResultIn(BaseModel):
        name: str
        passed: bool
        reason: str
        severity: Literal["info", "warning", "hard_veto"] = "info"


    class TradeSignalIn(BaseModel):
        ticker: str
        action: Literal["BUY", "SELL", "HOLD", "WATCHLIST", "REJECT"]
        confidence: float
        risk_reward: Optional[float] = None
        regime: Optional[str] = None
        regime_match: Optional[bool] = None
        sentiment_score: Optional[float] = None
        flow_score: Optional[float] = None
        whale_score: Optional[float] = None
        liquidity_score: Optional[float] = None
        dollar_volume: Optional[float] = None
        spread_bps: Optional[float] = None
        atr_pct: Optional[float] = None
        position_multiplier: float = 1.0
        gates: List[GateResultIn] = Field(default_factory=list)
        metadata: Dict[str, Any] = Field(default_factory=dict)


    class NLCommandIn(BaseModel):
        text: str


    class ExecuteCommandIn(BaseModel):
        action: Literal[
            "SET_RISK_MULTIPLIER",
            "SET_MIN_RR",
            "ENABLE_MODE",
            "DISABLE_MODE",
            "EXPLAIN_TRADE",
            "WHY_REJECTED",
            "ABSTAIN",
        ]
        value: Optional[Any] = None
        target: Optional[str] = None
        reason: str = "Manual command."
        confidence: float = 1.0
        original_text: str = ""


def _to_signal(payload: Any) -> TradeSignal:
    return TradeSignal(
        ticker=payload.ticker,
        action=TradeAction(payload.action),
        confidence=payload.confidence,
        risk_reward=payload.risk_reward,
        regime=payload.regime,
        regime_match=payload.regime_match,
        sentiment_score=payload.sentiment_score,
        flow_score=payload.flow_score,
        whale_score=payload.whale_score,
        liquidity_score=payload.liquidity_score,
        dollar_volume=payload.dollar_volume,
        spread_bps=payload.spread_bps,
        atr_pct=payload.atr_pct,
        position_multiplier=payload.position_multiplier,
        gates=[
            GateResult(
                name=g.name,
                passed=g.passed,
                reason=g.reason,
                severity=g.severity,
            )
            for g in payload.gates
        ],
        metadata=payload.metadata,
    )


engine = NaturalLanguageTradeEngine()

if APIRouter is not None:
    # Owner-gated dependency — protects state-mutating endpoints. Imported
    # lazily inside each handler to keep this module import-safe in tests.
    from fastapi import Request, Depends

    async def _owner_dep(request: Request):
        from routes.admin import _require_owner
        return await _require_owner(request)

    router = APIRouter(prefix="/api/nl-trading", tags=["Natural Language Trading"])

    @router.post("/explain", dependencies=[Depends(_owner_dep)])
    def explain_trade(payload: TradeSignalIn) -> Dict[str, Any]:
        try:
            signal = _to_signal(payload)
            explanation = engine.explain_trade(signal)
            return asdict(explanation)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/debate", dependencies=[Depends(_owner_dep)])
    def debate_trade(payload: TradeSignalIn) -> Dict[str, Any]:
        try:
            signal = _to_signal(payload)
            debate = engine.build_debate(signal)
            return asdict(debate)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/why-rejected", dependencies=[Depends(_owner_dep)])
    def why_rejected(payload: TradeSignalIn) -> Dict[str, Any]:
        try:
            signal = _to_signal(payload)
            return {
                "schema_version": SCHEMA_VERSION,
                "ticker": signal.ticker,
                "report": engine.why_rejected(signal),
            }
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/parse-command", dependencies=[Depends(_owner_dep)])
    def parse_command(payload: NLCommandIn) -> Dict[str, Any]:
        try:
            command = engine.parse_command(payload.text)
            return asdict(command)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/execute-command", dependencies=[Depends(_owner_dep)])
    def execute_command(payload: ExecuteCommandIn) -> Dict[str, Any]:
        try:
            command = ParsedNLCommand(
                schema_version=SCHEMA_VERSION,
                action=CommandAction(payload.action),
                value=payload.value,
                target=payload.target,
                reason=payload.reason,
                confidence=payload.confidence,
                original_text=payload.original_text,
            )
            return engine.execute_command(command)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.get("/state", dependencies=[Depends(_owner_dep)])
    def state() -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "state": asdict(engine.state),
        }


def set_db(db: Any) -> None:  # noqa: ARG001
    """Registry contract — this router has no Mongo dependency."""
    return None


# ============================================================
# Example local test
# ============================================================

if __name__ == "__main__":
    test_signal = TradeSignal(
        ticker="AAPL",
        action=TradeAction.BUY,
        confidence=0.74,
        risk_reward=2.15,
        regime="bullish continuation",
        regime_match=True,
        sentiment_score=0.22,
        flow_score=0.31,
        whale_score=0.18,
        liquidity_score=0.88,
        dollar_volume=95_000_000,
        spread_bps=12,
        atr_pct=0.024,
        position_multiplier=1.0,
        gates=[
            GateResult(
                name="risk_reward_gate",
                passed=True,
                reason="Risk-reward clears minimum threshold.",
            ),
            GateResult(
                name="liquidity_gate",
                passed=True,
                reason="Dollar volume and spread are acceptable.",
            ),
        ],
    )

    e = NaturalLanguageTradeEngine()
    print(json.dumps(asdict(e.explain_trade(test_signal)), indent=2))

    cmd = e.parse_command("Reduce risk because the market is choppy")
    print(json.dumps(asdict(cmd), indent=2))
    print(json.dumps(e.execute_command(cmd), indent=2))
