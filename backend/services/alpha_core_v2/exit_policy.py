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
  * entry price  — the broker fill price of the open V2 entry (receipt store).
  * high-watermark & first-seen time — tracked in-process by the runner. On a
    restart these reset; the stop/target still work off entry price, only the
    trailing-stop peak and max-hold clock restart. Documented, not hidden.
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
    ``engine.close_position`` (sells the broker's reported qty)."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self._peak: dict[str, float] = {}
        self._first_seen: dict[str, float] = {}

    async def run(self, *, force: bool = False) -> dict:
        if not force and not policy_enabled():
            return {"ok": True, "ran": False, "reason": "exit_policy_disabled"}
        cfg = ExitConfig.load()
        try:
            positions = self.engine.broker.get_positions()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": f"positions_unavailable:{str(exc)[:120]}"}

        held_symbols = {p.symbol for p in positions if p.qty > 0}
        # Drop tracking for positions that no longer exist.
        for sym in list(self._peak):
            if sym not in held_symbols:
                self._peak.pop(sym, None)
                self._first_seen.pop(sym, None)

        now = time.time()
        actions = []
        for p in positions:
            if p.qty <= 0:
                continue
            sym = p.symbol
            entry = self.engine.store.last_entry_price(sym)
            quote = self.engine.broker.get_execution_quote(sym)
            current = float(quote.price) if quote is not None else 0.0
            if current > 0:
                self._peak[sym] = max(self._peak.get(sym, current), current)
            self._first_seen.setdefault(sym, now)
            decision = decide_exit(
                entry=entry, current=current,
                high_watermark=self._peak.get(sym, current),
                held_seconds=now - self._first_seen[sym], cfg=cfg,
            )
            rec = {"symbol": sym, "entry": entry, "current": current,
                   "decision": decision.reason, "exited": False}
            if decision.should_exit:
                r = await self.engine.close_position(sym, reason=f"exit_policy:{decision.reason}")
                rec["exited"] = (r.outcome.value == "TRADED")
                rec["close_outcome"] = r.outcome.value
                self._peak.pop(sym, None)
                self._first_seen.pop(sym, None)
                logger.info("[exit-policy] %s -> %s (%s)", sym, r.outcome.value, decision.reason)
            actions.append(rec)
        return {"ok": True, "ran": True, "positions": len(actions), "actions": actions}


__all__ = ["ExitConfig", "ExitDecision", "decide_exit", "policy_enabled",
           "ExitPolicyRunner"]
