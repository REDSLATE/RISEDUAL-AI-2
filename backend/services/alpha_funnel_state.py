"""alpha_funnel_state — promotable candidate registry.

Fluid state machine per operator design:

    DISCOVERED  →  RESEARCH  ↔  WATCH  →  ARMED  →  ACTIONABLE  →  EXECUTED
                     ↑           ↑         ↑
                     └───────────┴─────────┘   (backward transitions allowed)

Any state → REJECTED (terminal for this cycle).
Strengthening candidates can be promoted; deteriorating candidates
demoted, so a #11 that strengthens can overtake a #2 that weakens.

Persistence
-----------
In-memory dict is the fast working state (thread-safe with a
module lock). A bounded SQLite mirror in the hot store survives
restarts, but restored candidates come back only as WATCH or
RESEARCH — NEVER as ACTIONABLE, because they must be revalidated
against fresh market/broker data before promotion.

The rank-change log
-------------------
Every score adjustment carries (delta, reason). Recent history is
kept per candidate so an operator can inspect WHY the ranking
changed — the missing piece from earlier Alpha versions.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── State machine ──────────────────────────────────────────────

class CandidateState:
    DISCOVERED = "DISCOVERED"
    RESEARCH   = "RESEARCH"
    WATCH      = "WATCH"
    ARMED      = "ARMED"
    ACTIONABLE = "ACTIONABLE"
    EXECUTED   = "EXECUTED"
    REJECTED   = "REJECTED"


# States that survive a restart. ACTIONABLE and EXECUTED are
# session-scoped by design; restored candidates must be
# revalidated before they can execute.
_RESTORABLE_STATES: frozenset[str] = frozenset({
    CandidateState.DISCOVERED,
    CandidateState.RESEARCH,
    CandidateState.WATCH,
    CandidateState.ARMED,
})


@dataclass
class Candidate:
    symbol: str
    state: str = CandidateState.DISCOVERED
    initial_score: float = 0.0
    current_score: float = 0.0
    score_history: list[dict] = field(default_factory=list)  # {delta, reason, at_ns, from_state, to_state}
    last_broker_snapshot: Optional[dict] = None
    rejection_reason: Optional[str] = None
    updated_at_ns: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ── State manager ──────────────────────────────────────────────

_LOCK = threading.Lock()
_STATE: dict[str, Candidate] = {}   # symbol → Candidate


def _now_ns() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1e9)


def _ensure_schema() -> None:
    from services import alpha_hot_store
    alpha_hot_store.init()
    with alpha_hot_store._connect() as con:  # noqa: SLF001
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS alpha_funnel_candidates (
                symbol             TEXT PRIMARY KEY,
                state              TEXT NOT NULL,
                initial_score      REAL,
                current_score      REAL,
                score_history      TEXT NOT NULL,
                last_broker_snap   TEXT,
                rejection_reason   TEXT,
                updated_at_ns      INTEGER NOT NULL
            )
            """
        )


def clear() -> None:
    """Wipe in-memory state (test / rollover helper)."""
    with _LOCK:
        _STATE.clear()


def get_all() -> list[Candidate]:
    with _LOCK:
        return [Candidate(**c.to_dict()) for c in _STATE.values()]


def get_by_state(state: str) -> list[Candidate]:
    with _LOCK:
        return [
            Candidate(**c.to_dict()) for c in _STATE.values()
            if c.state == state
        ]


def get(symbol: str) -> Optional[Candidate]:
    sym = symbol.upper()
    with _LOCK:
        c = _STATE.get(sym)
        return Candidate(**c.to_dict()) if c else None


# ── Ingestion + transitions ─────────────────────────────────────

def ingest_discovery(discovered: list[dict], *, max_n: int = 40) -> list[str]:
    """Merge a discovery batch into the funnel.

    Each entry must have ``{"symbol": str, "score": float}``.

    Rules:
    * New symbols land at ``DISCOVERED`` with ``initial_score=score``.
    * Existing symbols already in the funnel keep their state;
      their ``current_score`` is updated to reflect the new
      discovery score (delta logged with reason=re-discovered).
    * ``max_n`` caps how many NEW symbols we accept; existing
      candidates are never displaced by the cap.
    * Operator rule: don't discard tail candidates before ranking.
      Callers pass their full ranked list; ``max_n`` only limits
      NEW additions to the funnel this cycle.

    Returns the list of symbols that were added or refreshed.
    """
    touched: list[str] = []
    added = 0
    with _LOCK:
        for entry in discovered or []:
            sym = str(entry.get("symbol") or "").upper()
            if not sym:
                continue
            score = float(entry.get("score") or 0.0)
            existing = _STATE.get(sym)
            if existing is None:
                if added >= max_n:
                    continue
                _STATE[sym] = Candidate(
                    symbol=sym,
                    state=CandidateState.DISCOVERED,
                    initial_score=score,
                    current_score=score,
                    updated_at_ns=_now_ns(),
                )
                added += 1
                touched.append(sym)
            else:
                # Refresh score; keep state.
                delta = round(score - existing.current_score, 4)
                existing.current_score = score
                existing.updated_at_ns = _now_ns()
                if abs(delta) >= 0.0001:
                    existing.score_history.append({
                        "delta": delta,
                        "reason": "rediscovered",
                        "at_ns": existing.updated_at_ns,
                        "from_state": existing.state,
                        "to_state": existing.state,
                    })
                touched.append(sym)
    return touched


