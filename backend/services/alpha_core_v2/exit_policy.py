"""Alpha Core v2 — deterministic exit / position-protection policy (2026-06).

Doctrine: OFF/HALT revokes new ENTRIES, but open positions still need a
protection policy. This module supplies one. It is deterministic (pure decision
function + a thin runner) and broker-authoritative — it exits by calling the
engine's `close_position`, which sells the qty Public actually reports.

Default OFF: the runner is a no-op unless ``ALPHA_V2_EXIT_POLICY=1``. Enabling
it does NOT arm live ENTRIES — entries and exits are separately gated. A stop is
still a live SELL order, so treat enabling this as a live action reviewed by the
operator.

Anchors & limits:
  * entry price comes from a reconciled broker fill, never a discovery quote.
  * high-watermark and hold clock persist in SQLite, keyed to the entry receipt.
  * missing/stale quotes and short positions cannot trigger a long exit.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def _f(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    try:
        return float(raw) if raw else default
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ExitConfig:
    stop_pct: float          # hard stop below entry (0.02 = -2%)
    take_profit_pct: float   # take profit above entry (0.04 = +4%)
    trail_pct: float         # trailing stop below high-watermark (0.0 = off)
    max_hold_s: float        # time stop in seconds (0 = off)

    @classmethod
    def load(cls) -> "ExitConfig":
        return cls(
            stop_pct=max(0.0, _f("ALPHA_V2_STOP_PCT", 0.02)),
            take_profit_pct=max(0.0, _f("ALPHA_V2_TAKE_PROFIT_PCT", 0.04)),
            trail_pct=max(0.0, _f("ALPHA_V2_TRAIL_PCT", 0.0)),
            max_hold_s=max(0.0, _f("ALPHA_V2_MAX_HOLD_S", 0.0)),
        )


@dataclass(frozen=True)
class ExitDecision:
    should_exit: bool
    reason: str


def decide_exit(*, entry: float, current: float, high_watermark: float,
                held_seconds: float, cfg: ExitConfig) -> ExitDecision:
    """Pure decision. Long-only. Precedence: stop → trailing → target → time.

    No anchor (entry<=0 or current<=0) ⇒ never fabricate an exit."""
    if entry <= 0 or current <= 0:
        return ExitDecision(False, "no_anchor")
    if cfg.stop_pct > 0 and current <= entry * (1.0 - cfg.stop_pct):
        return ExitDecision(True, f"stop_loss:{current:.4f}<={entry * (1 - cfg.stop_pct):.4f}")
    if cfg.trail_pct > 0 and high_watermark > entry:
        trail_line = high_watermark * (1.0 - cfg.trail_pct)
        if current <= trail_line:
            return ExitDecision(True, f"trailing_stop:{current:.4f}<={trail_line:.4f}")
    if cfg.take_profit_pct > 0 and current >= entry * (1.0 + cfg.take_profit_pct):
        return ExitDecision(True, f"take_profit:{current:.4f}>={entry * (1 + cfg.take_profit_pct):.4f}")
    if cfg.max_hold_s > 0 and held_seconds >= cfg.max_hold_s:
        return ExitDecision(True, f"max_hold:{held_seconds:.0f}s>={cfg.max_hold_s:.0f}s")
    return ExitDecision(False, "hold")


def policy_enabled() -> bool:
    return (os.environ.get("ALPHA_V2_EXIT_POLICY") or "").strip().lower() in (
        "1", "true", "yes", "on")


class ExitPolicyRunner:
    """Applies :func:`decide_exit` to every broker-held position each call.

    Broker-authoritative: positions and the execution quote come from the
    broker; entry comes from the receipt store; exit is via
    ``engine.close_position`` (sells the broker's reported qty).

    Watermark and hold clock live in the receipt store and survive worker
    recreation and process restarts. A new entry receipt resets both.
    """

    async def run(self, engine, *, force: bool = False) -> dict:
        if not force and not policy_enabled():
            return {"ok": True, "ran": False, "reason": "exit_policy_disabled"}
        cfg = ExitConfig.load()
        try:
            positions = engine.broker.get_positions()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": f"positions_unavailable:{str(exc)[:120]}"}

        now = time.time()
        actions = []
        for p in positions:
            if p.qty <= 0 or p.side != "long":
                continue
            sym = p.symbol
            anchor = engine.store.entry_anchor(sym)
            entry = float(anchor.get("fill_price") or 0.0) if anchor else 0.0
            quote = engine.broker.get_execution_quote(sym)
            max_age = engine.config.quote_max_age_s
            if quote is None or quote.age_seconds() > max_age:
                actions.append({"symbol": sym, "decision": "execution_quote_unavailable_or_stale",
                                "exited": False, "close_submitted": False})
                continue
            current = float(quote.price)
            if anchor and current > 0:
                peak, first = engine.store.track_exit(
                    sym, anchor["receipt_id"], current, now, anchor["created_ns"] / 1e9,
                )
            else:
                peak, first = current, now
            decision = decide_exit(
                entry=entry, current=current,
                high_watermark=peak,
                held_seconds=now - first, cfg=cfg,
            )
            rec = {"symbol": sym, "entry": entry, "current": current,
                   "decision": decision.reason, "exited": False}
            if decision.should_exit:
                r = await engine.close_position(sym, reason=f"exit_policy:{decision.reason}")
                rec["close_submitted"] = (r.outcome.value == "TRADED")
                rec["exited"] = bool(r.position_reconciled and r.position_status == "reconciled_flat")
                rec["close_outcome"] = r.outcome.value
                logger.info("[exit-policy] %s -> %s (%s)", sym, r.outcome.value, decision.reason)
            actions.append(rec)
        return {"ok": True, "ran": True, "positions": len(actions), "actions": actions}


__all__ = ["ExitConfig", "ExitDecision", "decide_exit", "policy_enabled",
           "ExitPolicyRunner"]
