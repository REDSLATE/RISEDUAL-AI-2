"""Broker Comparison Service.

Reads compact per-submit rows from the SQLite ``broker_comparison`` table
(written by both MooMoo and Public.com execution paths) and produces
aggregates the Mission Control UI can eyeball to decide which broker
actually fills better.

Metrics (per broker)
--------------------
* ``samples``           : row count in the requested window
* ``fill_rate``         : filled_ok / total  (0..1)
* ``avg_slippage_bps``  : mean of ``slippage_bps`` over filled rows
* ``p50_ack_ms``        : median ack latency (submit → broker ack) ms
* ``p95_ack_ms``        : 95th percentile ack latency ms
* ``avg_fill_latency_ms``: mean broker→fill latency ms (filled rows only)

Winner (composite score, per operator spec)
-------------------------------------------
Slippage    50%
Fill rate   30%
Ack latency 20%

Each raw metric is normalized to a 0..1 score where 1 is best. When only
one broker has samples the winner is that broker; when neither has any
data we return ``winner=None`` and expose ``low_sample_warning=True``.

We never hide low sample counts — the UI shows the raw sample count so
the operator can decide whether the composite is meaningful yet.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from statistics import mean
from typing import Any, Optional

from services import alpha_hot_store

logger = logging.getLogger(__name__)

# Composite weights — kept as module constants so tests can assert.
W_SLIPPAGE = 0.50
W_FILL_RATE = 0.30
W_ACK_LATENCY = 0.20

# Known brokers we always emit (even with zero samples) so the UI can
# still render the "Public vs MooMoo" side-by-side view without
# undefined branches.
_KNOWN_BROKERS = ("public", "moomoo")

# Sample floor below which the winner should be treated as unreliable.
LOW_SAMPLE_THRESHOLD = 10

# ── time window parsing ─────────────────────────────────────────────

_WINDOWS_SEC = {
    "1h": 3_600,
    "6h": 6 * 3_600,
    "1d": 86_400,
    "3d": 3 * 86_400,
    "7d": 7 * 86_400,
    "30d": 30 * 86_400,
}


def _window_to_ns(window: str) -> Optional[int]:
    """Return the cutoff timestamp in ns for a window, or None for ``all``."""
    w = (window or "").strip().lower()
    if w in ("", "all"):
        return None
    sec = _WINDOWS_SEC.get(w)
    if sec is None:
        return None
    return time.time_ns() - int(sec * 1e9)


# ── SQLite reads (fail-safe) ─────────────────────────────────────────


def _rows(
    since_ns: Optional[int] = None,
    symbol: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict]:
    """Read broker_comparison rows. Returns [] on any failure so callers
    never have to think about whether the table exists yet."""
    try:
        alpha_hot_store.init()
        path = alpha_hot_store._path()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return []
    where: list[str] = []
    args: list[Any] = []
    if since_ns is not None:
        where.append("ts_ns >= ?")
        args.append(int(since_ns))
    if symbol:
        where.append("upper(symbol) = ?")
        args.append(symbol.upper())
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    limit_sql = f"LIMIT {int(limit)}" if limit else ""
    q = (
        "SELECT broker, symbol, side, qty, limit_price, submit_latency_ms, "
        "ack_latency_ms, fill_latency_ms, fill_price, slippage_bps, "
        "status, error, ts_ns "
        f"FROM broker_comparison {where_sql} ORDER BY ts_ns DESC {limit_sql}"
    )
    try:
        with sqlite3.connect(path, timeout=5.0) as con:
            con.row_factory = sqlite3.Row
            # Table may not exist yet on a fresh box — swallow that.
            try:
                cur = con.execute(q, args)
            except sqlite3.OperationalError:
                return []
            return [dict(r) for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        logger.debug("[broker_comparison] rows read failed: %s", exc)
        return []


# ── stats helpers ────────────────────────────────────────────────────


def _percentile(values: list[float], q: float) -> Optional[float]:
    """Simple percentile (linear interpolation) for tiny sample sizes.
    Returns None on empty input rather than raising."""
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return float(xs[lo] * (1.0 - frac) + xs[hi] * frac)


def _filled(row: dict) -> bool:
    """A row is 'filled' when we have both a real slippage measurement
    AND either a positive fill price or a broker-side filled status."""
    slip = row.get("slippage_bps")
    fill_px = row.get("fill_price")
    status = (row.get("status") or "").lower()
    if slip is None and fill_px is None and "fill" not in status:
        return False
    if row.get("error"):
        # Even if partial data was recorded, an error trumps the fill flag.
        return False
    return True


def _aggregate_broker(rows: list[dict]) -> dict:
    """Compute the raw per-broker metrics dict (no scoring yet)."""
    total = len(rows)
    filled_rows = [r for r in rows if _filled(r)]
    filled_ok = len(filled_rows)
    slip_vals = [
        float(r["slippage_bps"]) for r in filled_rows
        if r.get("slippage_bps") is not None
    ]
    ack_vals = [
        float(r["ack_latency_ms"]) for r in rows
        if r.get("ack_latency_ms") is not None and r["ack_latency_ms"] > 0
    ]
    fill_lat_vals = [
        float(r["fill_latency_ms"]) for r in filled_rows
        if r.get("fill_latency_ms") is not None and r["fill_latency_ms"] > 0
    ]
    return {
        "samples": total,
        "filled": filled_ok,
        "fill_rate": (filled_ok / total) if total > 0 else None,
        "avg_slippage_bps": (mean(slip_vals) if slip_vals else None),
        "p50_ack_ms": _percentile(ack_vals, 0.50),
        "p95_ack_ms": _percentile(ack_vals, 0.95),
        "avg_fill_latency_ms": (mean(fill_lat_vals) if fill_lat_vals else None),
        "rejected": sum(1 for r in rows if r.get("error")),
    }


def _score_component(
    broker_value: Optional[float],
    other_value: Optional[float],
    *,
    lower_is_better: bool,
) -> Optional[float]:
    """Score one broker on one metric relative to the other. Returns a
    number in [0, 1] where 1 = this broker is at least as good, and the
    losing broker gets a proportional partial score. Returns None when
    the metric is unavailable for this broker (so weight redistribution
    can happen upstream)."""
    if broker_value is None:
        return None
    if other_value is None:
        # Only this broker has data — award full credit for the metric.
        return 1.0
    if lower_is_better:
        # Smaller value → better. Score = min(other, self) / max(other, self)
        # for the loser; winner gets 1.
        if broker_value <= 0 and other_value <= 0:
            return 0.5
        if broker_value <= other_value:
            return 1.0
        # broker_value > other_value → partial
        if broker_value == 0:
            return 1.0
        return max(0.0, min(1.0, other_value / broker_value))
    # higher_is_better
    if broker_value >= other_value:
        return 1.0
    if other_value == 0:
        return 0.0
    return max(0.0, min(1.0, broker_value / other_value))


def _composite_score(self_agg: dict, other_agg: dict) -> Optional[float]:
    """Blend slippage / fill_rate / ack_latency into a single 0..1 score.
    Returns None when no components can be scored."""
    parts: list[tuple[float, float]] = []  # (weight, score)

    s = _score_component(
        self_agg.get("avg_slippage_bps"),
        other_agg.get("avg_slippage_bps"),
        lower_is_better=True,
    )
    if s is not None:
        parts.append((W_SLIPPAGE, s))

    f = _score_component(
        self_agg.get("fill_rate"),
        other_agg.get("fill_rate"),
        lower_is_better=False,
    )
    if f is not None:
        parts.append((W_FILL_RATE, f))

    a = _score_component(
        self_agg.get("p50_ack_ms"),
        other_agg.get("p50_ack_ms"),
        lower_is_better=True,
    )
    if a is not None:
        parts.append((W_ACK_LATENCY, a))

    if not parts:
        return None
    wsum = sum(w for w, _ in parts)
    if wsum <= 0:
        return None
    return round(sum(w * v for w, v in parts) / wsum, 4)


# ── public API ───────────────────────────────────────────────────────


def compare(
    *,
    window: str = "1d",
    symbol: Optional[str] = None,
) -> dict:
    """Return per-broker aggregates + composite winner for the window."""
    since_ns = _window_to_ns(window)
    rows = _rows(since_ns=since_ns, symbol=symbol)
    by_broker: dict[str, list[dict]] = {b: [] for b in _KNOWN_BROKERS}
    for r in rows:
        b = (r.get("broker") or "").lower()
        if b not in by_broker:
            by_broker[b] = []
        by_broker[b].append(r)

    aggs = {b: _aggregate_broker(rs) for b, rs in by_broker.items()}
    # Only Public vs MooMoo participate in the composite scoring.
    pub = aggs.get("public") or _aggregate_broker([])
    moo = aggs.get("moomoo") or _aggregate_broker([])
    pub["composite_score"] = _composite_score(pub, moo)
    moo["composite_score"] = _composite_score(moo, pub)

    # Pick the winner. Ties or no data → None.
    winner: Optional[str] = None
    a, b = pub["composite_score"], moo["composite_score"]
    if a is not None or b is not None:
        if a is None:
            winner = "moomoo"
        elif b is None:
            winner = "public"
        elif a > b:
            winner = "public"
        elif b > a:
            winner = "moomoo"
        else:
            winner = None  # exact tie
    low_sample = any(
        aggs[br]["samples"] > 0 and aggs[br]["samples"] < LOW_SAMPLE_THRESHOLD
        for br in _KNOWN_BROKERS
    ) or all(aggs[br]["samples"] == 0 for br in _KNOWN_BROKERS)

    return {
        "window": window,
        "symbol": (symbol or "").upper() or None,
        "brokers": {b: aggs[b] for b in _KNOWN_BROKERS},
        "winner": winner,
        "weights": {
            "slippage": W_SLIPPAGE,
            "fill_rate": W_FILL_RATE,
            "ack_latency": W_ACK_LATENCY,
        },
        "low_sample_warning": low_sample,
        "low_sample_threshold": LOW_SAMPLE_THRESHOLD,
    }


def recent_rows(*, limit: int = 50, symbol: Optional[str] = None) -> list[dict]:
    """Last N raw rows for the side-by-side table."""
    return _rows(since_ns=None, symbol=symbol, limit=int(max(1, min(500, limit))))


def record_public_submit(
    *,
    client_order_id: str,
    broker_order_id: str,
    symbol: str,
    side: str,
    qty: float,
    limit_price: float,
    submit_latency_ms: int,
    ack_latency_ms: int,
    fill_price: Optional[float],
    status: str,
    error: Optional[str] = None,
) -> None:
    """Compact writer for the Public.com executor.

    Slippage (in bps) is derived here from ``fill_price`` vs
    ``limit_price`` when both are present so aggregation code doesn't
    need a second data source. Marketable orders will not have an
    explicit limit_price — in that case pass ``limit_price`` as the
    mark price used at submit time (aka expected fill) and slippage
    still comes out honestly.
    """
    import json  # local — writes are already infrequent
    slippage_bps: Optional[float] = None
    fill_latency_ms: Optional[int] = None
    if fill_price and limit_price and limit_price > 0:
        # Signed slippage in bps relative to expected fill.
        # BUY: paying more than expected is positive slippage (bad for us).
        # SELL: receiving less than expected is positive slippage (bad).
        side_up = (side or "").upper()
        raw = (float(fill_price) - float(limit_price)) / float(limit_price) * 10_000.0
        slippage_bps = raw if side_up == "BUY" else -raw
    try:
        alpha_hot_store.init()
        path = alpha_hot_store._path()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return
    try:
        with sqlite3.connect(path, timeout=5.0) as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS broker_comparison (
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
                );"""
            )
            con.execute(
                """INSERT INTO broker_comparison(
                    broker, client_order_id, broker_order_id, symbol, side,
                    qty, limit_price, submit_latency_ms, ack_latency_ms,
                    fill_latency_ms, fill_price, slippage_bps, status, error, extra, ts_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "public",
                    (client_order_id or "") or None,
                    (broker_order_id or "") or None,
                    (symbol or "").upper() or None,
                    (side or "").upper() or None,
                    float(qty) or None,
                    float(limit_price) or None,
                    int(submit_latency_ms) or None,
                    int(ack_latency_ms) or None,
                    fill_latency_ms,
                    float(fill_price) if fill_price else None,
                    slippage_bps,
                    (status or "") or None,
                    (error or "") or None,
                    json.dumps({}),
                    time.time_ns(),
                ),
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[broker_comparison] public write failed: %s", exc)


__all__ = [
    "compare",
    "recent_rows",
    "record_public_submit",
    "W_SLIPPAGE",
    "W_FILL_RATE",
    "W_ACK_LATENCY",
    "LOW_SAMPLE_THRESHOLD",
]
