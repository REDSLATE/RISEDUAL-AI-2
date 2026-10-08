"""Alpha Core v2 — compact receipt store (SQLite).

Retains structured receipts / history ONLY. It NEVER answers "does a live
position exist" — Public does. That is the guardrail against rebuilding the
exact stale-ledger failure Legacy hit. The store distinguishes two broker
facts: order acknowledgement/fill vs. the subsequently reconciled position.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading

logger = logging.getLogger(__name__)


class ReceiptStore:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        with self._lock:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS receipts (
                    receipt_id         TEXT PRIMARY KEY,
                    cycle_id           TEXT,
                    symbol             TEXT,
                    created_ns         INTEGER,
                    outcome            TEXT,
                    order_id           TEXT,
                    order_status       TEXT,
                    order_acknowledged INTEGER,
                    position_reconciled INTEGER,
                    position_status    TEXT,
                    payload            TEXT
                )"""
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_receipts_created ON receipts(created_ns)"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS exit_tracking "
                "(symbol TEXT PRIMARY KEY, entry_id TEXT, peak REAL, first_seen REAL)"
            )
            self._conn.commit()

    def reserve_order(self, receipt) -> bool:
        """Commit the client UUID before HTTP; serialize across store instances.

        A crash/timeout leaves a durable unknown order. It is never aged out.
        Only broker reconciliation can release this reservation.
        """
        d = receipt.to_dict()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT 1 FROM receipts WHERE symbol=? AND order_id IS NOT NULL "
                    "AND position_reconciled=0 AND position_status IN "
                    "('pending','closing','submitting') LIMIT 1", (d["symbol"],),
                ).fetchone()
                if row:
                    self._conn.rollback()
                    return False
                self._conn.execute(
                    "INSERT INTO receipts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (d["receipt_id"], d["cycle_id"], d["symbol"], d["created_ns"],
                     d["outcome"], d["order_id"], d["order_status"], 0, 0,
                     d["position_status"], json.dumps(d)),
                )
                self._conn.commit()
                return True
            except BaseException:
                self._conn.rollback()
                raise

    def save(self, receipt) -> None:
        d = receipt.to_dict()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO receipts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    d["receipt_id"], d["cycle_id"], d["symbol"], d["created_ns"],
                    d["outcome"], d.get("order_id"), d.get("order_status"),
                    1 if d.get("order_acknowledged") else 0,
                    1 if d.get("position_reconciled") else 0,
                    d.get("position_status") or "",
                    json.dumps(d),
                ),
            )
            self._conn.commit()

    def get_receipt(self, receipt_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT payload FROM receipts WHERE receipt_id=?",
                                     (receipt_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM receipts ORDER BY created_ns DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def open_position_symbols(self) -> set[str]:
        """Symbols v2 BELIEVES it opened (audit hint only — NOT authority)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT symbol FROM receipts WHERE position_status='open'",
            ).fetchall()
        return {r["symbol"] for r in rows}

    def outstanding_orders(self) -> list[dict]:
        """TRADED receipts whose resulting POSITION isn't reconciled yet."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM receipts WHERE order_id IS NOT NULL "
                "AND position_reconciled=0 AND position_status IN "
                "('pending','closing','submitting')",
            ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def mark_reconciled_flat(self, symbol: str) -> int:
        with self._lock:
            rows = self._conn.execute(
                "SELECT receipt_id, payload FROM receipts WHERE symbol=? "
                "AND position_status='open'", (symbol,),
            ).fetchall()
            for row in rows:
                d = json.loads(row["payload"])
                d["position_status"] = "reconciled_flat"
                d["reconciled_position_qty"] = 0.0
                self._conn.execute(
                    "UPDATE receipts SET position_status='reconciled_flat', payload=? "
                    "WHERE receipt_id=?", (json.dumps(d), row["receipt_id"]),
                )
            self._conn.commit()
            return len(rows)

    def update_reconciliation(self, receipt_id: str, *, position_reconciled: bool,
                              reconciled_position_qty: float, order_status: str,
                              position_status: str, filled_qty: float = 0.0,
                              fill_price: float = 0.0) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM receipts WHERE receipt_id=?", (receipt_id,),
            ).fetchone()
            if not row:
                return
            d = json.loads(row["payload"])
            d.update({
                "position_reconciled": position_reconciled,
                "reconciled_position_qty": reconciled_position_qty,
                "order_status": order_status,
                "position_status": position_status,
                "broker_reported_fill_qty": filled_qty,
                "fill_price": fill_price or d.get("fill_price", 0.0),
            })
            self._conn.execute(
                "UPDATE receipts SET position_reconciled=?, order_status=?, "
                "position_status=?, payload=? WHERE receipt_id=?",
                (1 if position_reconciled else 0, order_status, position_status,
                 json.dumps(d), receipt_id),
            )
            self._conn.commit()

    def last_entry_price(self, symbol: str) -> float:
        """Most recent broker fill/execution price for an OPEN entry on this
        symbol. Used by the exit policy to anchor stop/target. Returns 0.0 when
        unknown (caller must treat 0.0 as 'no anchor' and skip exit math)."""
        anchor = self.entry_anchor(symbol)
        return float(anchor.get("fill_price") or 0.0) if anchor else 0.0

    def entry_anchor(self, symbol: str) -> dict | None:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM receipts WHERE symbol=? AND position_status='open' "
                "ORDER BY created_ns DESC", (symbol,),
            ).fetchall()
        for row in rows:
            d = json.loads(row["payload"])
            if d.get("action") != "close" and float(d.get("fill_price") or 0) > 0:
                return d
        return None

    def track_exit(self, symbol: str, entry_id: str, price: float, now: float,
                   opened_at: float) -> tuple[float, float]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM exit_tracking WHERE symbol=?", (symbol,),
            ).fetchone()
            same_entry = row is not None and row["entry_id"] == entry_id
            peak = max(price, row["peak"]) if same_entry else price
            first = row["first_seen"] if same_entry else min(now, opened_at)
            self._conn.execute(
                "INSERT OR REPLACE INTO exit_tracking VALUES (?,?,?,?)",
                (symbol, entry_id, peak, first),
            )
            self._conn.commit()
        return peak, first

    def counts(self) -> dict:
        with self._lock:
            rows = self._conn.execute(
                "SELECT outcome, COUNT(*) n FROM receipts GROUP BY outcome",
            ).fetchall()
        return {r["outcome"]: r["n"] for r in rows}
