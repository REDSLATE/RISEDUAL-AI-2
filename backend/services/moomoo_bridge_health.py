"""MooMoo bridge health + explicit readiness ladder.

Architecture (operator directive, 2026-02)
==========================================
Alpha's broker plane runs Public.com and MooMoo INDEPENDENTLY. Public
never depends on MooMoo being reachable. MooMoo joins the Funnel as a
second market witness in stages — not one giant "connected" flag.

Readiness ladder
----------------
``DISCONNECTED``   — TCP to OpenD unreachable.
``CONNECTED``      — TCP handshake succeeds (OpenD is up).
``DATA_READY``     — Quote context opens and returns a fresh snapshot
                     for a canary symbol (SPY by default).
``RESEARCH_READY`` — Trade context opens AND account info retrievable
                     (i.e. the pod can enumerate the MooMoo account).
                     Alpha may consult MooMoo as a research witness.
``EXECUTION_READY``— The operator has flipped the explicit execution
                     enable AND ``MOOMOO_LIVE_ENABLED=1``. Only at
                     this level does the broker router route orders
                     via MooMoo.

Design constraints
------------------
* All probes are best-effort and NEVER raise. A missing OpenD is
  reported, not propagated.
* Smoke test is read-only: TCP → quote → account/perm → normalized
  ``BrokerResearchSnapshot``. Never submits an order.
* State is computed on demand — no background thread. Callers ask,
  we answer with what's true right now.
* Ladder is monotonic: if a lower rung fails, higher rungs cannot
  be true (an EXECUTION_READY that has lost DATA_READY is impossible).
"""
from __future__ import annotations

import logging
import os
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ReadinessState:
    DISCONNECTED = "DISCONNECTED"
    CONNECTED = "CONNECTED"
    DATA_READY = "DATA_READY"
    RESEARCH_READY = "RESEARCH_READY"
    EXECUTION_READY = "EXECUTION_READY"


_LADDER = [
    ReadinessState.DISCONNECTED,
    ReadinessState.CONNECTED,
    ReadinessState.DATA_READY,
    ReadinessState.RESEARCH_READY,
    ReadinessState.EXECUTION_READY,
]


# ── Config helpers ─────────────────────────────────────────────────

def _host() -> str:
    return (os.environ.get("MOOMOO_OPEND_HOST") or "moomoo-opend").strip()


def _port() -> int:
    try:
        return int(os.environ.get("MOOMOO_OPEND_PORT") or "11111")
    except (TypeError, ValueError):
        return 11111


def _canary_symbol() -> str:
    return (os.environ.get("MOOMOO_CANARY_SYMBOL") or "SPY").strip().upper()


def _execution_gate_enabled() -> bool:
    """Operator's explicit execution readiness — separate from LIVE_ENABLED.

    ``MOOMOO_EXECUTION_READY`` gates the Funnel's execution routing.
    ``MOOMOO_LIVE_ENABLED`` is the hardware kill switch. BOTH must be on
    for the ladder to reach EXECUTION_READY.
    """
    return (os.environ.get("MOOMOO_EXECUTION_READY") or "0").strip().lower() in (
        "1", "true", "yes", "on"
    )


def _live_enabled() -> bool:
    return (os.environ.get("MOOMOO_LIVE_ENABLED") or "0").strip().lower() in (
        "1", "true", "yes", "on"
    )


# ── Health dataclass ───────────────────────────────────────────────

@dataclass
class MoomooBridgeHealth:
    state: str
    checked_at_ns: int

    # OpenD reachability
    opend_host: str
    opend_port: int
    opend_reachable: bool
    tcp_latency_ms: Optional[float] = None

    # Quote context
    market_data_ok: bool = False
    canary_symbol: Optional[str] = None
    canary_quote_age_seconds: Optional[float] = None
    canary_last_price: Optional[float] = None
    quote_latency_ms: Optional[float] = None

    # Trade context
    trade_ctx_ok: bool = False
    account_ok: bool = False
    account_id: Optional[int] = None
    buying_power_usd: Optional[float] = None
    trading_permission: Optional[str] = None  # "REAL" | "SIMULATE" | None

    # Gates
    live_enabled: bool = False
    execution_ready_flag: bool = False

    # Diagnostics
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "checked_at_ns": self.checked_at_ns,
            "opend": {
                "host": self.opend_host,
                "port": self.opend_port,
                "reachable": self.opend_reachable,
                "tcp_latency_ms": self.tcp_latency_ms,
            },
            "market_data": {
                "ok": self.market_data_ok,
                "canary_symbol": self.canary_symbol,
                "canary_quote_age_seconds": self.canary_quote_age_seconds,
                "canary_last_price": self.canary_last_price,
                "quote_latency_ms": self.quote_latency_ms,
            },
            "trade": {
                "context_ok": self.trade_ctx_ok,
                "account_ok": self.account_ok,
                "account_id": self.account_id,
                "buying_power_usd": self.buying_power_usd,
                "trading_permission": self.trading_permission,
            },
            "gates": {
                "live_enabled": self.live_enabled,
                "execution_ready_flag": self.execution_ready_flag,
            },
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


