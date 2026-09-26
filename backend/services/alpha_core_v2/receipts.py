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
            os.makedirs(os.path.dirname(path), exist_ok=True)
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
            self._conn.commit()

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
                "SELECT payload FROM receipts WHERE outcome='TRADED' "
                "AND position_reconciled=0",
            ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def mark_reconciled_flat(self, symbol: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE receipts SET position_status='reconciled_flat' "
                "WHERE symbol=? AND position_status='open'", (symbol,),
            )
            self._conn.commit()
            return cur.rowcount or 0

    def update_reconciliation(self, receipt_id: str, *, position_reconciled: bool,
                              reconciled_position_qty: float, order_status: str,
                              position_status: str) -> None:
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
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM receipts WHERE symbol=? AND outcome='TRADED' "
                "ORDER BY created_ns DESC LIMIT 20", (symbol,),
            ).fetchall()
        for r in rows:
            d = json.loads(r["payload"])
            if d.get("action") == "close":
                continue
            price = float(d.get("fill_price") or 0.0) or float(d.get("execution_price") or 0.0)
            if price > 0:
                return price
        return 0.0

    def counts(self) -> dict:
        with self._lock:
            rows = self._conn.execute(
                "SELECT outcome, COUNT(*) n FROM receipts GROUP BY outcome",
            ).fetchall()
        return {r["outcome"]: r["n"] for r in rows}
