from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .models import (
    ClaimRequest,
    ClaimResult,
    IntentStatus,
    IntentTransitionResult,
    TERMINAL_INTENT_STATUSES,
    TerminalResult,
    TraceEvent,
)


class AtlasLedgerError(RuntimeError):
    pass


class IntentNotFound(AtlasLedgerError):
    pass


class InvalidTransition(AtlasLedgerError):
    pass


class TerminalIntentError(InvalidTransition):
    pass


class IdentityCollision(AtlasLedgerError):
    pass


class TraceError(AtlasLedgerError):
    pass


ALLOWED_TRANSITIONS: dict[IntentStatus, frozenset[IntentStatus]] = {
    IntentStatus.PENDING: frozenset(
        {IntentStatus.APPROVED, IntentStatus.REJECTED, IntentStatus.EXPIRED}
    ),
    IntentStatus.APPROVED: frozenset(
        {IntentStatus.SUBMITTED, IntentStatus.REJECTED, IntentStatus.EXPIRED}
    ),
    IntentStatus.SUBMITTED: frozenset(
        {IntentStatus.TERMINAL, IntentStatus.REJECTED, IntentStatus.EXPIRED}
    ),
    IntentStatus.TERMINAL: frozenset(),
    IntentStatus.REJECTED: frozenset(),
    IntentStatus.EXPIRED: frozenset(),
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 3)


