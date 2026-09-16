"""Alpha Core v2 — the engine. One candidate → exactly one terminal outcome.

Lifecycle: FIND → DECIDE → RANK → RISK → ACCOUNT → POSITION → SIZE → ORDER
→ CONFIRM → RECONCILE.

Broker-authoritative throughout: positions, account and fills come from the
broker, never from the SQLite store. The pre-ORDER position check is also the
double-submission guard — if Legacy (or a prior v2 order) already opened the
symbol, the broker reports it held and v2 BLOCKS as a duplicate.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from services.alpha_core_v2.broker import BrokerPort
from services.alpha_core_v2.config import Config
from services.alpha_core_v2.contracts import (
    AccountState, Candidate, CycleResult, Outcome, Receipt, Stage, new_id,
)
from services.alpha_core_v2.discovery import (
    DataSource, MarketDataPoolSource, discover,
)
from services.alpha_core_v2.receipts import ReceiptStore
from services.alpha_core_v2.sizing import plan_size

logger = logging.getLogger(__name__)


class CoreV2Engine:
    def __init__(self, broker: BrokerPort, store: ReceiptStore, config: Config,
                 source: Optional[DataSource] = None):
        self.broker = broker
        self.store = store
        self.config = config
        self.source = source or MarketDataPoolSource()

    # ── one terminal per candidate ──────────────────────────────────
    def _receipt(self, cycle_id: str, cand: Candidate, outcome: Outcome,
                 stage: Stage, reason: str, **extra) -> Receipt:
        r = Receipt(
            receipt_id=new_id("rcpt"), cycle_id=cycle_id, symbol=cand.symbol,
            created_ns=time.time_ns(), outcome=outcome, stage_reached=stage,
            reason=reason, pattern=cand.pattern, score=cand.score,
            confidence=cand.confidence, mark=cand.mark, **extra,
        )
        self.store.save(r)
        return r

    async def _process(self, cycle_id: str, cand: Candidate,
                       account: AccountState, *, live: bool) -> Receipt:
        cfg = self.config

        # DECIDE — conviction floor.
        if cand.confidence < cfg.confidence_floor:
            return self._receipt(cycle_id, cand, Outcome.BLOCKED, Stage.DECIDE,
                                  f"below_confidence_floor:{cand.confidence:.2f}<"
                                  f"{cfg.confidence_floor:.2f}")
        # ACCOUNT — broker truth (fetched once per cycle).
        if not account.ok:
            return self._receipt(cycle_id, cand, Outcome.FAILED, Stage.ACCOUNT,
                                  f"account_unavailable:{account.error}")

        # POSITION — broker authoritative. Fail closed on unknown state.
        try:
            positions = self.broker.get_positions()
        except Exception as exc:  # noqa: BLE001
            return self._receipt(cycle_id, cand, Outcome.FAILED, Stage.POSITION,
                                  f"broker_position_unknown:{str(exc)[:120]}",
                                  broker_held=None)
        held = next((p for p in positions if p.symbol == cand.symbol and p.qty > 0),
                    None)
        # Phantom reconcile: v2's store thinks it's open but broker is flat —
        # correct the record, never let it block (SQLite is not authority).
        reconciled = False
        if held is None and cand.symbol in self.store.open_position_symbols():
            n = self.store.mark_reconciled_flat(cand.symbol)
            reconciled = n > 0
            if reconciled:
                logger.info("[core-v2] %s phantom store row reconciled (broker flat)",
                            cand.symbol)
        if held is not None:
            return self._receipt(cycle_id, cand, Outcome.BLOCKED, Stage.POSITION,
                                  "duplicate_position", broker_held=True,
                                  broker_qty=held.qty, reconciled_phantom=reconciled)

        # RISK — max concurrent positions (broker-truth count).
        if len(positions) >= cfg.max_positions:
            return self._receipt(cycle_id, cand, Outcome.BLOCKED, Stage.RISK,
                                  f"max_positions:{len(positions)}>={cfg.max_positions}",
                                  broker_held=False, reconciled_phantom=reconciled)

        # SIZE — account-aware, provenance recorded.
        plan = plan_size(
            desired_notional=cfg.desired_notional, equity=account.equity,
            buying_power=account.buying_power, alloc_pct=cfg.alloc_pct,
            cash_reserve=cfg.cash_reserve, mark=cand.mark, min_trade=cfg.min_trade,
        )
        if plan.quantity <= 0:
            return self._receipt(cycle_id, cand, Outcome.BLOCKED, Stage.SIZE,
                                  "below_minimum_trade_size", broker_held=False,
                                  equity=account.equity, buying_power=account.buying_power,
                                  reconciled_phantom=reconciled, sizing=plan.to_dict())

        # Flag-gated: below the canary flag we run the FULL pipeline and record
        # exactly what we WOULD trade, but never submit.
        if not live:
            return self._receipt(cycle_id, cand, Outcome.BLOCKED, Stage.ORDER,
                                  "core_v2_disabled", broker_held=False,
                                  equity=account.equity, buying_power=account.buying_power,
                                  reconciled_phantom=reconciled, sizing=plan.to_dict(),
                                  requested_qty=plan.quantity)

        # ORDER — submit fractional.
        result = self.broker.submit(cand.symbol, plan.quantity, "buy")
        if not result.ok:
            return self._receipt(cycle_id, cand, Outcome.FAILED, Stage.ORDER,
                                  result.error or "order_failed", broker_held=False,
                                  equity=account.equity, buying_power=account.buying_power,
                                  reconciled_phantom=reconciled, sizing=plan.to_dict(),
                                  requested_qty=plan.quantity,
                                  order_id=result.order_id, order_status=result.status)

        # CONFIRM + RECONCILE — broker result is truth.
        filled = result.status in ("filled", "partially_filled")
        broker_confirmed = filled and result.filled_qty > 0
        position_status = "open" if filled else "pending"
        return self._receipt(
            cycle_id, cand, Outcome.TRADED, Stage.RECONCILE,
            "filled" if filled else "accepted_pending_fill",
            broker_held=False, equity=account.equity,
            buying_power=account.buying_power, reconciled_phantom=reconciled,
            sizing=plan.to_dict(), requested_qty=plan.quantity,
            order_id=result.order_id, order_status=result.status,
            filled_qty=result.filled_qty, fill_price=result.fill_price,
            broker_confirmed=broker_confirmed, position_status=position_status,
        )

    async def run_cycle(self, *, live: Optional[bool] = None) -> CycleResult:
        cycle_id = new_id("cyc")
        if live is None:
            live = self.config.enabled
        candidates = await discover(self.config, self.source)
        account = self.broker.get_account()
        receipts = []
        for cand in candidates:
            try:
                r = await self._process(cycle_id, cand, account, live=live)
            except Exception as exc:  # noqa: BLE001
                # Even an unexpected crash yields a terminal FAILED receipt —
                # a candidate can never vanish into an unaccounted state.
                logger.warning("[core-v2] %s processing crashed: %s",
                               cand.symbol, exc, exc_info=True)
                r = self._receipt(cycle_id, cand, Outcome.FAILED, Stage.RECONCILE,
                                  f"engine_exception:{str(exc)[:120]}")
            receipts.append(r)
        result = CycleResult(
            cycle_id=cycle_id, candidates_in=len(candidates),
            traded=sum(1 for r in receipts if r.outcome is Outcome.TRADED),
            blocked=sum(1 for r in receipts if r.outcome is Outcome.BLOCKED),
            failed=sum(1 for r in receipts if r.outcome is Outcome.FAILED),
            receipts=receipts,
        )
        if not result.balanced:
            logger.error(
                "[core-v2] ACCOUNTING VIOLATION cycle=%s in=%d traded=%d "
                "blocked=%d failed=%d", cycle_id, result.candidates_in,
                result.traded, result.blocked, result.failed,
            )
        logger.info("[core-v2] cycle=%s in=%d traded=%d blocked=%d failed=%d live=%s",
                    cycle_id, result.candidates_in, result.traded,
                    result.blocked, result.failed, live)
        return result

    async def reconcile_outstanding(self) -> dict:
        """Restart-safe: finalize TRADED receipts whose fill wasn't confirmed
        by re-asking the broker for the order + current positions."""
        pending = self.store.outstanding_orders()
        try:
            held = {p.symbol for p in self.broker.get_positions()}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": f"positions_unavailable:{str(exc)[:120]}"}
        finalized = 0
        for d in pending:
            oid = d.get("order_id")
            if not oid:
                continue
            res = self.broker.get_order(oid)
            if res.status in ("filled", "partially_filled") and res.filled_qty > 0:
                self.store.update_confirmation(
                    d["receipt_id"], broker_confirmed=True, order_status=res.status,
                    filled_qty=res.filled_qty, fill_price=res.fill_price,
                    position_status="open" if d["symbol"] in held else "reconciled_flat",
                )
                finalized += 1
            elif res.status == "rejected":
                self.store.update_confirmation(
                    d["receipt_id"], broker_confirmed=False, order_status="rejected",
                    filled_qty=0.0, fill_price=0.0, position_status="reconciled_flat",
                )
                finalized += 1
        return {"ok": True, "pending": len(pending), "finalized": finalized}
