"""MooMoo Options Policy — V1 (read-only, live chain preview).

This module is the single source of truth for how RISEDUAL AI would
select and validate an options contract on MooMoo. In V1 we do NOT
submit any option orders — ``moomoo_broker_adapter.submit_option`` stays
gated OFF until the operator explicitly flips ``MOOMOO_OPTIONS_ENABLED=1``.

What we do expose in V1:
    * ``policy()``      — the effective config the system is running with,
                          sourced from env vars with conservative defaults.
    * ``preview(sym)``  — a live-chain dry run: "if Alpha wanted a call
                          on <sym> right now, what contract would it pick,
                          why, and what alternatives got rejected?"

The preview endpoint uses MooMoo's own option chain via the market data
adapter — no synthetic contracts, no mocked fills. When OpenD is
unreachable it returns ``available=False`` so the UI can render a clear
"MooMoo unavailable" state instead of fake data.

Playbook defaults (per operator, 2026-02):
    delta target    : 0.30 – 0.45
    DTE window      : 7 – 21 days
    open interest   : ≥ 1000
    daily volume    : ≥ 500
    spread cap      : ≤ 8% of option mid
    order type      : marketable LIMIT only (never MARKET)
    max contracts   : 1 initially
    stale quote max : 30 seconds
    earnings blackout for new short-DTE positions

These knobs are configurable via env vars so the operator can dial the
policy tighter (e.g. ``MOOMOO_OPT_MIN_OI=2000``) without redeploying.
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── config from env with defaults ────────────────────────────────────


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    raw = (os.environ.get(key) or "").strip().lower()
    if raw == "":
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass
class OptionsPolicy:
    enabled: bool = False
    delta_min: float = 0.30
    delta_max: float = 0.45
    dte_min: int = 7
    dte_max: int = 21
    min_open_interest: int = 1000
    min_daily_volume: int = 500
    max_spread_pct: float = 8.0        # percentage of mid
    order_type: str = "marketable_limit"  # never MARKET in V1
    max_contracts: int = 1
    stale_quote_max_sec: int = 30
    earnings_blackout: bool = True
    # Explanatory notes surfaced to the UI — never treat as executable config
    notes: list[str] = field(default_factory=list)


def policy() -> OptionsPolicy:
    p = OptionsPolicy(
        enabled=_env_bool("MOOMOO_OPTIONS_ENABLED", False),
        delta_min=_env_float("MOOMOO_OPT_DELTA_MIN", 0.30),
        delta_max=_env_float("MOOMOO_OPT_DELTA_MAX", 0.45),
        dte_min=_env_int("MOOMOO_OPT_DTE_MIN", 7),
        dte_max=_env_int("MOOMOO_OPT_DTE_MAX", 21),
        min_open_interest=_env_int("MOOMOO_OPT_MIN_OI", 1000),
        min_daily_volume=_env_int("MOOMOO_OPT_MIN_VOL", 500),
        max_spread_pct=_env_float("MOOMOO_OPT_MAX_SPREAD_PCT", 8.0),
        order_type="marketable_limit",
        max_contracts=_env_int("MOOMOO_OPT_MAX_CONTRACTS", 1),
        stale_quote_max_sec=_env_int("MOOMOO_OPT_STALE_QUOTE_SEC", 30),
        earnings_blackout=_env_bool("MOOMOO_OPT_EARNINGS_BLACKOUT", True),
    )
    p.notes = [
        "V1 read-only — submit_option remains gated OFF regardless of "
        "MOOMOO_OPTIONS_ENABLED until the operator flips both this flag "
        "and the execution-policy readiness check.",
        "Never use MARKET orders on options; wide spreads make MARKET "
        "orders unsafe even on liquid names.",
        "Spread cap is enforced as a percentage of option mid, not "
        "cents — a $0.10 spread means very different things on a $0.50 "
        "contract vs a $5.00 contract.",
        "Stale quotes (older than stale_quote_max_sec) auto-reject.",
    ]
    return p


# ── contract selection ──────────────────────────────────────────────

def _to_date(raw: Any) -> Optional[date]:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    if not raw:
        return None
    s = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _spread_pct(bid: float, ask: float) -> Optional[float]:
    if bid <= 0 or ask <= 0 or ask <= bid:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return round((ask - bid) / mid * 100.0, 2)


@dataclass
class ContractScore:
    contract: dict
    passed: bool
    rejection: Optional[str] = None
    dte: Optional[int] = None
    delta: Optional[float] = None
    spread_pct: Optional[float] = None


def _score_contract(c: dict, p: OptionsPolicy, today: date, stale_cutoff_ns: int) -> ContractScore:
    """Return a ContractScore for one chain row. ``c`` is expected to
    look like the flat dict produced by ``moomoo_market_data_adapter.
    to_option_chain_row()`` — bid/ask/last/volume/open_interest/delta/
    expiry/strike/opt_type/data_time_ns."""
    delta = c.get("delta")
    if delta is not None:
        try:
            delta = abs(float(delta))
        except (TypeError, ValueError):
            delta = None

    bid = float(c.get("bid") or 0.0)
    ask = float(c.get("ask") or 0.0)
    spr = _spread_pct(bid, ask)
    oi = int(c.get("open_interest") or 0)
    vol = int(c.get("volume") or 0)
    expiry = _to_date(c.get("expiry"))
    dte = (expiry - today).days if expiry else None
    data_ts_ns = c.get("data_time_ns")

    if data_ts_ns is not None:
        try:
            if int(data_ts_ns) < stale_cutoff_ns:
                return ContractScore(c, False, "stale_quote", dte, delta, spr)
        except (TypeError, ValueError):
            pass

    if dte is None:
        return ContractScore(c, False, "expiry_unknown", dte, delta, spr)
    if dte < p.dte_min or dte > p.dte_max:
        return ContractScore(c, False, f"dte_out_of_range({dte})", dte, delta, spr)

    if delta is None:
        return ContractScore(c, False, "delta_unknown", dte, delta, spr)
    if delta < p.delta_min or delta > p.delta_max:
        return ContractScore(c, False, f"delta_out_of_band({delta:.2f})", dte, delta, spr)

    if oi < p.min_open_interest:
        return ContractScore(c, False, f"open_interest_below_floor({oi})", dte, delta, spr)
    if vol < p.min_daily_volume:
        return ContractScore(c, False, f"volume_below_floor({vol})", dte, delta, spr)

    if spr is None:
        return ContractScore(c, False, "no_two_sided_quote", dte, delta, spr)
    if spr > p.max_spread_pct:
        return ContractScore(c, False, f"spread_over_cap({spr:.1f}%)", dte, delta, spr)

    return ContractScore(c, True, None, dte, delta, spr)


def _selection_score(sc: ContractScore, p: OptionsPolicy) -> float:
    """Higher is better. Used only among rows that already passed all
    hard gates — so this is a tie-break, not a filter."""
    # Prefer deltas near the middle of the band and tighter spreads.
    delta_mid = (p.delta_min + p.delta_max) / 2.0
    delta_dist = abs((sc.delta or delta_mid) - delta_mid)
    spread = sc.spread_pct if sc.spread_pct is not None else p.max_spread_pct
    # Normalize each component to 0..1 (1 = best) and blend evenly.
    delta_score = 1.0 - min(1.0, delta_dist / max(0.01, delta_mid))
    spread_score = 1.0 - min(1.0, spread / max(0.01, p.max_spread_pct))
    return 0.6 * delta_score + 0.4 * spread_score


def select_contract(
    chain: list[dict],
    *,
    direction: str = "call",
    now: Optional[datetime] = None,
    p: Optional[OptionsPolicy] = None,
) -> dict:
    """Deterministic contract selection over a live chain.

    Returns a dict with:
        selected: dict | None
        rejected: list[dict]  (top rejections with reasons)
        rule_hits: dict[str, int]
    """
    pol = p or policy()
    now_dt = now or datetime.now(timezone.utc)
    today = now_dt.date()
    stale_cutoff_ns = int(
        (now_dt - timedelta(seconds=pol.stale_quote_max_sec)).timestamp() * 1e9
    )
    want = "call" if (direction or "").lower().startswith("c") else "put"

    scored: list[ContractScore] = []
    rule_hits: dict[str, int] = {}
    for c in chain or []:
        opt_type = str(c.get("opt_type") or c.get("option_type") or "").lower()
        # Normalize MooMoo enum names (CALL/PUT) as well as short forms.
        if opt_type not in ("call", "put"):
            if "call" in opt_type:
                opt_type = "call"
            elif "put" in opt_type:
                opt_type = "put"
        if opt_type != want:
            continue
        sc = _score_contract(c, pol, today, stale_cutoff_ns)
        scored.append(sc)
        if not sc.passed:
            rule_hits[sc.rejection or "unknown"] = (
                rule_hits.get(sc.rejection or "unknown", 0) + 1
            )

    passing = [s for s in scored if s.passed]
    passing.sort(key=lambda s: _selection_score(s, pol), reverse=True)

    def _serialize(sc: ContractScore) -> dict:
        c = sc.contract
        bid = float(c.get("bid") or 0.0)
        ask = float(c.get("ask") or 0.0)
        mid = round((bid + ask) / 2.0, 4) if bid > 0 and ask > 0 else None
        return {
            "symbol": c.get("symbol") or c.get("code"),
            "underlying": c.get("underlying"),
            "opt_type": c.get("opt_type") or c.get("option_type"),
            "strike": c.get("strike"),
            "expiry": c.get("expiry"),
            "dte": sc.dte,
            "delta": round(sc.delta, 3) if sc.delta is not None else None,
            "bid": bid or None,
            "ask": ask or None,
            "mid": mid,
            "spread_pct": sc.spread_pct,
            "open_interest": c.get("open_interest"),
            "volume": c.get("volume"),
            "rejection": sc.rejection,
        }

    selected_row: Optional[dict] = None
    reason: Optional[str] = None
    est_debit: Optional[float] = None
    est_max_risk: Optional[float] = None
    if passing:
        top = passing[0]
        selected_row = _serialize(top)
        est_debit = selected_row.get("mid")
        # For long calls/puts max risk == debit × 100 × contracts.
        if est_debit is not None:
            est_max_risk = round(
                est_debit * 100.0 * max(1, pol.max_contracts), 2
            )
            selected_row["estimated_debit"] = est_debit
            selected_row["estimated_max_risk"] = est_max_risk
            selected_row["contracts"] = pol.max_contracts
        reason = (
            f"delta {selected_row['delta']} in band "
            f"[{pol.delta_min}-{pol.delta_max}], DTE {selected_row['dte']}d "
            f"in window [{pol.dte_min}-{pol.dte_max}], spread "
            f"{selected_row['spread_pct']}% ≤ {pol.max_spread_pct}%, "
            f"OI {selected_row['open_interest']}, "
            f"volume {selected_row['volume']}."
        )
        selected_row["why_selected"] = reason

    # Top rejections — sort by "closeness to passing" heuristically:
    #   contracts whose delta is inside the band but failed on
    #   liquidity should surface before ones that failed on delta.
    def _rej_prio(sc: ContractScore) -> tuple:
        # Order rejection reasons roughly worst → best-almost-passed.
        priority_map = {
            "delta_unknown": 0,
            "expiry_unknown": 0,
            "dte_out_of_range": 1,
            "delta_out_of_band": 2,
            "no_two_sided_quote": 3,
            "stale_quote": 3,
            "open_interest_below_floor": 4,
            "volume_below_floor": 4,
            "spread_over_cap": 5,
        }
        r = (sc.rejection or "").split("(")[0]
        return (priority_map.get(r, 0), sc.delta or 0.0)

    rejected = [
        _serialize(sc) for sc in sorted(scored, key=_rej_prio, reverse=True)
        if not sc.passed
    ][:5]

    return {
        "selected": selected_row,
        "rejected": rejected,
        "rule_hits": rule_hits,
        "candidates_evaluated": len(scored),
        "policy_used": policy_to_dict(pol),
    }


# ── serialization ────────────────────────────────────────────────────


def policy_to_dict(p: OptionsPolicy) -> dict:
    return {
        "enabled": p.enabled,
        "delta_min": p.delta_min,
        "delta_max": p.delta_max,
        "dte_min": p.dte_min,
        "dte_max": p.dte_max,
        "min_open_interest": p.min_open_interest,
        "min_daily_volume": p.min_daily_volume,
        "max_spread_pct": p.max_spread_pct,
        "order_type": p.order_type,
        "max_contracts": p.max_contracts,
        "stale_quote_max_sec": p.stale_quote_max_sec,
        "earnings_blackout": p.earnings_blackout,
        "notes": p.notes,
    }


__all__ = [
    "OptionsPolicy",
    "policy",
    "policy_to_dict",
    "select_contract",
]