class AtlasLedger:
    """Compact SQLite ledger for idempotency, lifecycle, and timing traces.

    The ledger stores payload hashes rather than full market/order payloads. It is not an
    order router and never contacts a broker.
    """

    def __init__(self, path: str | Path, *, max_events_per_trace: int = 32):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_events_per_trace = max_events_per_trace
        if max_events_per_trace < 4 or max_events_per_trace > 128:
            raise ValueError("max_events_per_trace must be between 4 and 128")
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS intent_ledger (
                    intent_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    source_idempotency_key TEXT UNIQUE,
                    client_order_id TEXT UNIQUE,
                    stack TEXT NOT NULL,
                    lane TEXT NOT NULL,
                    broker TEXT NOT NULL,
                    broker_account_ref TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
                    strategy TEXT NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('pending', 'approved', 'submitted', 'terminal', 'rejected', 'expired')
                    ),
                    payload_sha256 TEXT NOT NULL DEFAULT '',
                    broker_order_id TEXT UNIQUE,
                    reason_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    terminal_at TEXT,
                    transition_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_intent_status_updated
                    ON intent_ledger(status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_intent_symbol_created
                    ON intent_ledger(symbol, created_at);

                CREATE TABLE IF NOT EXISTS intent_transition (
                    intent_id TEXT NOT NULL REFERENCES intent_ledger(intent_id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    reason_code TEXT,
                    occurred_at TEXT NOT NULL,
                    PRIMARY KEY (intent_id, sequence)
                );

                CREATE TABLE IF NOT EXISTS trace_summary (
                    trace_id TEXT PRIMARY KEY,
                    correlation_id TEXT,
                    stack TEXT NOT NULL,
                    lane TEXT,
                    symbol TEXT,
                    build_sha TEXT,
                    started_ns INTEGER NOT NULL,
                    ended_ns INTEGER,
                    terminal_result TEXT CHECK (
                        terminal_result IS NULL OR terminal_result IN (
                            'NO_SETUP', 'BRAIN_HOLD', 'INTENT_CREATED',
                            'DUPLICATE_SUPPRESSED', 'GATE_BLOCKED',
                            'BROKER_SUBMITTED', 'BROKER_REJECTED',
                            'BROKER_ACKNOWLEDGED', 'ERROR'
                        )
                    ),
                    reason_code TEXT,
                    intent_id TEXT,
                    total_latency_ms REAL,
                    bottleneck_stage TEXT,
                    event_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_trace_result_started
                    ON trace_summary(terminal_result, started_ns);
                CREATE INDEX IF NOT EXISTS idx_trace_symbol_started
                    ON trace_summary(symbol, started_ns);

                CREATE TABLE IF NOT EXISTS trace_event (
                    trace_id TEXT NOT NULL REFERENCES trace_summary(trace_id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    reason_code TEXT,
                    timestamp_ns INTEGER NOT NULL,
                    latency_from_previous_ms REAL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (trace_id, sequence)
                );

                PRAGMA user_version = 1;
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(intent_ledger)").fetchall()
            }
            if "source_idempotency_key" not in columns:
                connection.execute(
                    "ALTER TABLE intent_ledger ADD COLUMN source_idempotency_key TEXT"
                )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_intent_source_idempotency
                ON intent_ledger(source_idempotency_key)
                WHERE source_idempotency_key IS NOT NULL
                """
            )

    def claim_intent(self, request: ClaimRequest) -> ClaimResult:
        canonical = request.fingerprint.canonical()
        intent_id = request.normalized_intent_id()
        idempotency_key = request.idempotency_key()
        source_idempotency_key = request.source_idempotency_key()
        client_order_id = (
            str(request.client_order_id).strip()[:240] if request.client_order_id else None
        )
        now = _utc_now()

        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    """
                    SELECT intent_id, idempotency_key, source_idempotency_key,
                           client_order_id, status
                    FROM intent_ledger
                    WHERE intent_id = ?
                       OR idempotency_key = ?
                       OR (? IS NOT NULL AND source_idempotency_key = ?)
                       OR (? IS NOT NULL AND client_order_id = ?)
                    """,
                    (
                        intent_id,
                        idempotency_key,
                        source_idempotency_key,
                        source_idempotency_key,
                        client_order_id,
                        client_order_id,
                    ),
                ).fetchall()
                distinct = {row["intent_id"] for row in existing}
                if len(distinct) > 1:
                    raise IdentityCollision(
                        "intent_id, idempotency_key, and client_order_id resolve to different intents"
                    )
                if existing:
                    row = existing[0]
                    duplicate_by = (
                        "intent_id"
                        if row["intent_id"] == intent_id
                        else "idempotency_key"
                        if row["idempotency_key"] == idempotency_key
                        else "source_idempotency_key"
                        if source_idempotency_key is not None
                        and row["source_idempotency_key"] == source_idempotency_key
                        else "client_order_id"
                    )
                    connection.execute("COMMIT")
                    return ClaimResult(
                        accepted=False,
                        intent_id=intent_id,
                        idempotency_key=idempotency_key,
                        status=IntentStatus(row["status"]),
                        duplicate_by=duplicate_by,
                        existing_intent_id=row["intent_id"],
                    )

                connection.execute(
                    """
                    INSERT INTO intent_ledger (
                        intent_id, idempotency_key, source_idempotency_key, client_order_id,
                        stack, lane, broker, broker_account_ref, symbol, side,
                        strategy, opportunity_id, status, payload_sha256,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    (
                        intent_id,
                        idempotency_key,
                        source_idempotency_key,
                        client_order_id,
                        canonical["stack"],
                        canonical["lane"],
                        canonical["broker"],
                        canonical["broker_account_ref"],
                        canonical["symbol"],
                        canonical["side"],
                        canonical["strategy"],
                        canonical["opportunity_id"],
                        request.payload_sha256(),
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO intent_transition (
                        intent_id, sequence, from_status, to_status, occurred_at
                    ) VALUES (?, 0, NULL, 'pending', ?)
                    """,
                    (intent_id, now),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

        return ClaimResult(
            accepted=True,
            intent_id=intent_id,
            idempotency_key=idempotency_key,
            status=IntentStatus.PENDING,
        )

    def transition_intent(
        self,
        intent_id: str,
        to_status: IntentStatus | str,
        *,
        reason_code: str | None = None,
        broker_order_id: str | None = None,
    ) -> IntentTransitionResult:
        target = IntentStatus(to_status)
        intent_id = str(intent_id).strip()
        now = _utc_now()
        reason_code = str(reason_code).strip()[:160] if reason_code else None
        broker_order_id = str(broker_order_id).strip()[:240] if broker_order_id else None

        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT status, transition_count, broker_order_id
                    FROM intent_ledger WHERE intent_id = ?
                    """,
                    (intent_id,),
                ).fetchone()
                if row is None:
                    raise IntentNotFound(intent_id)
                current = IntentStatus(row["status"])
                if current == target:
                    connection.execute("COMMIT")
                    return IntentTransitionResult(intent_id, current, target, False)
                if current in TERMINAL_INTENT_STATUSES:
                    raise TerminalIntentError(
                        f"intent {intent_id} is terminal ({current}) and cannot move to {target}"
                    )
                if target not in ALLOWED_TRANSITIONS[current]:
                    raise InvalidTransition(f"invalid transition: {current} -> {target}")
                if row["broker_order_id"] and broker_order_id not in {
                    None,
                    row["broker_order_id"],
                }:
                    raise IdentityCollision("broker_order_id cannot be changed once attached")

                sequence = int(row["transition_count"]) + 1
                terminal_at = now if target in TERMINAL_INTENT_STATUSES else None
                connection.execute(
                    """
                    UPDATE intent_ledger
                    SET status = ?, reason_code = COALESCE(?, reason_code),
                        broker_order_id = COALESCE(?, broker_order_id),
                        updated_at = ?, terminal_at = COALESCE(?, terminal_at),
                        transition_count = ?
                    WHERE intent_id = ?
                    """,
                    (
                        target.value,
                        reason_code,
                        broker_order_id,
                        now,
                        terminal_at,
                        sequence,
                        intent_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO intent_transition (
                        intent_id, sequence, from_status, to_status, reason_code, occurred_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (intent_id, sequence, current.value, target.value, reason_code, now),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return IntentTransitionResult(intent_id, current, target, True)

    def get_intent(self, intent_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM intent_ledger WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            return dict(row) if row else None

    def latest_intents(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM intent_ledger ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

    def start_trace(
        self,
        trace_id: str,
        *,
        stack: str,
        lane: str | None = None,
        symbol: str | None = None,
        correlation_id: str | None = None,
        build_sha: str | None = None,
        started_ns: int | None = None,
    ) -> None:
        started_ns = started_ns if started_ns is not None else time.time_ns()
        with self.connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO trace_summary (
                        trace_id, correlation_id, stack, lane, symbol, build_sha,
                        started_ns, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(trace_id),
                        correlation_id,
                        str(stack),
                        lane,
                        symbol.upper() if symbol else None,
                        build_sha,
                        int(started_ns),
                        _utc_now(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise TraceError(f"trace already exists: {trace_id}") from exc

    def append_trace_event(self, trace_id: str, event: TraceEvent) -> int:
        details = self._compact_details(event.details)
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                summary = connection.execute(
                    "SELECT terminal_result FROM trace_summary WHERE trace_id = ?",
                    (trace_id,),
                ).fetchone()
                if summary is None:
                    raise TraceError(f"unknown trace: {trace_id}")
                if summary["terminal_result"] is not None:
                    raise TraceError(f"trace is already finalized: {trace_id}")
                previous = connection.execute(
                    """
                    SELECT sequence, timestamp_ns FROM trace_event
                    WHERE trace_id = ? ORDER BY sequence DESC LIMIT 1
                    """,
                    (trace_id,),
                ).fetchone()
                sequence = int(previous["sequence"]) + 1 if previous else 0
                if sequence >= self.max_events_per_trace:
                    raise TraceError(
                        f"trace exceeds max_events_per_trace={self.max_events_per_trace}"
                    )
                if previous and event.timestamp_ns < int(previous["timestamp_ns"]):
                    raise TraceError("trace timestamps must be monotonic")
                delta_ms = (
                    (event.timestamp_ns - int(previous["timestamp_ns"])) / 1_000_000
                    if previous
                    else None
                )
                connection.execute(
                    """
                    INSERT INTO trace_event (
                        trace_id, sequence, stage, outcome, reason_code,
                        timestamp_ns, latency_from_previous_ms, details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trace_id,
                        sequence,
                        str(event.stage),
                        str(event.outcome),
                        event.reason_code,
                        int(event.timestamp_ns),
                        delta_ms,
                        details,
                    ),
                )
                connection.execute(
                    "UPDATE trace_summary SET event_count = ? WHERE trace_id = ?",
                    (sequence + 1, trace_id),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return sequence

    def finalize_trace(
        self,
        trace_id: str,
        terminal_result: TerminalResult | str,
        *,
        reason_code: str | None = None,
        intent_id: str | None = None,
        ended_ns: int | None = None,
    ) -> dict[str, Any]:
        terminal = TerminalResult(terminal_result)
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                summary = connection.execute(
                    "SELECT * FROM trace_summary WHERE trace_id = ?", (trace_id,)
                ).fetchone()
                if summary is None:
                    raise TraceError(f"unknown trace: {trace_id}")
                if summary["terminal_result"] is not None:
                    if summary["terminal_result"] == terminal.value:
                        connection.execute("COMMIT")
                        return dict(summary)
                    raise TraceError(
                        f"trace already finalized as {summary['terminal_result']}"
                    )
                events = connection.execute(
                    """
                    SELECT stage, timestamp_ns, latency_from_previous_ms
                    FROM trace_event WHERE trace_id = ? ORDER BY sequence
                    """,
                    (trace_id,),
                ).fetchall()
                if events:
                    last_ns = int(events[-1]["timestamp_ns"])
                    ended_ns = ended_ns if ended_ns is not None else last_ns
                    if ended_ns < last_ns:
                        raise TraceError("ended_ns cannot precede the final trace event")
                    first_ns = int(events[0]["timestamp_ns"])
                    total_latency_ms = (ended_ns - first_ns) / 1_000_000
                    bottleneck = max(
                        events[1:],
                        key=lambda row: float(row["latency_from_previous_ms"] or 0),
                        default=None,
                    )
                    bottleneck_stage = bottleneck["stage"] if bottleneck else events[0]["stage"]
                else:
                    ended_ns = ended_ns if ended_ns is not None else int(summary["started_ns"])
                    total_latency_ms = max(
                        0.0, (ended_ns - int(summary["started_ns"])) / 1_000_000
                    )
                    bottleneck_stage = None
                connection.execute(
                    """
                    UPDATE trace_summary
                    SET ended_ns = ?, terminal_result = ?, reason_code = ?, intent_id = ?,
                        total_latency_ms = ?, bottleneck_stage = ?
                    WHERE trace_id = ?
                    """,
                    (
                        int(ended_ns),
                        terminal.value,
                        reason_code,
                        intent_id,
                        total_latency_ms,
                        bottleneck_stage,
                        trace_id,
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return self.get_trace(trace_id) or {}

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            summary = connection.execute(
                "SELECT * FROM trace_summary WHERE trace_id = ?", (trace_id,)
            ).fetchone()
            if summary is None:
                return None
            events = connection.execute(
                "SELECT * FROM trace_event WHERE trace_id = ? ORDER BY sequence",
                (trace_id,),
            ).fetchall()
        result = dict(summary)
        result["events"] = [
            {**dict(row), "details": json.loads(row["details_json"])} for row in events
        ]
        for event in result["events"]:
            event.pop("details_json", None)
        return result

    def diagnostics(self, *, window_hours: int = 24) -> dict[str, Any]:
        cutoff_ns = time.time_ns() - int(timedelta(hours=window_hours).total_seconds() * 1e9)
        with self.connection() as connection:
            status_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM intent_ledger GROUP BY status"
            ).fetchall()
            result_rows = connection.execute(
                """
                SELECT terminal_result, COUNT(*) AS count
                FROM trace_summary
                WHERE started_ns >= ? AND terminal_result IS NOT NULL
                GROUP BY terminal_result
                """,
                (cutoff_ns,),
            ).fetchall()
            latencies = [
                float(row["total_latency_ms"])
                for row in connection.execute(
                    """
                    SELECT total_latency_ms FROM trace_summary
                    WHERE started_ns >= ? AND total_latency_ms IS NOT NULL
                    """,
                    (cutoff_ns,),
                ).fetchall()
            ]
            open_traces = connection.execute(
                "SELECT COUNT(*) FROM trace_summary WHERE terminal_result IS NULL"
            ).fetchone()[0]
        storage_bytes = self.path.stat().st_size if self.path.exists() else 0
        for suffix in ("-wal", "-shm"):
            companion = Path(str(self.path) + suffix)
            if companion.exists():
                storage_bytes += companion.stat().st_size
        return {
            "window_hours": window_hours,
            "intents_by_status": {row["status"]: row["count"] for row in status_rows},
            "terminal_results": {
                row["terminal_result"]: row["count"] for row in result_rows
            },
            "open_traces": open_traces,
            "latency_ms": {
                "samples": len(latencies),
                "p50": _percentile(latencies, 0.50),
                "p95": _percentile(latencies, 0.95),
                "max": round(max(latencies), 3) if latencies else None,
            },
            "storage_bytes": storage_bytes,
        }

    def prune(
        self,
        *,
        event_days: int = 30,
        summary_days: int = 180,
        transition_days: int = 180,
        dedupe_days: int = 365,
    ) -> dict[str, int]:
        if not (0 < event_days <= summary_days <= dedupe_days):
            raise ValueError("retention must satisfy 0 < event_days <= summary_days <= dedupe_days")
        now = datetime.now(UTC)
        event_cutoff_ns = int((now - timedelta(days=event_days)).timestamp() * 1e9)
        summary_cutoff_ns = int((now - timedelta(days=summary_days)).timestamp() * 1e9)
        transition_cutoff = (now - timedelta(days=transition_days)).isoformat()
        dedupe_cutoff = (now - timedelta(days=dedupe_days)).isoformat()

        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                event_count = connection.execute(
                    """
                    DELETE FROM trace_event
                    WHERE trace_id IN (
                        SELECT trace_id FROM trace_summary
                        WHERE ended_ns IS NOT NULL AND ended_ns < ?
                    )
                    """,
                    (event_cutoff_ns,),
                ).rowcount
                summary_count = connection.execute(
                    """
                    DELETE FROM trace_summary
                    WHERE ended_ns IS NOT NULL AND ended_ns < ?
                    """,
                    (summary_cutoff_ns,),
                ).rowcount
                transition_count = connection.execute(
                    """
                    DELETE FROM intent_transition
                    WHERE occurred_at < ? AND intent_id IN (
                        SELECT intent_id FROM intent_ledger
                        WHERE status IN ('terminal', 'rejected', 'expired')
                    )
                    """,
                    (transition_cutoff,),
                ).rowcount
                intent_count = connection.execute(
                    """
                    DELETE FROM intent_ledger
                    WHERE status IN ('terminal', 'rejected', 'expired')
                      AND terminal_at IS NOT NULL AND terminal_at < ?
                    """,
                    (dedupe_cutoff,),
                ).rowcount
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return {
            "trace_events_deleted": event_count,
            "trace_summaries_deleted": summary_count,
            "intent_transitions_deleted": transition_count,
            "intent_identities_deleted": intent_count,
        }

    @staticmethod
    def _compact_details(details: Mapping[str, Any]) -> str:
        encoded = json.dumps(details, sort_keys=True, separators=(",", ":"), default=str)
        if len(encoded.encode("utf-8")) <= 2048:
            return encoded
        import hashlib

        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        return json.dumps(
            {"details_omitted": True, "sha256": digest, "original_bytes": len(encoded)},
            separators=(",", ":"),
        )

    def export_snapshot(self, *, intent_limit: int = 100) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "generated_at": _utc_now(),
            "diagnostics": self.diagnostics(),
            "latest_intents": self.latest_intents(intent_limit),
        }
