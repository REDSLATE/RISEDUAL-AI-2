"""MoomooBrokerAdapter — order submission over OpenD.

V1 constraints (per operator spec)
----------------------------------
* Live US equities, RTH only, one position at a time, tiny notional
* Options schema present, autonomous option orders **disabled**
* No fallback to another broker on rejection — rejection stays observable
* Unlock password NEVER stored in Mongo, source, or logs. Read transiently
  from ``os.environ["MOOMOO_TRADE_UNLOCK_PASSWORD"]`` at submit time only
  (this env var is populated by the runtime secret provider, not by the app)

Alpha never calls this adapter directly. Everything routes through
``services.broker_router.route(intent)`` which enforces the pipeline.

Broker comparison telemetry
---------------------------
Every submit records latency + fill metrics into the SQLite hot store
(``broker_comparison`` table). Compact rollups can then be surfaced
to Mission Control without duplicating high-frequency data into Mongo.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_trade_ctx: Any = None
_cached_acc_id: Optional[int] = None


def _host() -> str:
    return (os.environ.get("MOOMOO_OPEND_HOST") or "moomoo-opend").strip()


def _port() -> int:
    try:
        return int(os.environ.get("MOOMOO_OPEND_PORT") or "11111")
    except (TypeError, ValueError):
        return 11111


def _live_enabled() -> bool:
    return (os.environ.get("MOOMOO_LIVE_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")


def _options_enabled() -> bool:
    return (os.environ.get("MOOMOO_OPTIONS_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")


def _tiny_notional_cap_usd() -> float:
    try:
        return float(os.environ.get("MOOMOO_MAX_NOTIONAL_USD") or "50")
    except (TypeError, ValueError):
        return 50.0


def _acc_id() -> Optional[int]:
    """Return the configured MooMoo US securities account id.

    Only ``acc_id`` (int) is persisted — never the login/unlock credentials.
    Empty/missing → adapter cannot trade; broker_router keeps the trade
    path with Public.com.
    """
    global _cached_acc_id
    if _cached_acc_id is not None:
        return _cached_acc_id
    raw = (os.environ.get("MOOMOO_ACC_ID") or "").strip()
    if not raw:
        return None
    try:
        _cached_acc_id = int(raw)
    except (TypeError, ValueError):
        return None
    return _cached_acc_id


def _get_trade_ctx() -> Any:
    global _trade_ctx
    with _LOCK:
        if _trade_ctx is not None:
            return _trade_ctx
        try:
            from moomoo import OpenSecTradeContext, TrdMarket, SecurityFirm
            _trade_ctx = OpenSecTradeContext(
                filter_trdmarket=TrdMarket.US,
                host=_host(), port=_port(),
                security_firm=SecurityFirm.FUTUINC,
            )
            logger.info("[moomoo_broker] OpenD trade context opened")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[moomoo_broker] OpenD trade context failed: %s", exc)
            _trade_ctx = None
        return _trade_ctx


def close() -> None:
    global _trade_ctx
    with _LOCK:
        if _trade_ctx is None:
            return
        try:
            _trade_ctx.close()
        except Exception:  # noqa: BLE001
            pass
        _trade_ctx = None


# ─── read paths (no unlock required) ─────────────────────────────


def _to_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    return s if s.startswith("US.") else f"US.{s}"


def account_info() -> Optional[dict]:
    ctx = _get_trade_ctx()
    acc = _acc_id()
    if ctx is None or acc is None:
        return None
    try:
        from moomoo import RET_OK, TrdEnv, Currency
        ret, data = ctx.accinfo_query(trd_env=TrdEnv.REAL, acc_id=acc, currency=Currency.USD)
        if ret != RET_OK:
            return None
        rows = data.to_dict("records") if hasattr(data, "to_dict") else []
        return rows[0] if rows else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[moomoo_broker] account_info failed: %s", exc)
        return None


def positions() -> Optional[list[dict]]:
    ctx = _get_trade_ctx()
    acc = _acc_id()
    if ctx is None or acc is None:
        return None
    try:
        from moomoo import RET_OK, TrdEnv, TrdMarket, Currency
        ret, data = ctx.position_list_query(
            position_market=TrdMarket.US, trd_env=TrdEnv.REAL,
            acc_id=acc, currency=Currency.USD,
        )
        if ret != RET_OK:
            return None
        return data.to_dict("records") if hasattr(data, "to_dict") else []
    except Exception as exc:  # noqa: BLE001
        logger.debug("[moomoo_broker] positions failed: %s", exc)
        return None


def orders() -> Optional[list[dict]]:
    ctx = _get_trade_ctx()
    acc = _acc_id()
    if ctx is None or acc is None:
        return None
    try:
        from moomoo import RET_OK, TrdEnv, TrdMarket
        ret, data = ctx.order_list_query(order_market=TrdMarket.US,
                                          trd_env=TrdEnv.REAL, acc_id=acc)
        if ret != RET_OK:
            return None
        return data.to_dict("records") if hasattr(data, "to_dict") else []
    except Exception as exc:  # noqa: BLE001
        logger.debug("[moomoo_broker] orders failed: %s", exc)
        return None


def fills() -> Optional[list[dict]]:
    ctx = _get_trade_ctx()
    acc = _acc_id()
    if ctx is None or acc is None:
        return None
    try:
        from moomoo import RET_OK, TrdEnv, TrdMarket
        ret, data = ctx.deal_list_query(deal_market=TrdMarket.US,
                                         trd_env=TrdEnv.REAL, acc_id=acc)
        if ret != RET_OK:
            return None
        return data.to_dict("records") if hasattr(data, "to_dict") else []
    except Exception as exc:  # noqa: BLE001
        logger.debug("[moomoo_broker] fills failed: %s", exc)
        return None


# ─── write path (unlock required + telemetry) ────────────────────


@dataclass
class SubmitResult:
    ok: bool
    order_id: Optional[str]
    status: Optional[str]
    error: Optional[str]
    submit_latency_ms: int
    ack_latency_ms: int
    raw: Optional[dict] = None


def _one_position_open() -> bool:
    """V1 constraint: never submit while another Alpha position is open."""
    pos = positions()
    if not pos:
        return False
    return any(float(p.get("qty") or 0.0) > 0 for p in pos)


def submit_equity(*, symbol: str, side: str, qty: float, limit_price: float,
                   client_order_id: str) -> SubmitResult:
    """Submit a MooMoo US equity limit order.

    Enforces V1 caps (RTH, tiny notional, single position) BEFORE calling
    OpenD. All safety fails cleanly and observably — never silently
    hand off to another broker.
    """
    start = time.time_ns()
    if not _live_enabled():
        return SubmitResult(False, None, None, "moomoo_live_disabled", 0, 0)
    ctx = _get_trade_ctx()
    acc = _acc_id()
    if ctx is None or acc is None:
        return SubmitResult(False, None, None, "no_context_or_acc_id", 0, 0)
    if limit_price <= 0 or qty <= 0:
        return SubmitResult(False, None, None, "invalid_price_or_qty", 0, 0)
    notional = float(qty) * float(limit_price)
    cap = _tiny_notional_cap_usd()
    if notional > cap:
        return SubmitResult(False, None, None, f"notional_over_cap:{notional:.2f}>{cap:.2f}", 0, 0)
    if _one_position_open():
        return SubmitResult(False, None, None, "position_already_open", 0, 0)

    unlock_pwd = os.environ.get("MOOMOO_TRADE_UNLOCK_PASSWORD") or ""
    if not unlock_pwd:
        return SubmitResult(False, None, None, "unlock_password_missing", 0, 0)

    try:
        from moomoo import RET_OK, TrdEnv, TrdSide, OrderType, TimeInForce, Session
    except Exception:  # noqa: BLE001
        return SubmitResult(False, None, None, "sdk_import_failed", 0, 0)

    # Unlock transiently — never log the password or the derived MD5.
    try:
        ret_unlock, _msg = ctx.unlock_trade(password=unlock_pwd)
        if ret_unlock != RET_OK:
            return SubmitResult(False, None, None, "unlock_failed", 0, 0)
    except Exception:  # noqa: BLE001
        return SubmitResult(False, None, None, "unlock_exception", 0, 0)

    ack_start = time.time_ns()
    try:
        side_enum = TrdSide.BUY if side.upper() == "BUY" else TrdSide.SELL
        ret, data = ctx.place_order(
            price=float(limit_price),
            qty=float(qty),
            code=_to_symbol(symbol),
            trd_side=side_enum,
            order_type=OrderType.NORMAL,
            trd_env=TrdEnv.REAL,
            acc_id=acc,
            time_in_force=TimeInForce.DAY,
            session=Session.RTH,
            remark=client_order_id,  # never a secret
        )
    except Exception as exc:  # noqa: BLE001
        _safe_lock(ctx)
        return SubmitResult(False, None, None, f"place_order_exception:{exc.__class__.__name__}",
                             (time.time_ns() - start) // 1_000_000,
                             (time.time_ns() - ack_start) // 1_000_000)
    finally:
        _safe_lock(ctx)

    ack_ms = (time.time_ns() - ack_start) // 1_000_000
    submit_ms = (time.time_ns() - start) // 1_000_000

    if ret != RET_OK:
        return SubmitResult(False, None, None, f"place_order_rejected:{str(data)[:200]}",
                             submit_ms, ack_ms)
    rows = data.to_dict("records") if hasattr(data, "to_dict") else []
    row = rows[0] if rows else {}
    order_id = str(row.get("order_id") or "")
    status_v = str(row.get("order_status") or "")

    # Broker-comparison telemetry (SQLite hot store, not Mongo).
    try:
        from services import alpha_hot_store
        alpha_hot_store.init()
        _record_comparison(
            broker="moomoo", client_order_id=client_order_id,
            symbol=symbol, side=side, qty=qty, limit_price=limit_price,
            submit_latency_ms=int(submit_ms),
            ack_latency_ms=int(ack_ms),
            broker_order_id=order_id,
            status=status_v, error=None,
        )
    except Exception:  # noqa: BLE001
        pass
    return SubmitResult(True, order_id, status_v, None, int(submit_ms), int(ack_ms), row)


def _safe_lock(ctx: Any) -> None:
    """Re-lock trading. Never raise from this — we can't leave a failure
    path un-locked, but the lock call itself is best-effort."""
    try:
        ctx.unlock_trade(is_unlock=False)
    except Exception:  # noqa: BLE001
        pass


def cancel_order(order_id: str) -> bool:
    ctx = _get_trade_ctx()
    acc = _acc_id()
    if ctx is None or acc is None:
        return False
    try:
        from moomoo import RET_OK, TrdEnv, ModifyOrderOp
        ret, _data = ctx.modify_order(
            modify_order_op=ModifyOrderOp.CANCEL,
            order_id=order_id, trd_env=TrdEnv.REAL, acc_id=acc,
        )
        return ret == RET_OK
    except Exception:  # noqa: BLE001
        return False


# ─── options (schema-only in V1) ─────────────────────────────────


def submit_option(*, option_symbol: str, side: str, qty: int, limit_price: float,
                   client_order_id: str) -> SubmitResult:
    """Placeholder for options execution — deliberately gated OFF in V1.

    When ``MOOMOO_OPTIONS_ENABLED=1`` and the options-execution policy
    is ready, this will wrap ``ctx.place_order`` with ``OrderType.NORMAL``
    on an OPRA option symbol. Until then, autonomous option orders
    always return ``options_disabled``.
    """
    if not _options_enabled():
        return SubmitResult(False, None, None, "options_disabled", 0, 0)
    return SubmitResult(False, None, None, "options_execution_policy_not_ready", 0, 0)


# ─── broker-comparison telemetry ─────────────────────────────────


def _record_comparison(**fields: Any) -> None:
    """Compact per-submit row in SQLite (never Mongo). Callers should
    schedule a rollup that reads this into pattern-performance later."""
    import json
    import sqlite3
    try:
        from services import alpha_hot_store
        path = alpha_hot_store._path()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return
    try:
        with sqlite3.connect(path, timeout=5.0) as con:
            con.execute("""CREATE TABLE IF NOT EXISTS broker_comparison (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                broker TEXT NOT NULL,
                client_order_id TEXT,
                broker_order_id TEXT,
                symbol TEXT,
                side TEXT,
                qty REAL,
                limit_price REAL,
                submit_latency_ms INTEGER,
                ack_latency_ms INTEGER,
                fill_latency_ms INTEGER,
                fill_price REAL,
                slippage_bps REAL,
                status TEXT,
                error TEXT,
                extra TEXT,
                ts_ns INTEGER NOT NULL
            );""")
            con.execute("""INSERT INTO broker_comparison(
                broker, client_order_id, broker_order_id, symbol, side,
                qty, limit_price, submit_latency_ms, ack_latency_ms,
                fill_latency_ms, fill_price, slippage_bps, status, error, extra, ts_ns
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                str(fields.get("broker") or ""),
                str(fields.get("client_order_id") or "") or None,
                str(fields.get("broker_order_id") or "") or None,
                str(fields.get("symbol") or "") or None,
                str(fields.get("side") or "") or None,
                float(fields.get("qty") or 0.0) or None,
                float(fields.get("limit_price") or 0.0) or None,
                int(fields.get("submit_latency_ms") or 0) or None,
                int(fields.get("ack_latency_ms") or 0) or None,
                int(fields.get("fill_latency_ms") or 0) or None,
                float(fields.get("fill_price") or 0.0) or None,
                float(fields.get("slippage_bps") or 0.0) or None,
                str(fields.get("status") or "") or None,
                str(fields.get("error") or "") or None,
                json.dumps(fields.get("extra") or {})[:1000],
                time.time_ns(),
            ))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[moomoo_broker] comparison write failed: %s", exc)


def status() -> dict:
    """Non-secret operational status for the admin UI."""
    return {
        "opend_host": _host(),
        "opend_port": _port(),
        "connected": _trade_ctx is not None,
        "acc_id_configured": _acc_id() is not None,
        "live_enabled": _live_enabled(),
        "options_enabled": _options_enabled(),
        "max_notional_usd": _tiny_notional_cap_usd(),
        "market": "US",
        "security_firm": "FUTUINC",
    }


__all__ = [
    "SubmitResult",
    "account_info", "positions", "orders", "fills",
    "submit_equity", "submit_option", "cancel_order",
    "status", "close",
]
