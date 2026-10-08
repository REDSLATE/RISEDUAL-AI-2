"""Alpha Core v2 — the engine. One candidate → exactly one terminal outcome.

Lifecycle: FIND → DECIDE → RANK → RISK → ACCOUNT → [lock] IDEMPOTENCY →
POSITION → RISK(positions) → QUOTE → SIZE → ORDER → [/lock] CONFIRM →
RECEIPT → RECONCILE.

Broker-authoritative throughout: account, positions, the execution quote and
fills all come from the broker, never from the SQLite store. The pre-ORDER
position check + per-symbol lock + in-flight idempotency key together prevent
double submission (incl. against Legacy, which the broker reports as held).

Two broker facts are kept DISTINCT on the receipt:
  * order_acknowledged / broker_reported_fill_qty  (the order ACK / fill)
  * position_reconciled / reconciled_position_qty  (verified account position)
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import replace
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
                 source: Optional[DataSource] = None, sovereign_gate=None,
                 sovereign_enforce: bool = False):
        self.broker = broker
        self.store = store
        self.config = config
        self.source = source or MarketDataPoolSource()
        # Optional advisory Sovereign veto. Injected by the worker only; the
        # manual /run-cycle route builds the engine WITHOUT a gate so existing
        # manual cycles are unaffected. The gate can only VETO (never submit).
        self.sovereign_gate = sovereign_gate
        self.sovereign_enforce = sovereign_enforce
        # Per-symbol execution lock + in-flight idempotency key. Together
        # they guarantee two candidates for the same symbol arriving nearly
        # simultaneously produce AT MOST ONE broker submission — the lock
        # alone can't, because both workers could read held=False before
        # either order becomes a position.
        self._symbol_locks: dict[str, asyncio.Lock] = {}
        self._inflight: dict[str, str] = {}
        self._closing: set[str] = set()

    def _symbol_lock(self, symbol: str) -> asyncio.Lock:
        return self._symbol_locks.setdefault(symbol, asyncio.Lock())

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

        # SOVEREIGN — advisory veto (optional, worker-only). Advisory by
        # default: a HOLD is logged but does NOT stop the candidate. It only
        # blocks when sovereign_enforce AND this is a live cycle — i.e. an
        # operator has separately armed enforcement on a live autonomous run.
        if self.sovereign_gate is not None:
            try:
                proposal = await self.sovereign_gate(cand)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[core-v2:sovereign] %s unavailable: %s", cand.symbol, exc)
                if self.sovereign_enforce and live:
                    return self._receipt(cycle_id, cand, Outcome.FAILED, Stage.DECIDE,
                                         f"sovereign_unavailable:{str(exc)[:120]}")
            else:
                logger.info("[core-v2:sovereign] %s shadow=%s action=%s vetoes=%s",
                            cand.symbol, not (self.sovereign_enforce and live),
                            proposal.action, proposal.vetoes)
                if self.sovereign_enforce and live and (proposal.action != "BUY" or proposal.vetoes):
                    return self._receipt(cycle_id, cand, Outcome.BLOCKED, Stage.DECIDE,
                                         "sovereign_hold:" + ",".join(proposal.vetoes)[:180])
        # ACCOUNT — broker truth (fetched once per cycle, checked before lock).
        if not account.ok:
            return self._receipt(cycle_id, cand, Outcome.FAILED, Stage.ACCOUNT,
                                  f"account_unavailable:{account.error}")

        # ── Critical section: idempotency → position → risk → quote → size
        # → submit, serialized per symbol. ──────────────────────────────
        async with self._symbol_lock(cand.symbol):
            # IDEMPOTENCY — an outstanding successful submit this process
            # (broker may not yet report the position) blocks a second one.
            if cand.symbol in self._inflight or self._pending(cand.symbol):
                return self._receipt(cycle_id, cand, Outcome.BLOCKED, Stage.ORDER,
                                     "concurrent_duplicate",
                                     order_id=self._inflight.get(cand.symbol))

            try:
                if self.broker.get_open_orders(cand.symbol):
                    return self._receipt(cycle_id, cand, Outcome.BLOCKED, Stage.ORDER,
                                         "broker_order_in_flight")
            except Exception as exc:
                return self._receipt(cycle_id, cand, Outcome.FAILED, Stage.ORDER,
                                     f"broker_orders_unknown:{str(exc)[:120]}")

            # POSITION — broker authoritative. Fail closed on unknown state.
            try:
                positions = self.broker.get_positions()
            except Exception as exc:  # noqa: BLE001
                return self._receipt(cycle_id, cand, Outcome.FAILED, Stage.POSITION,
                                     f"broker_position_unknown:{str(exc)[:120]}",
                                     broker_held=None)
            held = next((p for p in positions
                         if p.symbol == cand.symbol and p.qty > 0), None)
            # Phantom reconcile: store thinks it's open but broker is flat —
            # correct the record, never let it block (SQLite is not authority).
            reconciled = False
            if held is None and cand.symbol in self.store.open_position_symbols():
                reconciled = self.store.mark_reconciled_flat(cand.symbol) > 0
                if reconciled:
                    logger.info("[core-v2] %s phantom store row reconciled (broker flat)",
                                cand.symbol)
            if held is not None:
                return self._receipt(cycle_id, cand, Outcome.BLOCKED, Stage.POSITION,
                                     "duplicate_position", broker_held=True,
                                     broker_qty=held.qty, reconciled_phantom=reconciled)

            # NOTE (2026-06): there is NO position-count limit here by design.
            # Alpha's portfolio rule is account-percentage sizing — existing
            # positions constrain the next trade ONLY through remaining buying
            # power (checked in SIZE below), never through a count of names.
            # The former ALPHA_V2_MAX_CONCURRENT_POSITIONS gate was a regression
            # from that model and has been removed.

            # QUOTE — fresh execution price from the broker (source of truth).
            # Discovery mark answered "interesting?"; this answers "what price
            # am I about to buy at?" — a different data contract.
            quote = self.broker.get_execution_quote(cand.symbol)
            if quote is None:
                return self._receipt(cycle_id, cand, Outcome.FAILED, Stage.QUOTE,
                                     "execution_quote_unavailable", broker_held=False,
                                     reconciled_phantom=reconciled)
            age = quote.age_seconds()
            exec_price = float(quote.price)
            age_known = age != float("inf")
            qmeta = dict(execution_price=exec_price,
                         execution_quote_source=quote.source,
                         execution_quote_age_s=(round(age, 3) if age_known else None),
                         broker_held=False, reconciled_phantom=reconciled,
                         equity=account.equity, buying_power=account.buying_power)
            if not age_known:
                return self._receipt(cycle_id, cand, Outcome.BLOCKED, Stage.QUOTE,
                                     "stale_execution_quote:missing_timestamp", **qmeta)
            if age > cfg.quote_max_age_s:
                return self._receipt(cycle_id, cand, Outcome.BLOCKED, Stage.QUOTE,
                                     f"stale_execution_quote:{age:.1f}s>"
                                     f"{cfg.quote_max_age_s:.1f}s", **qmeta)

            # SIZE — account-percentage: alloc_pct of AVAILABLE buying power,
            # recalculated from the EXECUTION mark. Bounded by the absolute
            # per-trade risk cap and affordability/reserve.
            plan = plan_size(
                buying_power=account.buying_power, alloc_pct=cfg.alloc_pct,
                per_trade_cap=cfg.desired_notional, cash_reserve=cfg.cash_reserve,
                mark=exec_price, min_trade=cfg.min_trade,
            )
            if plan.quantity <= 0:
                return self._receipt(cycle_id, cand, Outcome.BLOCKED, Stage.SIZE,
                                     "below_minimum_trade_size", sizing=plan.to_dict(),
                                     **qmeta)

            # Affordability invariant: estimated submission cost must never
            # exceed spendable buying power (guards the $1-min bump edge).
            spendable_bp = max(0.0, account.buying_power - cfg.cash_reserve)
            estimated_cost = plan.quantity * exec_price
            if estimated_cost > spendable_bp + 1e-6:
                return self._receipt(cycle_id, cand, Outcome.BLOCKED, Stage.SIZE,
                                     f"notional_exceeds_buying_power:"
                                     f"{estimated_cost:.2f}>{spendable_bp:.2f}",
                                     sizing=plan.to_dict(), **qmeta)

            # Flag-gated: below the canary flag run the FULL pipeline and
            # record exactly what we WOULD trade, but never submit.
            if not live:
                return self._receipt(cycle_id, cand, Outcome.BLOCKED, Stage.ORDER,
                                     "core_v2_disabled", sizing=plan.to_dict(),
                                     requested_qty=plan.quantity, **qmeta)

            # Commit the UUID before HTTP; a lost ACK must not permit a retry.
            return self._submit_reserved(
                cycle_id, cand, qty=plan.quantity, side="buy", action="open",
                sizing=plan.to_dict(), **qmeta,
            )

    def _pending(self, symbol: str) -> bool:
        return any(d["symbol"] == symbol for d in self.store.outstanding_orders())

    def _submit_reserved(self, cycle_id: str, cand: Candidate, *, qty: float,
                         side: str, action: str, **meta) -> Receipt:
        # The persisted UUID is the actual Public orderId, including on timeout.
        r = Receipt(new_id("rcpt"), cycle_id, cand.symbol, time.time_ns(),
                    Outcome.FAILED, Stage.ORDER, reason="submission_unconfirmed",
                    action=action, pattern=cand.pattern, score=cand.score,
                    confidence=cand.confidence, mark=cand.mark,
                    order_id=str(uuid.uuid4()), requested_qty=qty,
                    order_status="submitting",
                    position_status="closing" if action == "close" else "pending",
                    **meta)
        if not self.store.reserve_order(r):
            return self._receipt(cycle_id, cand, Outcome.BLOCKED, Stage.ORDER,
                                 "close_in_flight" if action == "close" else "concurrent_duplicate",
                                 action=action)
        try:
            result = self.broker.submit(cand.symbol, qty, side,
                                        client_order_id=r.order_id, close=action == "close")
        except Exception as exc:
            r.reason = f"submission_unknown:{str(exc)[:120]}"
            self.store.save(r)
            return r
        # Never discard our reserved UUID when an ACK is absent.
        r.order_id = result.order_id or r.order_id
        r.order_status = result.status or "unknown"
        r.broker_reported_fill_qty = result.filled_qty
        r.fill_price = result.fill_price
        r.order_acknowledged = result.ok
        r.outcome = Outcome.TRADED if result.ok else Outcome.FAILED
        r.stage_reached = Stage.CONFIRM if result.ok else Stage.ORDER
        r.reason = ("close_accepted_pending" if action == "close" else "accepted_pending_fill") if result.ok else (result.error or "submission_unknown")
        if action == "close":
            r.reason += ":" + cand.reason
        if result.status in ("rejected", "cancelled", "expired", "failed"):
            r.position_reconciled = True
            r.position_status = "open" if meta.get("broker_held") else "reconciled_flat"
        elif action == "close":
            self._closing.add(cand.symbol)
        else:
            self._inflight[cand.symbol] = r.order_id
        self.store.save(r)
        return r

    async def close_position(self, symbol: str, *, reason: str = "operator",
                             expected_side: str = "long") -> Receipt:
        """Close existing exposure only; ACK and partial fill remain pending.

        No entry confidence or buying-power gate applies. A durable reservation
        and broker open-order check prevent reversal or double close.
        """
        symbol = (symbol or "").upper()
        cid = new_id("close")
        cand = Candidate(symbol=symbol, mark=0.0, score=0.0, pattern="",
                         confidence=0.0, reason=reason)
        async with self._symbol_lock(symbol):
            if symbol in self._closing or self._pending(symbol):
                return self._receipt(cid, cand, Outcome.BLOCKED, Stage.ORDER,
                                     "close_in_flight", action="close")
            try:
                if self.broker.get_open_orders(symbol):
                    return self._receipt(cid, cand, Outcome.BLOCKED, Stage.ORDER,
                                         "broker_order_in_flight", action="close")
                positions = self.broker.get_positions()
            except Exception as exc:
                return self._receipt(cid, cand, Outcome.FAILED, Stage.POSITION,
                                     f"broker_position_unknown:{str(exc)[:120]}",
                                     action="close", broker_held=None)
            held = next((p for p in positions if p.symbol == symbol and p.qty > 0), None)
            if held is None:
                self.store.mark_reconciled_flat(symbol)
                return self._receipt(cid, cand, Outcome.BLOCKED, Stage.POSITION,
                                     "already_flat", action="close", broker_held=False,
                                     position_status="reconciled_flat")
            if expected_side not in {"long": "sell", "short": "buy"} or held.side != expected_side:
                return self._receipt(cid, cand, Outcome.BLOCKED, Stage.POSITION,
                                     "position_side_mismatch", action="close")
            return self._submit_reserved(
                cid, cand, qty=held.qty, side="sell" if expected_side == "long" else "buy",
                action="close", broker_held=True, broker_qty=held.qty,
            )

    async def close_all_positions(self, *, reason: str = "operator") -> dict:
        """Flatten every position the broker reports. Returns per-symbol outcomes."""
        try:
            positions = self.broker.get_positions()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": f"positions_unavailable:{str(exc)[:120]}"}
        out = []
        for p in positions:
            r = await self.close_position(p.symbol, reason=reason)
            out.append({"symbol": p.symbol, "outcome": r.outcome.value,
                        "reason": r.reason})
        return {"ok": True, "closed": out}

    async def run_cycle(self, *, live: Optional[bool] = None) -> CycleResult:
        cycle_id = new_id("cyc")
        if live is None:
            live = self.config.enabled
        candidates = await discover(self.config, self.source)
        account = self.broker.get_account()
        receipts = []
        # Running buying power: each accepted order this cycle commits capital
        # that the broker won't reflect until settlement, so decrement locally.
        # This makes "remaining capital is the constraint" hold ACROSS the cycle
        # (each successive 3% allocation sizes against what's actually left) and
        # prevents a cycle from over-committing now that there is no count cap.
        remaining_bp = account.buying_power if account.ok else 0.0
        for cand in candidates:
            try:
                cycle_account = replace(account, buying_power=max(0.0, remaining_bp))
                r = await self._process(cycle_id, cand, cycle_account, live=live)
            except Exception as exc:  # noqa: BLE001
                # Even an unexpected crash yields a terminal FAILED receipt —
                # a candidate can never vanish into an unaccounted state.
                logger.warning("[core-v2] %s processing crashed: %s",
                               cand.symbol, exc, exc_info=True)
                r = self._receipt(cycle_id, cand, Outcome.FAILED, Stage.CONFIRM,
                                  f"engine_exception:{str(exc)[:120]}")
            receipts.append(r)
            if r.outcome is Outcome.TRADED:
                spent = float((r.sizing or {}).get("final_notional") or 0.0)
                remaining_bp = max(0.0, remaining_bp - spent)
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
        """Poll cumulative broker fills; partial/unknown orders retain locks.

        Orders become terminal first, then the position snapshot must reflect
        the result. Rejected exits never fabricate a flat position.
        """
        pending = self.store.outstanding_orders()
        finalized = 0
        for d in pending:
            sym = d["symbol"]
            async with self._symbol_lock(sym):
                try:
                    res = self.broker.get_order(d["order_id"])
                    positions = self.broker.get_positions()  # AFTER order status
                except Exception as exc:
                    return {"ok": False, "reason": f"reconciliation_unknown:{str(exc)[:120]}"}
                if res is None:
                    continue
                pos_qty = sum(p.qty for p in positions if p.symbol == sym)
                closing = d.get("action") == "close"
                terminal = res.status in ("filled", "rejected", "cancelled", "expired", "failed")
                # Full fill plus a lagging holdings snapshot is not reconciled.
                consistent = (pos_qty <= 1e-9 if closing else pos_qty > 0) if res.status == "filled" else True
                done = terminal and consistent
                state = ("open" if pos_qty > 0 else "reconciled_flat") if done else ("closing" if closing else "pending")
                self.store.update_reconciliation(
                    d["receipt_id"], position_reconciled=done,
                    reconciled_position_qty=pos_qty, order_status=res.status or "unknown",
                    position_status=state, filled_qty=res.filled_qty, fill_price=res.fill_price,
                )
                if done:
                    if closing and pos_qty <= 1e-9:
                        self.store.mark_reconciled_flat(sym)
                    self._inflight.pop(sym, None)
                    self._closing.discard(sym)
                    finalized += 1
        return {"ok": True, "pending": len(pending), "finalized": finalized}