def transition(
    symbol: str, *, to_state: str, delta: float = 0.0, reason: str = "",
) -> bool:
    """Promote or demote a candidate. Returns True on success.

    Any state can move to any other state — the caller is
    responsible for enforcing the allowed transitions (that's
    the funnel orchestrator's job). This function's role is
    bookkeeping: apply the delta, log the reason.
    """
    sym = symbol.upper()
    with _LOCK:
        c = _STATE.get(sym)
        if c is None:
            return False
        from_state = c.state
        c.state = to_state
        c.current_score = round(c.current_score + delta, 4)
        c.updated_at_ns = _now_ns()
        if delta or reason or from_state != to_state:
            c.score_history.append({
                "delta": round(delta, 4),
                "reason": reason,
                "at_ns": c.updated_at_ns,
                "from_state": from_state,
                "to_state": to_state,
            })
            # Cap history to the last 40 entries — bounded memory.
            if len(c.score_history) > 40:
                c.score_history = c.score_history[-40:]
        if to_state == CandidateState.REJECTED and reason:
            c.rejection_reason = reason
        return True


def attach_broker_snapshot(symbol: str, snap_dict: dict) -> None:
    with _LOCK:
        c = _STATE.get(symbol.upper())
        if c:
            c.last_broker_snapshot = snap_dict


def rank_candidates(state: Optional[str] = None) -> list[Candidate]:
    """Return live-ranked candidates. When ``state`` is given,
    restricts the ranking to that state (e.g. rank ARMED)."""
    all_ = get_all()
    if state:
        all_ = [c for c in all_ if c.state == state]
    return sorted(all_, key=lambda c: c.current_score, reverse=True)


# ── SQLite persistence ─────────────────────────────────────────

def persist_to_sqlite() -> None:
    """Snapshot the in-memory state to SQLite. Best-effort."""
    _ensure_schema()
    from services import alpha_hot_store
    try:
        with alpha_hot_store._connect() as con:  # noqa: SLF001
            con.execute("DELETE FROM alpha_funnel_candidates")
            with _LOCK:
                for c in _STATE.values():
                    con.execute(
                        "INSERT INTO alpha_funnel_candidates "
                        "(symbol, state, initial_score, current_score, "
                        "score_history, last_broker_snap, rejection_reason, "
                        "updated_at_ns) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            c.symbol, c.state, c.initial_score, c.current_score,
                            json.dumps(c.score_history, default=str),
                            json.dumps(c.last_broker_snapshot, default=str) if c.last_broker_snapshot else None,
                            c.rejection_reason,
                            c.updated_at_ns,
                        ),
                    )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[funnel_state] persist failed: %s", exc)


def restore_from_sqlite() -> int:
    """Rehydrate in-memory state on process boot. Candidates that
    were ARMED at persistence time come back as WATCH — they must
    be revalidated before they can advance again. ACTIONABLE /
    EXECUTED are NOT restored.

    Returns the number of candidates restored.
    """
    _ensure_schema()
    from services import alpha_hot_store
    n = 0
    try:
        with alpha_hot_store._connect() as con:  # noqa: SLF001
            rows = con.execute(
                "SELECT * FROM alpha_funnel_candidates"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[funnel_state] restore read failed: %s", exc)
        return 0
    with _LOCK:
        for r in rows:
            state = r["state"]
            if state not in _RESTORABLE_STATES:
                continue
            # Downgrade ARMED to WATCH — no free promotions across restart.
            if state == CandidateState.ARMED:
                state = CandidateState.WATCH
            try:
                history = json.loads(r["score_history"] or "[]")
            except (TypeError, ValueError):
                history = []
            snap = None
            if r["last_broker_snap"]:
                try:
                    snap = json.loads(r["last_broker_snap"])
                except (TypeError, ValueError):
                    snap = None
            _STATE[r["symbol"]] = Candidate(
                symbol=r["symbol"],
                state=state,
                initial_score=float(r["initial_score"] or 0),
                current_score=float(r["current_score"] or 0),
                score_history=history,
                last_broker_snapshot=snap,
                rejection_reason=r["rejection_reason"],
                updated_at_ns=int(r["updated_at_ns"] or 0),
            )
            n += 1
    logger.info("[funnel_state] restored %d candidates (ARMED→WATCH downgrade applied)", n)
    return n


__all__ = [
    "Candidate", "CandidateState",
    "ingest_discovery", "transition", "attach_broker_snapshot",
    "get_all", "get_by_state", "get", "rank_candidates",
    "persist_to_sqlite", "restore_from_sqlite", "clear",
]
