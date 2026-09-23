"""Alpha Core v2 — READ-ONLY arm preflight (2026-06).

Alpha is standalone; MC is a dead dependency. Before ALPHA_CORE_V2=1 is
flipped in prod, this module verifies the FULL live-execution surface against
the real Public account WITHOUT submitting a single order.

It exercises exactly the same broker port + engine interlocks the live cycle
uses, so a green result means the plumbing that would fire an order is proven
end-to-end minus the final submit.

Returns one verdict: ``READY_TO_ARM`` or ``BLOCKED`` + explicit reasons.
Never submits an order. Never mutates broker state. The only local write it
avoids too — reconciliation is a dry comparison, not a store update.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

_FALSEY = ("0", "false", "no", "off")


async def run_preflight(db: Any) -> dict:
    from services.alpha_core_v2.broker import PublicBroker
    from services.alpha_core_v2.config import Config
    from services.alpha_core_v2.engine import CoreV2Engine
    from services.alpha_core_v2.receipts import ReceiptStore

    cfg = Config.load()
    checks: list[dict] = []
    blockers: list[str] = []
    warnings: list[str] = []
    confirms: list[dict] = []

    def add(name: str, status: str, detail: Any, **extra) -> dict:
        rec = {"check": name, "status": status, "detail": detail, **extra}
        checks.append(rec)
        if status == "fail":
            blockers.append(f"{name}: {detail}")
        elif status == "warn":
            warnings.append(f"{name}: {detail}")
        elif status == "confirm":
            confirms.append(rec)
        return rec

    def verdict() -> dict:
        return {
            "engine": "alpha_core_v2",
            "verdict": "READY_TO_ARM" if not blockers else "BLOCKED",
            "blocking_reasons": blockers,
            "warnings": warnings,
            "operator_confirmations_required": confirms,
            "checks": checks,
            "note": "READ-ONLY preflight — NO order was submitted, no broker "
                    "state mutated.",
            "arm_instruction": (
                "Set ALPHA_CORE_V2=1 (this forces Legacy OFF via interlock) "
                "then POST /api/admin/alpha-v2/run-cycle?live=true"
            ),
        }

    # ── 1. credentials / authentication ────────────────────────────────
    broker: Optional[PublicBroker] = None
    try:
        broker = await PublicBroker.from_db(db)
    except Exception as exc:  # noqa: BLE001
        add("credentials_authentication", "fail",
            f"PublicBroker.from_db raised: {str(exc)[:180]}")
        return verdict()
    if broker is None:
        add("credentials_authentication", "fail",
            "Public broker not connected — no creds via env "
            "(PUBLIC_API_KEY/PUBLIC_ACCOUNT_ID), no {broker_id:public,"
            "status:connected} row, and no active /api/broker/connect record")
        return verdict()

    acct = broker.get_account()
    if not acct.ok:
        add("credentials_authentication", "fail",
            f"secret→JWT/account probe failed: {acct.error}")
        return verdict()
    add("credentials_authentication", "pass",
        "secret→JWT exchange + authenticated account fetch OK")

    # ── 2. correct account (operator must eyeball) ──────────────────────
    acct_num = "(unknown)"
    try:
        raw = broker._c.get_account() or {}
        acct_num = str(raw.get("account_number") or raw.get("accountId") or "(unknown)")
    except Exception as exc:  # noqa: BLE001
        logger.debug("[preflight] raw account fetch failed: %s", exc)
    add("correct_account", "confirm",
        f"Public account_number={acct_num} — operator MUST confirm this is the "
        "intended PRODUCTION account before arming",
        account_number=acct_num)

    # ── 3. account / buying-power retrieval ─────────────────────────────
    if acct.buying_power >= 0 and acct.equity >= 0:
        add("account_buying_power_retrieval", "pass",
            f"equity=${acct.equity:.2f} buying_power=${acct.buying_power:.2f} "
            f"cash=${acct.cash:.2f}",
            equity=acct.equity, buying_power=acct.buying_power, cash=acct.cash)
    else:
        add("account_buying_power_retrieval", "fail",
            f"nonsensical account figures equity={acct.equity} bp={acct.buying_power}")

    # ── 4. positions retrieval ──────────────────────────────────────────
    positions = None
    try:
        positions = broker.get_positions()
        add("positions_retrieval", "pass", f"{len(positions)} live position(s)",
            positions=[{"symbol": p.symbol, "qty": p.qty, "side": p.side}
                       for p in positions])
    except Exception as exc:  # noqa: BLE001
        add("positions_retrieval", "fail",
            f"get_positions raised (FAIL-CLOSED — engine never opens blind): "
            f"{str(exc)[:160]}")

    # ── 5 + 6. live quote retrieval + freshness ─────────────────────────
    probe_sym = cfg.universe[0] if cfg.universe else "SPY"
    quote = broker.get_execution_quote(probe_sym)
    if quote is None:
        add("live_quote_retrieval", "fail",
            f"no usable execution quote for {probe_sym} (missing/zero/unparseable)")
    else:
        add("live_quote_retrieval", "pass",
            f"{probe_sym} price=${float(quote.price):.4f} source={quote.source}",
            symbol=probe_sym, price=float(quote.price), source=quote.source)
        age = quote.age_seconds()
        if age == float("inf"):
            add("quote_timestamp_freshness", "warn",
                "execution quote had no parseable timestamp — the engine BLOCKS "
                "missing-timestamp quotes by design (freshness unknown)", age_s=None)
        elif age <= cfg.quote_max_age_s:
            add("quote_timestamp_freshness", "pass",
                f"age={age:.1f}s <= max {cfg.quote_max_age_s:.1f}s", age_s=round(age, 2))
        else:
            add("quote_timestamp_freshness", "warn",
                f"age={age:.1f}s > max {cfg.quote_max_age_s:.1f}s — market likely "
                f"closed. At trade time the engine BLOCKS stale quotes by design, "
                f"so this self-heals in RTH.", age_s=round(age, 2))

    # ── 7. broker position reconciliation (DRY compare, no writes) ──────
    store = ReceiptStore(cfg.db_path)
    store_open = store.open_position_symbols()
    if positions is not None:
        broker_syms = {p.symbol for p in positions if p.qty > 0}
        phantom = sorted(store_open - broker_syms)
        add("broker_position_reconciliation",
            "pass" if not phantom else "warn",
            f"store_open={sorted(store_open)} broker_held={sorted(broker_syms)} "
            f"phantom_store_rows={phantom} "
            f"(engine auto-reconciles phantoms to flat at cycle time)",
            phantom=phantom)
    else:
        add("broker_position_reconciliation", "fail",
            "cannot reconcile — broker positions unavailable")

    # get_order gap — affects fact#2 finalization only, not duplicate safety
    if hasattr(broker._c, "get_order"):
        add("reconcile_order_status_api", "pass",
            "PublicTradingService.get_order present")
    else:
        add("reconcile_order_status_api", "warn",
            "PublicTradingService has no get_order() — reconcile_outstanding() "
            "cannot finalize position via order-status lookup. Duplicate safety "
            "is UNAFFECTED (positions are re-fetched broker-authoritatively each "
            "cycle). Recommend adding get_order() for clean fact#2 bookkeeping.")

    # ── 8. order endpoint availability (READ-ONLY probe, NO submit) ─────
    try:
        recent = broker._c.get_orders(status="all", limit=1)
        add("order_endpoint_availability", "pass",
            f"orders endpoint reachable + authenticated (listed {len(recent)} "
            f"row(s)); submit path present — NO order placed",
            listed=len(recent))
    except Exception as exc:  # noqa: BLE001
        add("order_endpoint_availability", "warn",
            f"get_orders read-probe failed: {str(exc)[:140]} — place_order path "
            f"exists but could not be verified without submitting")

    # ── 9. idempotency / duplicate-position protection ──────────────────
    engine = CoreV2Engine(broker, store, cfg)
    dup_ok = all(hasattr(engine, a) for a in ("_symbol_locks", "_inflight", "_closing"))
    add("idempotency_duplicate_protection", "pass" if dup_ok else "fail",
        f"per-symbol asyncio lock + in-flight idempotency key + broker-authoritative "
        f"POSITION duplicate guard present; current inflight={dict(engine._inflight)}; "
        f"store dedupes by receipt_id (INSERT OR REPLACE)")

    # ── 10. hardware kill switch ────────────────────────────────────────
    try:
        from services import alpha_hardware_kill_switch as _hw
        tripped, reason = _hw.check()
        add("hardware_kill_switch", "fail" if tripped else "pass",
            f"tripped={tripped} reason={reason or 'none'}")
    except Exception as exc:  # noqa: BLE001
        add("hardware_kill_switch", "fail",
            f"kill-switch subsystem error (FAIL-CLOSED): {str(exc)[:140]}")

    # ── 11. execution interlocks ────────────────────────────────────────
    try:
        from services.public_equity_live_executor import _live_exec_enabled
        legacy_live = _live_exec_enabled()
    except Exception as exc:  # noqa: BLE001
        legacy_live = None
        logger.debug("[preflight] legacy flag probe failed: %s", exc)
    v2_armed = cfg.enabled
    pub_flag = os.environ.get("RISEDUAL_PUBLIC_LIVE_EXEC", "(unset)")

    if v2_armed and legacy_live:
        add("mutual_exclusion_legacy_vs_v2", "fail",
            "FORBIDDEN: both Legacy live-exec AND Core v2 are armed simultaneously")
    else:
        add("mutual_exclusion_legacy_vs_v2", "pass",
            f"v2_armed={v2_armed} legacy_live_exec_enabled={legacy_live} "
            f"(RISEDUAL_PUBLIC_LIVE_EXEC={pub_flag}); interlock forces Legacy OFF "
            f"the moment ALPHA_CORE_V2=1")

    add("core_v2_arm_flag", "info",
        f"ALPHA_CORE_V2 currently {'ARMED' if v2_armed else 'NOT armed'} "
        f"— arming after a READY verdict is the intended next step")

    add("sizing_config", "pass",
        f"alloc_pct={cfg.alloc_pct} (of AVAILABLE buying power) "
        f"per_trade_risk_cap=${cfg.desired_notional} cash_reserve=${cfg.cash_reserve} "
        f"min_trade=${cfg.min_trade} confidence_floor={cfg.confidence_floor} "
        f"quote_max_age_s={cfg.quote_max_age_s}",
        alloc_pct=cfg.alloc_pct, per_trade_risk_cap=cfg.desired_notional,
        cash_reserve=cfg.cash_reserve, min_trade=cfg.min_trade,
        confidence_floor=cfg.confidence_floor)

    add("no_position_count_limit", "pass",
        f"{len(positions or [])} position(s) held — this is NOT a limit. Alpha "
        f"sizes account-percentage; existing positions constrain the next trade "
        f"ONLY through remaining buying power. ALPHA_V2_MAX_CONCURRENT_POSITIONS "
        f"has been removed from the entry decision path.")

    emit_raw = (os.environ.get("RISEDUAL_EMIT_INTENTS_TO_MC") or "").strip().lower()
    emit_off = emit_raw in _FALSEY
    add("mc_intent_route_severed", "pass" if emit_off else "warn",
        f"RISEDUAL_EMIT_INTENTS_TO_MC={emit_raw or '(unset)'} "
        f"standalone={os.environ.get('RISEDUAL_STANDALONE_MODE', '(unset)')} — "
        f"{'MC intent route SEVERED' if emit_off else 'still enabled; set to 0 to sever (and set it in the prod deploy panel)'}")

    # ── ORDER-READY simulation (NO submit) — prove an otherwise-valid
    # candidate can progress ACCOUNT → POSITION → SIZE → ORDER_READY using
    # live account facts, placing no order. ────────────────────────────
    from services.alpha_core_v2.sizing import plan_size
    held_syms = {p.symbol for p in positions} if positions is not None else set()
    probe = next((s for s in cfg.universe if s not in held_syms), None)
    chain: dict = {"ACCOUNT": None, "POSITION": None, "SIZE": None, "ORDER_READY": None}
    if not acct.ok:
        add("order_ready_simulation", "fail", "ACCOUNT stage unavailable", chain=chain)
    elif probe is None:
        add("order_ready_simulation", "warn",
            "every configured universe symbol is already held — cannot demo a "
            "fresh entry (a duplicate-position/buying-power outcome, NOT a count cap)",
            chain=chain)
    else:
        chain["ACCOUNT"] = (f"equity=${acct.equity:.2f} "
                            f"buying_power=${acct.buying_power:.2f}")
        chain["POSITION"] = f"{probe} not held → eligible (no count gate)"
        pq = broker.get_execution_quote(probe)
        if pq is None:
            add("order_ready_simulation", "warn",
                f"{probe}: no execution quote right now (market likely closed) — "
                f"chain proven through POSITION; SIZE/ORDER_READY need a live quote",
                chain=chain)
        else:
            px = float(pq.price)
            plan = plan_size(
                buying_power=acct.buying_power, alloc_pct=cfg.alloc_pct,
                per_trade_cap=cfg.desired_notional, cash_reserve=cfg.cash_reserve,
                mark=px, min_trade=cfg.min_trade,
            )
            chain["SIZE"] = plan.to_dict()
            spendable_bp = max(0.0, acct.buying_power - cfg.cash_reserve)
            est_cost = plan.quantity * px
            if plan.quantity > 0 and est_cost <= spendable_bp + 1e-6:
                chain["ORDER_READY"] = (
                    f"WOULD submit BUY {plan.quantity} {probe} @ ${px:.4f} "
                    f"= ${est_cost:.2f} — NO ORDER PLACED")
                add("order_ready_simulation", "pass",
                    f"{probe}: ACCOUNT → POSITION → SIZE → ORDER_READY. "
                    f"{cfg.alloc_pct:.0%} of ${acct.buying_power:.2f} = "
                    f"${plan.allocation_notional:.2f} → {plan.quantity} sh @ "
                    f"${px:.4f} = ${plan.final_notional:.2f}; "
                    f"remaining_buying_power=${plan.remaining_buying_power:.2f}. "
                    f"NO ORDER SUBMITTED.",
                    chain=chain)
            else:
                add("order_ready_simulation", "warn",
                    f"{probe}: sized to {plan.quantity} sh "
                    f"(${plan.final_notional:.2f}) — below broker minimum / "
                    f"unaffordable at this balance. BLOCK below_minimum_trade_size "
                    f"is the correct outcome (a capital constraint, not a count cap).",
                    chain=chain)

    return verdict()
