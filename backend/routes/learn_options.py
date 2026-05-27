"""Options Education Layer — beginner-friendly options glossary (2026-02-26, P3).

Static, curated glossary so the frontend has a single source of truth
for inline tooltips and the dedicated ``/options → Learn`` tab. No
LLM calls, no DB writes — definitions are vetted by the operator and
live in this module so changes go through code review.

Definitions follow the plain-language style of ClearValue Investing's
options walkthroughs: one short sentence first (tooltip-friendly),
then a longer paragraph for the glossary page. Each term carries a
``category`` so the UI can group ("Basics", "Greeks", "Strategies",
"Mechanics"). The exported router is mounted under ``/api/learn`` by
``route_registry.py``.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/learn", tags=["learn-options"])


# Glossary — kept in code (not Mongo) so the schema is git-auditable
# and the file ships with the deploy. Adding a term is a one-line PR.
_GLOSSARY: dict[str, dict[str, Any]] = {
    # ── Basics ───────────────────────────────────────────────────
    "call": {
        "term": "Call Option",
        "category": "Basics",
        "short": "A contract giving you the right to BUY a stock at a set price before expiration.",
        "long": (
            "A call option is a contract that gives the buyer the right — "
            "but not the obligation — to BUY 100 shares of a stock at a "
            "specific price (the strike) before a specific date (the "
            "expiration). Traders buy calls when they expect the stock to "
            "RISE. The buyer pays the premium upfront; the maximum loss "
            "is that premium."
        ),
    },
    "put": {
        "term": "Put Option",
        "category": "Basics",
        "short": "A contract giving you the right to SELL a stock at a set price before expiration.",
        "long": (
            "A put option is a contract that gives the buyer the right — "
            "but not the obligation — to SELL 100 shares of a stock at a "
            "specific price (the strike) before a specific date (the "
            "expiration). Traders buy puts when they expect the stock to "
            "FALL, or to hedge an existing long position. Max loss is the "
            "premium paid."
        ),
    },
    "strike": {
        "term": "Strike Price",
        "category": "Basics",
        "short": "The price at which the option can be exercised — buy (call) or sell (put).",
        "long": (
            "The strike (or 'exercise price') is the price at which the "
            "option contract can be acted on. For a call, you buy the "
            "stock at the strike; for a put, you sell at the strike. "
            "Strikes are listed in fixed increments by the exchange — "
            "$1, $2.50, $5, etc. — depending on the stock's price."
        ),
    },
    "premium": {
        "term": "Premium",
        "category": "Basics",
        "short": "The price you pay (or receive) for an option contract — quoted per share.",
        "long": (
            "The premium is the option's price, quoted PER SHARE but "
            "settled per contract (100 shares). A premium of $1.50 means "
            "you pay $150 per contract. The premium has two parts: "
            "INTRINSIC value (real, in-the-money distance) and "
            "EXTRINSIC value (time + volatility). Sellers collect the "
            "premium upfront in exchange for taking on obligation."
        ),
    },
    "expiration": {
        "term": "Expiration Date",
        "category": "Basics",
        "short": "The date the option contract expires and becomes worthless if not exercised.",
        "long": (
            "Every option has a fixed expiration date — typically the "
            "third Friday of the month, with weekly contracts on most "
            "Fridays. After expiration, an unexercised option ceases to "
            "exist. Time decay (theta) accelerates as expiration "
            "approaches, eating into the extrinsic value of long options."
        ),
    },
    "itm": {
        "term": "In-the-Money (ITM)",
        "category": "Basics",
        "short": "An option with intrinsic value — calls below stock price, puts above.",
        "long": (
            "A call is ITM when the stock price is ABOVE the strike — "
            "exercising it would lock in a profit on the shares. A put "
            "is ITM when the stock is BELOW the strike. ITM options "
            "carry intrinsic value and therefore cost more, but they "
            "behave more like the underlying stock (higher delta)."
        ),
    },
    "otm": {
        "term": "Out-of-the-Money (OTM)",
        "category": "Basics",
        "short": "An option with NO intrinsic value — calls above stock, puts below.",
        "long": (
            "A call is OTM when the stock is BELOW the strike; a put is "
            "OTM when the stock is ABOVE the strike. OTM options are "
            "cheaper but have only extrinsic (time + volatility) value — "
            "they expire worthless if the stock doesn't move past the "
            "strike before expiration."
        ),
    },
    "atm": {
        "term": "At-the-Money (ATM)",
        "category": "Basics",
        "short": "An option whose strike is roughly equal to the current stock price.",
        "long": (
            "ATM options have a strike very close to the current stock "
            "price. They have the most extrinsic value (highest time "
            "premium) and the most sensitivity to volatility (highest "
            "vega), but no intrinsic value. ATM contracts typically "
            "have the highest dollar premium of any single-leg play."
        ),
    },
    "intrinsic": {
        "term": "Intrinsic Value",
        "category": "Basics",
        "short": "The real, exercise-now value of an option — only ITM options have it.",
        "long": (
            "Intrinsic value is the amount an option is in-the-money. For "
            "a call: max(0, stock_price − strike). For a put: "
            "max(0, strike − stock_price). OTM and ATM options have ZERO "
            "intrinsic value — their entire premium is extrinsic."
        ),
    },
    "extrinsic": {
        "term": "Extrinsic Value (Time Value)",
        "category": "Basics",
        "short": "Premium above intrinsic value — pays for time + volatility.",
        "long": (
            "Extrinsic value = premium − intrinsic value. It reflects the "
            "PROBABILITY the option moves further into the money before "
            "expiration. Extrinsic value decays to zero at expiration "
            "(theta decay) and rises when implied volatility rises."
        ),
    },

    # ── Greeks ───────────────────────────────────────────────────
    "delta": {
        "term": "Delta (Δ)",
        "category": "Greeks",
        "short": "How much the option's price moves for a $1 move in the stock.",
        "long": (
            "Delta is the rate of change of the option price with respect "
            "to the underlying stock. A delta of 0.50 means the option "
            "gains $0.50 for every $1 the stock rises (calls have "
            "positive delta; puts have negative delta, 0 to −1). Delta "
            "is also a rough proxy for the probability the option "
            "expires ITM."
        ),
    },
    "gamma": {
        "term": "Gamma (Γ)",
        "category": "Greeks",
        "short": "How fast delta itself changes as the stock moves.",
        "long": (
            "Gamma measures the rate of change of DELTA per $1 move in "
            "the stock. High gamma (typical for ATM options near "
            "expiration) means delta — and therefore the option's "
            "price — changes rapidly. Gamma is what makes options "
            "non-linear and is the source of both 'pin risk' and big "
            "intraday P&L swings."
        ),
    },
    "theta": {
        "term": "Theta (Θ) — Time Decay",
        "category": "Greeks",
        "short": "How much value the option loses every day from time passing.",
        "long": (
            "Theta is the daily dollar value an option loses to time "
            "decay, assuming nothing else changes. A theta of −0.05 "
            "means the option loses $5 per contract per day. Long "
            "options FIGHT theta; short options COLLECT it. Theta "
            "accelerates as expiration approaches, especially in the "
            "final 30 days."
        ),
    },
    "vega": {
        "term": "Vega (ν)",
        "category": "Greeks",
        "short": "How much the option moves for each 1% change in implied volatility.",
        "long": (
            "Vega measures sensitivity to IMPLIED VOLATILITY. A vega of "
            "0.10 means the option gains $10 per contract for each 1 "
            "percentage-point rise in IV. Long options are LONG vega "
            "(benefit from volatility expansion); short options are "
            "SHORT vega. Vega is largest for ATM options far from "
            "expiration."
        ),
    },
    "rho": {
        "term": "Rho (ρ)",
        "category": "Greeks",
        "short": "Sensitivity to interest-rate changes — usually the smallest Greek.",
        "long": (
            "Rho measures how much the option price changes per 1% move "
            "in the risk-free interest rate. Calls have positive rho, "
            "puts have negative rho. For most retail timeframes (under "
            "60 days) rho is small enough to ignore, but it matters for "
            "LEAPS and long-dated structured plays."
        ),
    },

    # ── Mechanics ────────────────────────────────────────────────
    "iv": {
        "term": "Implied Volatility (IV)",
        "category": "Mechanics",
        "short": "The market's forecast of how much the stock will move — annualized.",
        "long": (
            "IV is the volatility number implied by current option "
            "prices, expressed as an annualized standard deviation. High "
            "IV = expensive options (market expects big moves); low IV "
            "= cheap options. IV typically RISES into earnings and "
            "FALLS sharply afterward — the famous 'IV crush'."
        ),
    },
    "ivr": {
        "term": "IV Rank / IV Percentile",
        "category": "Mechanics",
        "short": "Where the current IV sits versus its 52-week range — high = expensive.",
        "long": (
            "IV Rank scales today's IV against the 52-week high/low so "
            "you can compare across stocks. IV Rank > 50 means options "
            "are RELATIVELY expensive — favorable for premium sellers. "
            "IV Rank < 30 means options are cheap — favorable for buyers."
        ),
    },
    "open_interest": {
        "term": "Open Interest (OI)",
        "category": "Mechanics",
        "short": "Total number of option contracts currently open at a given strike.",
        "long": (
            "Open interest is the cumulative count of contracts that "
            "have been opened and NOT yet closed or expired. High OI "
            "means tight bid/ask spreads and good liquidity. OI "
            "increases when a new buyer-seller pair opens a position, "
            "decreases when both sides close out."
        ),
    },
    "volume": {
        "term": "Options Volume",
        "category": "Mechanics",
        "short": "Number of contracts traded today — measures current activity.",
        "long": (
            "Volume is the number of contracts that changed hands during "
            "the current session. A sudden volume spike — especially "
            "well above the 20-day average and concentrated at a single "
            "strike — is the classic signature of 'unusual options "
            "activity' that the Options Radar surfaces."
        ),
    },
    "spread": {
        "term": "Bid-Ask Spread",
        "category": "Mechanics",
        "short": "The gap between best buy and best sell price — your slippage cost.",
        "long": (
            "The bid is the highest price a buyer will pay; the ask is "
            "the lowest a seller will accept. The SPREAD is the "
            "difference. Wide spreads (low liquidity) mean you lose "
            "money the instant you enter and exit. Always check the "
            "spread before sending an order — anything over ~5% of "
            "premium is a yellow flag."
        ),
    },
    "exercise": {
        "term": "Exercise",
        "category": "Mechanics",
        "short": "Acting on the option's right — buying (call) or selling (put) the shares.",
        "long": (
            "Exercising a call means buying 100 shares per contract at "
            "the strike. Exercising a put means selling 100 shares at "
            "the strike. Most retail traders close their option "
            "position (sell the contract) rather than exercising — "
            "exercising forfeits any remaining extrinsic value."
        ),
    },
    "assignment": {
        "term": "Assignment",
        "category": "Mechanics",
        "short": "Being forced to deliver on the obligation when your short option is exercised.",
        "long": (
            "If you SOLD an option and the buyer exercises it, you are "
            "ASSIGNED — meaning you must deliver the shares (short "
            "call) or buy the shares (short put) at the strike. "
            "Assignment risk is highest for ITM options near "
            "expiration and around ex-dividend dates."
        ),
    },

    # ── Strategies ───────────────────────────────────────────────
    "covered_call": {
        "term": "Covered Call",
        "category": "Strategies",
        "short": "Sell a call against 100 shares you already own — collect premium, cap upside.",
        "long": (
            "A covered call means you own 100 shares of a stock and "
            "sell one call against them. You collect the premium up "
            "front. If the stock stays below the strike, you keep "
            "everything. If it rallies past the strike you get "
            "assigned — your shares are called away at the strike. "
            "Income strategy for sideways-to-slightly-bullish views."
        ),
    },
    "csp": {
        "term": "Cash-Secured Put",
        "category": "Strategies",
        "short": "Sell a put while holding enough cash to buy the shares if assigned.",
        "long": (
            "A cash-secured put means selling a put on a stock you'd "
            "happily own at the strike, while reserving enough cash to "
            "buy 100 shares if assigned. You collect the premium "
            "upfront. If the stock stays above the strike at "
            "expiration, you keep the premium. If it falls below, you "
            "get assigned — but at an effective basis BELOW today's "
            "market price."
        ),
    },
    "credit_spread": {
        "term": "Credit Spread",
        "category": "Strategies",
        "short": "Sell one option and buy a further-OTM option — net credit, capped risk.",
        "long": (
            "A credit spread combines a SHORT option with a LONG option "
            "further out-of-the-money in the same expiration. You "
            "collect a net credit upfront and the long leg caps your "
            "maximum loss. Common forms: BULL PUT SPREAD (bullish) and "
            "BEAR CALL SPREAD (bearish). Defined risk, defined reward."
        ),
    },
    "debit_spread": {
        "term": "Debit Spread",
        "category": "Strategies",
        "short": "Buy one option and sell a further-OTM option — net cost, capped reward.",
        "long": (
            "A debit spread pays a net debit upfront. You buy a closer-"
            "to-money option and sell a further-OTM option in the same "
            "expiration. Cheaper than a single long option and reduces "
            "theta drag, but caps the maximum profit. Common forms: "
            "BULL CALL SPREAD (bullish) and BEAR PUT SPREAD (bearish)."
        ),
    },
    "iron_condor": {
        "term": "Iron Condor",
        "category": "Strategies",
        "short": "Sell a call spread AND a put spread — profit if the stock stays in a range.",
        "long": (
            "An iron condor stacks a bear call spread above and a bull "
            "put spread below the current price. You collect two "
            "credits up front and profit if the stock stays inside the "
            "two short strikes through expiration. Best deployed in "
            "high-IV environments on stocks expected to trade "
            "sideways. Both wings are defined-risk."
        ),
    },
    "straddle": {
        "term": "Long Straddle",
        "category": "Strategies",
        "short": "Buy an ATM call AND an ATM put — profit on a BIG move either direction.",
        "long": (
            "A long straddle pairs an at-the-money call with an at-the-"
            "money put at the same strike and expiration. You profit "
            "if the stock moves sharply in EITHER direction past the "
            "combined premium paid. Common around earnings, FDA "
            "decisions, or major catalysts. Loss is capped at the "
            "premium; gain is theoretically unlimited (call leg)."
        ),
    },
    "strangle": {
        "term": "Long Strangle",
        "category": "Strategies",
        "short": "Buy an OTM call AND an OTM put — cheaper straddle, needs bigger move.",
        "long": (
            "Similar to a straddle but uses OTM strikes — typically one "
            "strike above and below the current price. Cheaper than a "
            "straddle but requires a larger move to be profitable. Pure "
            "volatility play; direction-agnostic. Time decay and IV "
            "crush are the main risks."
        ),
    },
}


@router.get("/options")
async def list_options_glossary() -> dict[str, Any]:
    """Full glossary, grouped by category for the Learn tab.

    Returns
    -------
    dict
        ``{"count": int, "categories": [...], "terms": {key: term_doc}}``
        — ``terms`` is the raw map so the frontend can do O(1)
        lookups for inline tooltips. ``categories`` is the
        canonical display order.
    """
    categories: list[str] = []
    for v in _GLOSSARY.values():
        c = v["category"]
        if c not in categories:
            categories.append(c)

    return {
        "count": len(_GLOSSARY),
        "categories": categories,
        "terms": _GLOSSARY,
    }


@router.get("/options/{term_key}")
async def get_options_term(term_key: str) -> dict[str, Any]:
    """Single-term lookup for tooltip widgets. 404 if unknown."""
    key = (term_key or "").strip().lower()
    doc = _GLOSSARY.get(key)
    if not doc:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_term", "term_key": term_key},
        )
    return {"key": key, **doc}


__all__ = ["router"]