# ── TCP probe ──────────────────────────────────────────────────────

def _probe_tcp(host: str, port: int, *, timeout: float = 2.0) -> tuple[bool, Optional[float], Optional[str]]:
    """Return (reachable, latency_ms, err)."""
    started = time.time()
    try:
        # Fast path: getaddrinfo can hang if DNS is off, wrap in socket.setdefaulttimeout.
        with socket.create_connection((host, port), timeout=timeout):
            latency_ms = (time.time() - started) * 1000.0
            return True, round(latency_ms, 2), None
    except (socket.timeout, TimeoutError) as exc:
        return False, None, f"tcp_timeout:{exc}"
    except OSError as exc:
        return False, None, f"tcp_error:{exc.__class__.__name__}"


# ── Quote probe ────────────────────────────────────────────────────

def _probe_quote(symbol: str) -> tuple[bool, Optional[dict], Optional[str]]:
    try:
        from services import moomoo_market_data_adapter
        started = time.time()
        snap = moomoo_market_data_adapter.snapshot_quote(symbol)
        latency_ms = (time.time() - started) * 1000.0
        if snap is None:
            return False, None, "quote_snapshot_none"
        payload = {
            "last": snap.last, "bid": snap.bid, "ask": snap.ask,
            "volume": snap.volume, "ts": snap.ts,
            "latency_ms": round(latency_ms, 2),
        }
        return True, payload, None
    except Exception as exc:  # noqa: BLE001
        return False, None, f"quote_exception:{exc.__class__.__name__}"


# ── Account probe ─────────────────────────────────────────────────

def _probe_account() -> tuple[bool, Optional[dict], Optional[str]]:
    try:
        from services import moomoo_broker_adapter
        info = moomoo_broker_adapter.account_info()
        if info is None:
            return False, None, "account_info_none"
        return True, info, None
    except Exception as exc:  # noqa: BLE001
        return False, None, f"account_exception:{exc.__class__.__name__}"


# ── Composite ─────────────────────────────────────────────────────

def compute_health() -> MoomooBridgeHealth:
    """Non-invasive read-only probe of the entire ladder."""
    h = MoomooBridgeHealth(
        state=ReadinessState.DISCONNECTED,
        checked_at_ns=time.time_ns(),
        opend_host=_host(),
        opend_port=_port(),
        opend_reachable=False,
        canary_symbol=_canary_symbol(),
        live_enabled=_live_enabled(),
        execution_ready_flag=_execution_gate_enabled(),
    )

    # Rung 1 → CONNECTED
    reachable, tcp_ms, tcp_err = _probe_tcp(h.opend_host, h.opend_port)
    h.opend_reachable = reachable
    h.tcp_latency_ms = tcp_ms
    if not reachable:
        if tcp_err:
            h.errors.append(tcp_err)
        return h
    h.state = ReadinessState.CONNECTED

    # Rung 2 → DATA_READY
    ok_q, quote, q_err = _probe_quote(h.canary_symbol or "SPY")
    if ok_q and quote:
        h.market_data_ok = True
        h.canary_last_price = quote.get("last")
        h.quote_latency_ms = quote.get("latency_ms")
        # ts is a string in ET wall clock — compute age best-effort.
        h.canary_quote_age_seconds = _quote_age_seconds(quote.get("ts"))
        h.state = ReadinessState.DATA_READY
    else:
        if q_err:
            h.warnings.append(q_err)
        return h

    # Rung 3 → RESEARCH_READY
    ok_a, acct, a_err = _probe_account()
    if ok_a and acct:
        h.trade_ctx_ok = True
        h.account_ok = True
        try:
            h.account_id = int(os.environ.get("MOOMOO_ACC_ID") or 0) or None
        except (TypeError, ValueError):
            h.account_id = None
        # Field names vary across SDK builds; try common ones.
        for key in ("power", "buying_power", "cash", "total_assets"):
            if key in acct and acct.get(key) is not None:
                try:
                    h.buying_power_usd = float(acct.get(key))
                    break
                except (TypeError, ValueError):
                    continue
        h.trading_permission = str(acct.get("trd_env") or "REAL")
        h.state = ReadinessState.RESEARCH_READY
    else:
        if a_err:
            h.warnings.append(a_err)
        return h

    # Rung 4 → EXECUTION_READY (BOTH gates required)
    if h.live_enabled and h.execution_ready_flag:
        h.state = ReadinessState.EXECUTION_READY
    else:
        missing = []
        if not h.live_enabled:
            missing.append("MOOMOO_LIVE_ENABLED=0")
        if not h.execution_ready_flag:
            missing.append("MOOMOO_EXECUTION_READY=0")
        h.warnings.append(f"execution_gate_off:{','.join(missing)}")
    return h


