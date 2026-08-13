"""
Alpha — account-aware decision overlay.

Market conviction remains a MARKET score.
Account fit answers a separate question: "Does this opportunity fit the
current account right now?"

The overlay is deterministic. An LLM may read the resulting context/reasons,
but it cannot silently rewrite buying power or position facts.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from services.alpha_broker_account_context import AccountContext


@dataclass(frozen=True)
class AccountFit:
    score: float
    size_multiplier: float
    verdict: str  # PASS | REDUCE | HOLD | BLOCK
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_account_fit(
    *,
    context: AccountContext,
    symbol: str,
    side: str,
    desired_notional: float,
    allow_add_to_existing: bool = True,
    max_single_name_pct: float = 0.25,
    min_buying_power_buffer_pct: float = 0.05,
) -> AccountFit:
    symbol = symbol.upper().strip()
    side = side.upper().strip()
    desired_notional = max(0.0, float(desired_notional))
    reasons: list[str] = []
    score = 1.0
    mult = 1.0

    pos = next((p for p in context.positions if p.symbol == symbol), None)
    open_same_side = any(
        o.symbol == symbol and o.side == side and o.status not in {"filled", "cancelled", "canceled", "rejected"}
        for o in context.open_orders
    )

    # Prevent duplicate orders while one is already working.
    if open_same_side:
        return AccountFit(
            score=0.0,
            size_multiplier=0.0,
            verdict="BLOCK",
            reasons=("OPEN_ORDER_ALREADY_EXISTS",),
        )

    # BUY/ADD logic only. Exits should not be blocked because buying power is low.
    if side in {"BUY", "ADD", "COVER"}:
        if pos and pos.side == "long" and side in {"BUY", "ADD"}:
            reasons.append("EXISTING_POSITION")
            if not allow_add_to_existing:
                return AccountFit(0.0, 0.0, "BLOCK", tuple(reasons + ["ADD_DISABLED"]))
            score -= 0.15
            mult *= 0.75

        if context.buying_power <= 0:
            return AccountFit(
                score=0.0, size_multiplier=0.0, verdict="BLOCK",
                reasons=tuple(reasons + ["NO_BUYING_POWER"]),
            )

        buffer = max(0.0, context.equity * min_buying_power_buffer_pct)
        spendable = max(0.0, context.buying_power - buffer)
        if desired_notional > spendable:
            if spendable <= 0:
                return AccountFit(
                    score=0.0, size_multiplier=0.0, verdict="BLOCK",
                    reasons=tuple(reasons + ["BUYING_POWER_BUFFER"]),
                )
            ratio = spendable / max(desired_notional, 1e-9)
            score -= 0.25
            mult *= max(0.10, min(1.0, ratio))
            reasons.append("SIZE_TO_BUYING_POWER")

        existing_value = abs(pos.market_value) if pos else 0.0
        projected = existing_value + desired_notional
        if context.equity > 0 and projected / context.equity > max_single_name_pct:
            room = max(0.0, context.equity * max_single_name_pct - existing_value)
            if room <= 0:
                return AccountFit(
                    score=max(0.0, score - 0.50),
                    size_multiplier=0.0,
                    verdict="BLOCK",
                    reasons=tuple(reasons + ["SINGLE_NAME_CAP"]),
                )
            mult *= min(1.0, room / max(desired_notional, 1e-9))
            score -= 0.20
            reasons.append("REDUCE_SINGLE_NAME_CONCENTRATION")

    # SELL of something not held is suspicious; a SHORT policy can be handled separately.
    if side == "SELL" and not pos:
        return AccountFit(
            score=0.0, size_multiplier=0.0, verdict="BLOCK",
            reasons=("NO_POSITION_TO_SELL",),
        )

    score = max(0.0, min(1.0, score))
    mult = max(0.0, min(1.0, mult))
    verdict = "PASS" if mult >= 0.999 else "REDUCE"
    return AccountFit(score, mult, verdict, tuple(reasons or ["ACCOUNT_FIT_OK"]))


def decision_payload(
    *,
    market_decision: dict[str, Any],
    context: AccountContext,
    desired_notional: float,
) -> dict[str, Any]:
    """
    Attach account facts to a candidate WITHOUT altering market conviction.
    Alpha's decision model may inspect this payload before emitting the final
    action.
    """
    symbol = str(market_decision.get("symbol") or "").upper()
    side = str(market_decision.get("action") or market_decision.get("side") or "WATCH").upper()

    fit = evaluate_account_fit(
        context=context,
        symbol=symbol,
        side=side,
        desired_notional=desired_notional,
    )

    out = dict(market_decision)
    out["market_conviction"] = market_decision.get("conviction", market_decision.get("confidence"))
    out["account_context"] = context.to_model_payload()
    out["account_fit"] = fit.to_dict()
    out["broker_effective_notional"] = round(max(0.0, desired_notional * fit.size_multiplier), 2)
    return out