def _quote_age_seconds(ts: Optional[str]) -> Optional[float]:
    """Best-effort age from MooMoo ``data_time`` string (ET wall clock)."""
    if not ts:
        return None
    try:
        from datetime import datetime, timezone
        try:
            from zoneinfo import ZoneInfo
            et = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=ZoneInfo("America/New_York"))
        except (ValueError, ImportError):
            et = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            et = et.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - et).total_seconds()
        return round(age, 3)
    except Exception:  # noqa: BLE001
        return None


# ── Read-only smoke test ──────────────────────────────────────────

def run_smoke_test(symbol: Optional[str] = None) -> dict[str, Any]:
    """End-to-end READ-ONLY probe: tunnel → OpenD → account/perm →
    fresh quote → normalized BrokerResearchSnapshot. Never submits.

    Returns a dict with each stage's status so the operator can see
    exactly where the path fails.
    """
    sym = (symbol or _canary_symbol() or "SPY").upper()
    started_ns = time.time_ns()

    stages: dict[str, Any] = {
        "symbol": sym,
        "started_at_ns": started_ns,
        "tunnel": {"ok": False},
        "market_data": {"ok": False},
        "account": {"ok": False},
        "research_snapshot": {"ok": False},
    }

    # 1) Tunnel / TCP
    reachable, tcp_ms, tcp_err = _probe_tcp(_host(), _port())
    stages["tunnel"] = {
        "ok": reachable, "host": _host(), "port": _port(),
        "latency_ms": tcp_ms, "error": tcp_err,
    }
    if not reachable:
        stages["overall"] = {"ok": False, "failed_at": "tunnel"}
        return stages

    # 2) Market data / canary quote
    ok_q, quote, q_err = _probe_quote(sym)
    stages["market_data"] = {"ok": ok_q, "quote": quote, "error": q_err}
    if not ok_q:
        stages["overall"] = {"ok": False, "failed_at": "market_data"}
        return stages

    # 3) Account / trading permission (read-only)
    ok_a, acct, a_err = _probe_account()
    stages["account"] = {
        "ok": ok_a,
        "trading_permission": str((acct or {}).get("trd_env") or "REAL"),
        "error": a_err,
    }

    # 4) Normalized BrokerResearchSnapshot via the same path Alpha uses.
    try:
        import asyncio
        from services.alpha_broker_research import perform_research
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Called from an async context — run in a fresh loop
                # via a thread so we can synchronously return the snapshot.
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    fut = ex.submit(asyncio.run, perform_research(
                        symbol=sym,
                        signal_price=float((quote or {}).get("last") or 0.0),
                    ))
                    snap = fut.result(timeout=8.0)
            else:
                snap = asyncio.run(perform_research(
                    symbol=sym,
                    signal_price=float((quote or {}).get("last") or 0.0),
                ))
        except RuntimeError:
            snap = asyncio.run(perform_research(
                symbol=sym,
                signal_price=float((quote or {}).get("last") or 0.0),
            ))
        stages["research_snapshot"] = {
            "ok": True,
            "broker": snap.broker,
            "current_price": snap.current_price,
            "drift_bps": snap.drift_bps,
            "broker_quote_age_seconds": snap.broker_quote_age_seconds,
            "bid": snap.bid,
            "ask": snap.ask,
            "spread_bps": snap.spread_bps,
            "hard_block_reasons": list(snap.hard_block_reasons),
        }
    except Exception as exc:  # noqa: BLE001
        stages["research_snapshot"] = {
            "ok": False, "error": f"research_exception:{exc.__class__.__name__}",
        }

    stages["overall"] = {"ok": True, "duration_ms": round((time.time_ns() - started_ns) / 1e6, 2)}
    return stages


__all__ = [
    "ReadinessState", "MoomooBridgeHealth",
    "compute_health", "run_smoke_test",
]
