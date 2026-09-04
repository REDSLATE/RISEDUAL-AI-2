"""alpha_discernment_postmortem — same-session postmortem analysis.

Purpose
-------
Given a session date and a set of trade selections, this module

1. Reconstructs feature state AT DETECTION and AT TRIGGER for each
   selection by replaying the ``lifecycle_events`` payloads stored
   in the SQLite hot store.
2. Joins each setup with its resolved outcome (MFE, MAE, realized R,
   fills, latencies) from the ``alpha_outcomes`` Mongo collection.
3. Computes seven candidate discernment features designed to
   surface WHY some setups outperformed others:

   * move maturity / overextension
   * fading volume after the initial move
   * momentum deceleration
   * relative strength (vs SPY / QQQ when available in payload)
   * regime-pattern mismatch
   * late trigger (detection → trigger elapsed vs typical)
   * poor reward-remaining vs risk-to-invalidation ratio

4. Compares the operator-labelled "better" group against the
   "poorer" group across every feature — mean delta, direction,
   sample size, notes — and hands the structured comparison to
   Claude Opus 4.8 for narrative synthesis.
5. Persists the per-setup feature rows to a new SQLite table
   (``alpha_discernment_features``) and the session-level
   comparison + narrative to (``alpha_discernment_postmortems``),
   both in the same hot-store DB. Mongo is not touched.

Explicit non-goals
------------------
* NO ticker-specific rules are ever written. Symbols in the input
  are analysed only to source features; the persisted output keys
  are the features themselves, never the symbol names.
* NO trading gate is introduced from this data. The output is
  meant to seed the Trade Discernment Layer's candidate feature
  set — the learning happens over the next 30-50 independent
  selections, not over these four.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import statistics
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as _dt_time, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  SQLite schema (created lazily on first use)
# ─────────────────────────────────────────────

def _ensure_schema() -> None:
    """Create the discernment tables if they don't exist. Shares the
    same DB file as the rest of the hot store."""
    from services import alpha_hot_store
    alpha_hot_store.init()
    with alpha_hot_store._connect() as con:  # noqa: SLF001 — same-package access
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS alpha_discernment_features (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                session_date           TEXT NOT NULL,
                symbol                 TEXT NOT NULL,
                setup_id               TEXT NOT NULL,
                setup_type             TEXT,
                group_label            TEXT,           -- 'better' | 'poorer' | 'unlabeled'
                features_at_detection  TEXT NOT NULL,  -- JSON
                features_at_trigger    TEXT NOT NULL,  -- JSON
                outcome                TEXT NOT NULL,  -- JSON (mfe/mae/r/fill)
                discriminators         TEXT NOT NULL,  -- JSON (7 candidate features)
                notes                  TEXT,
                created_at             INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_disc_feat_session ON alpha_discernment_features(session_date);
            CREATE INDEX IF NOT EXISTS ix_disc_feat_symbol  ON alpha_discernment_features(symbol);

            CREATE TABLE IF NOT EXISTS alpha_discernment_postmortems (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                session_date   TEXT NOT NULL,
                better_group   TEXT NOT NULL,           -- JSON list of symbols
                poorer_group   TEXT NOT NULL,           -- JSON list of symbols
                comparison     TEXT NOT NULL,           -- JSON dict of feature deltas
                narrative      TEXT,                    -- Claude Opus 4.8 synthesis
                model_used     TEXT,
                warnings       TEXT,                    -- JSON list of caveats
                created_at     INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_disc_pm_session ON alpha_discernment_postmortems(session_date);
            """
        )


# ─────────────────────────────────────────────
#  Data model
# ─────────────────────────────────────────────

@dataclass
class SetupSnapshot:
    """Per-setup feature snapshot reconstructed from the hot store."""
    symbol: str
    setup_id: str
    setup_type: Optional[str]
    features_at_detection: dict[str, Any] = field(default_factory=dict)
    features_at_trigger: dict[str, Any] = field(default_factory=dict)
    outcome: dict[str, Any] = field(default_factory=dict)
    detected_ts_ns: Optional[int] = None
    triggered_ts_ns: Optional[int] = None
    notes: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────
#  Session-window helper
# ─────────────────────────────────────────────

def _session_window_ns(session_date_iso: str) -> tuple[int, int]:
    """Return (start_ns, end_ns) covering a full RTH session date in
    US/Eastern, expanded to catch pre-market and after-hours
    lifecycle rows (04:00 → 20:00 ET). Falls back to a 24h UTC
    window when zoneinfo isn't available.
    """
    d = date.fromisoformat(session_date_iso)
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        start = datetime.combine(d, _dt_time(4, 0), tzinfo=et)
        end = datetime.combine(d, _dt_time(20, 0), tzinfo=et)
    except Exception:  # noqa: BLE001
        start = datetime.combine(d, _dt_time(0, 0), tzinfo=timezone.utc)
        end = start + timedelta(days=1)
    return int(start.timestamp() * 1e9), int(end.timestamp() * 1e9)


# ─────────────────────────────────────────────
#  Extract per-symbol observations from hot store
# ─────────────────────────────────────────────

def _fetch_hot_store_events(
    *, symbols: list[str], start_ns: int, end_ns: int,
) -> dict[str, list[dict]]:
    """Return {symbol: [{event, stage, payload_dict, ts_ns, setup_id}, ...]}
    for all lifecycle rows in the window whose payload's ``symbol``
    (or explicit column) matches one of the requested symbols. Reads
    ONLY from the SQLite hot store — Mongo is not queried here.
    """
    from services import alpha_hot_store
    alpha_hot_store.init()
    upper = {s.upper() for s in symbols}
    out: dict[str, list[dict]] = {s: [] for s in upper}
    with alpha_hot_store._connect() as con:  # noqa: SLF001
        rows = con.execute(
            "SELECT setup_id, symbol, event, stage, payload, ts_ns "
            "FROM lifecycle_events "
            "WHERE ts_ns BETWEEN ? AND ? "
            "ORDER BY ts_ns ASC",
            (start_ns, end_ns),
        ).fetchall()
    for r in rows:
        try:
            payload = json.loads(r["payload"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        symbol = (r["symbol"] or payload.get("symbol") or "").upper()
        if symbol not in upper:
            continue
        out[symbol].append({
            "setup_id": r["setup_id"],
            "symbol": symbol,
            "event": r["event"],
            "stage": r["stage"],
            "payload": payload,
            "ts_ns": int(r["ts_ns"]),
        })
    return out


def _pick_first(events: list[dict], event_name: str) -> Optional[dict]:
    for e in events:
        if e.get("event") == event_name:
            return e
    return None


def _pick_last(events: list[dict], event_name: str) -> Optional[dict]:
    match = [e for e in events if e.get("event") == event_name]
    return match[-1] if match else None


def _reconstruct_setup_snapshot(
    *, symbol: str, events: list[dict],
) -> Optional[SetupSnapshot]:
    """Build a SetupSnapshot from a symbol's ordered lifecycle rows.

    Looks for the canonical events emitted by ``alpha_day_trader``:

    * ``setup_detected`` — features at detection (opp_score,
      setup_score, regime labels, price, invalidation, trigger).
    * ``triggered``       — features at trigger (confirmation price,
      current price).
    * ``no_pattern_match`` — pre-pattern feature dump the aggregator
      also uses; used only for context when the detection event is
      missing.
    """
    detected = _pick_first(events, "setup_detected")
    triggered = _pick_last(events, "triggered")
    if detected is None:
        # No detection observation → nothing meaningful to reconstruct.
        return None
    setup_id = detected.get("setup_id") or ""
    d_payload = detected.get("payload") or {}
    t_payload = (triggered or {}).get("payload") or {}
    snap = SetupSnapshot(
        symbol=symbol,
        setup_id=setup_id,
        setup_type=d_payload.get("setup_type") or d_payload.get("stage"),
        features_at_detection=d_payload,
        features_at_trigger=t_payload,
        detected_ts_ns=detected.get("ts_ns"),
        triggered_ts_ns=(triggered or {}).get("ts_ns"),
    )
    if triggered is None:
        snap.notes.append("no_trigger_observation")
    return snap


# ─────────────────────────────────────────────
#  Join with Mongo outcome (MFE / MAE / R)
# ─────────────────────────────────────────────

async def _attach_outcome(db: Any, snap: SetupSnapshot) -> None:
    """Best-effort lookup in ``alpha_outcomes`` — fills in
    outcome.{filled, mfe, mae, realized_r, reject_reason} when
    resolved. Adds a note when the join fails so the postmortem
    reports the gap instead of silently pretending.
    """
    if db is None or not snap.setup_id:
        snap.notes.append("outcome_lookup_skipped_no_db_or_setup_id")
        return
    try:
        doc = await db.alpha_outcomes.find_one({"setup_id": snap.setup_id})
    except Exception as exc:  # noqa: BLE001
        snap.notes.append(f"outcome_lookup_failed:{exc!s:.80}")
        return
    if doc is None:
        snap.notes.append("no_outcome_row_yet")
        return
    doc.pop("_id", None)
    ca = doc.get("created_at")
    if isinstance(ca, datetime):
        doc["created_at"] = ca.isoformat()
    snap.outcome = doc


# ─────────────────────────────────────────────
#  Seven candidate discriminators
# ─────────────────────────────────────────────

def _safe_num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _compute_discriminators(snap: SetupSnapshot) -> dict[str, Any]:
    """Return the seven candidate features. Every field is
    Optional[float]; missing features are ``None`` and never
    fabricated. The idea is to let the downstream comparator see
    the *shape* of the missing-data problem — we do not pretend
    to know values we can't measure.
    """
    d = snap.features_at_detection or {}
    t = snap.features_at_trigger or {}
    out: dict[str, Any] = {}

    # 1) Move maturity / overextension.
    # Use pct_change at detection as the completed move; a large
    # already-completed move at detection is by definition "late".
    out["move_maturity_pct"] = _safe_num(
        d.get("pct_change") or t.get("pct_change")
    )

    # 2) Fading volume after the initial move — RVOL delta from
    # detection to trigger. Negative = fading; positive = fresh push.
    d_rvol = _safe_num(d.get("relative_volume"))
    t_rvol = _safe_num(t.get("relative_volume"))
    out["rvol_delta_detection_to_trigger"] = (
        None if d_rvol is None or t_rvol is None else round(t_rvol - d_rvol, 4)
    )

    # 3) Momentum deceleration — price delta from the setup
    # confirmation to the actual trigger fire. If the setup lost
    # ground between detection and trigger, we entered late.
    d_price = _safe_num(d.get("price"))
    t_price = _safe_num(t.get("price"))
    out["price_delta_pct_detection_to_trigger"] = (
        None
        if d_price in (None, 0.0) or t_price is None
        else round(((t_price - d_price) / d_price) * 100.0, 4)
    )

    # 4) Relative strength — mirrored from any RS tags the detection
    # payload carried. Not universally populated; graceful ``None``
    # when absent.
    out["relative_strength_vs_spy"] = _safe_num(
        d.get("rs_spy") or d.get("relative_strength_spy")
    )
    out["relative_strength_vs_qqq"] = _safe_num(
        d.get("rs_qqq") or d.get("relative_strength_qqq")
    )

    # 5) Regime-pattern mismatch — a boolean-scored 0/1. A
    # momentum pattern (breakout/vwap_reclaim/etc.) firing in a
    # ``chop`` slow regime is a mismatch; a mean-revert pattern
    # in a ``trend_up`` slow regime is also a mismatch.
    mismatch = _regime_pattern_mismatch(
        setup_type=(snap.setup_type or "").lower(),
        slow_regime=(d.get("slow_regime") or "").lower(),
        fast_regime=(d.get("fast_regime") or "").lower(),
    )
    out["regime_pattern_mismatch"] = mismatch  # 0.0 / 1.0 / None

    # 6) Late trigger — detection→trigger elapsed in milliseconds.
    if snap.detected_ts_ns and snap.triggered_ts_ns:
        out["detect_to_trigger_ms"] = max(
            0, (snap.triggered_ts_ns - snap.detected_ts_ns) // 1_000_000,
        )
    else:
        out["detect_to_trigger_ms"] = None

    # 7) Reward remaining / risk to invalidation — computed against
    # the trigger price using the setup's own target and
    # invalidation levels.
    outcome = snap.outcome or {}
    trigger_p = _safe_num(outcome.get("trigger_price") or d.get("trigger_price"))
    invalid_p = _safe_num(
        outcome.get("invalidation_price") or d.get("invalidation_price")
    )
    target_p = _safe_num(outcome.get("target_price") or d.get("target_price"))
    if trigger_p and invalid_p and target_p:
        risk = abs(trigger_p - invalid_p)
        reward = abs(target_p - trigger_p)
        out["reward_over_risk"] = round(reward / risk, 4) if risk > 0 else None
    else:
        out["reward_over_risk"] = None

    return out


def _regime_pattern_mismatch(
    *, setup_type: str, slow_regime: str, fast_regime: str,
) -> Optional[float]:
    """Return 1.0 when the pattern family fights the regime,
    0.0 when aligned or neutral, and ``None`` when we can't
    classify (unknown pattern or unknown regime).
    """
    from services.alpha_day_trader import (
        MEAN_REVERT_PATTERNS, MOMENTUM_PATTERNS,
    )
    if not setup_type:
        return None
    is_momo = setup_type in MOMENTUM_PATTERNS
    is_mean_revert = setup_type in MEAN_REVERT_PATTERNS
    if not (is_momo or is_mean_revert):
        return None
    slow = (slow_regime or "").lower()
    if not slow or slow == "unknown":
        return None
    # Chop / mean-revert regime tokens.
    chop_hints = ("chop", "meanrevert", "range")
    is_chop_regime = any(h in slow for h in chop_hints)
    # Trend regime tokens.
    trend_hints = ("trend_up", "trend_down", "trending")
    is_trend_regime = any(h in slow for h in trend_hints)
    if is_momo and is_chop_regime:
        return 1.0
    if is_mean_revert and is_trend_regime:
        return 1.0
    return 0.0


# ─────────────────────────────────────────────
#  Group comparison
# ─────────────────────────────────────────────

def _compare_groups(
    *, better: list[SetupSnapshot], poorer: list[SetupSnapshot],
) -> dict[str, Any]:
    """Compute per-feature mean deltas between the operator-labelled
    better and poorer groups. Only features with at least one
    non-None value in each group get a delta; the rest are noted
    as ``insufficient_data``.
    """
    def _dict(g: list[SetupSnapshot]) -> dict[str, list[float]]:
        acc: dict[str, list[float]] = {}
        for s in g:
            for k, v in (s.__disc__ if hasattr(s, "__disc__") else {}).items():  # type: ignore[attr-defined]
                if isinstance(v, (int, float)):
                    acc.setdefault(k, []).append(float(v))
        return acc

    better_vals = _dict(better)
    poorer_vals = _dict(poorer)
    all_keys = sorted(set(better_vals) | set(poorer_vals))

    comparison: dict[str, Any] = {
        "better_n": len(better),
        "poorer_n": len(poorer),
        "features": {},
    }
    for k in all_keys:
        b = better_vals.get(k) or []
        p = poorer_vals.get(k) or []
        entry: dict[str, Any] = {
            "better_sample_size": len(b),
            "poorer_sample_size": len(p),
        }
        if b:
            entry["better_mean"] = round(statistics.mean(b), 4)
        if p:
            entry["poorer_mean"] = round(statistics.mean(p), 4)
        if b and p:
            delta = statistics.mean(b) - statistics.mean(p)
            entry["mean_delta"] = round(delta, 4)
            entry["direction"] = "better_higher" if delta > 0 else (
                "better_lower" if delta < 0 else "equal"
            )
        else:
            entry["note"] = "insufficient_data"
        comparison["features"][k] = entry
    return comparison


# ─────────────────────────────────────────────
#  Claude Opus 4.8 narrative synthesis
# ─────────────────────────────────────────────

async def _synthesize_narrative(comparison: dict[str, Any], *, warnings: list[str]) -> tuple[str, str]:
    """Ask Claude Opus 4.8 for a plain-English WHY. Returns
    (narrative, model_used). Falls back to a generated placeholder
    when the LLM key is missing or the call errors — the postmortem
    still lands.
    """
    api_key = os.environ.get("EMERGENT_LLM_KEY") or ""
    if not api_key:
        return (
            "LLM narrative skipped: EMERGENT_LLM_KEY not configured. "
            "Raw comparison JSON is the source of truth.",
            "none",
        )

    system_prompt = (
        "You are the Trade Discernment Layer's postmortem analyst for "
        "an equity day-trading system. You will be handed a feature-by-"
        "feature comparison between an operator-labelled 'better outcome' "
        "group and a 'poorer outcome' group of setups from a single "
        "session.\n\n"
        "Rules:\n"
        "1. NEVER produce ticker-specific rules like 'avoid X' or "
        "'prefer Y'. The symbols are provided only for grouping and "
        "MUST NOT appear in your conclusions.\n"
        "2. NEVER recommend a new hard trading gate from a sample of "
        "four setups. This is candidate-feature identification, not "
        "policy design.\n"
        "3. State a hypothesis in terms of the FEATURES that separated "
        "the two groups. Use words like 'candidate discriminator', "
        "'consistent with', 'preliminary'. Flag features with "
        "insufficient sample size (e.g. any missing values) as "
        "unreliable in this run.\n"
        "4. Rank the seven candidate discriminators (move maturity, "
        "fading volume, momentum deceleration, relative strength, "
        "regime-pattern mismatch, late trigger, reward-over-risk) by "
        "how well the observed delta separated the two groups.\n"
        "5. Return terse prose. No headings. No emojis. No opinions on "
        "the market. Just the analysis.\n"
    )
    user_prompt = (
        "Comparison JSON:\n" + json.dumps(comparison, indent=2)
        + "\n\nCaveats to weave in:\n- " + "\n- ".join(warnings or ["none"])
    )

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        chat = LlmChat(
            api_key=api_key,
            session_id=f"discernment-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            system_message=system_prompt,
        ).with_model("anthropic", "claude-opus-4-8")
        resp = await chat.send_message(UserMessage(text=user_prompt))
        text = resp if isinstance(resp, str) else str(resp)
        return text.strip(), "claude-opus-4-8"
    except Exception as exc:  # noqa: BLE001
        logger.warning("[discernment] Claude narrative failed: %s", exc)
        return (
            f"LLM narrative failed: {exc!s}. Raw comparison JSON is "
            f"the source of truth for this postmortem.",
            "error",
        )


# ─────────────────────────────────────────────
#  Public entry point
# ─────────────────────────────────────────────

async def run_postmortem(
    db: Any,
    *,
    session_date: str,
    better_group: list[str],
    poorer_group: list[str],
    invoke_llm: bool = True,
) -> dict[str, Any]:
    """Run the full pipeline and persist to SQLite. Returns the
    payload that also lives in ``alpha_discernment_postmortems``.

    ``session_date`` — ISO date string (e.g. ``"2026-09-02"``).
    ``better_group`` / ``poorer_group`` — operator-labelled symbol
    lists. Grouping determines only how the deltas are computed; no
    ticker-specific rule is ever persisted.
    """
    _ensure_schema()
    warnings: list[str] = []

    all_symbols = sorted({s.upper() for s in (better_group + poorer_group)})
    start_ns, end_ns = _session_window_ns(session_date)
    events_by_symbol = _fetch_hot_store_events(
        symbols=all_symbols, start_ns=start_ns, end_ns=end_ns,
    )
    snapshots: dict[str, SetupSnapshot] = {}
    for sym in all_symbols:
        snap = _reconstruct_setup_snapshot(
            symbol=sym, events=events_by_symbol.get(sym, []),
        )
        if snap is None:
            warnings.append(f"{sym}: no setup_detected event in session window")
            continue
        await _attach_outcome(db, snap)
        snap.__disc__ = _compute_discriminators(snap)  # type: ignore[attr-defined]
        snapshots[sym] = snap

    better_snaps = [snapshots[s.upper()] for s in better_group if s.upper() in snapshots]
    poorer_snaps = [snapshots[s.upper()] for s in poorer_group if s.upper() in snapshots]
    if not better_snaps or not poorer_snaps:
        warnings.append(
            "one or both groups have zero reconstructable setups; "
            "delta computation will be sparse"
        )

    comparison = _compare_groups(better=better_snaps, poorer=poorer_snaps)

    narrative = ""
    model_used = "none"
    if invoke_llm:
        narrative, model_used = await _synthesize_narrative(comparison, warnings=warnings)

    # Persist per-setup feature rows.
    now_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
    from services import alpha_hot_store
    with alpha_hot_store._connect() as con:  # noqa: SLF001
        for sym, snap in snapshots.items():
            group_label = "unlabeled"
            if sym in {s.upper() for s in better_group}:
                group_label = "better"
            elif sym in {s.upper() for s in poorer_group}:
                group_label = "poorer"
            con.execute(
                "INSERT INTO alpha_discernment_features "
                "(session_date, symbol, setup_id, setup_type, group_label, "
                "features_at_detection, features_at_trigger, outcome, "
                "discriminators, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_date, sym, snap.setup_id, snap.setup_type,
                    group_label,
                    json.dumps(snap.features_at_detection or {}, default=str),
                    json.dumps(snap.features_at_trigger or {}, default=str),
                    json.dumps(snap.outcome or {}, default=str),
                    json.dumps(snap.__disc__ or {}, default=str),  # type: ignore[attr-defined]
                    json.dumps(snap.notes or []),
                    now_ns,
                ),
            )
        con.execute(
            "INSERT INTO alpha_discernment_postmortems "
            "(session_date, better_group, poorer_group, comparison, "
            "narrative, model_used, warnings, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_date,
                json.dumps([s.upper() for s in better_group]),
                json.dumps([s.upper() for s in poorer_group]),
                json.dumps(comparison, default=str),
                narrative,
                model_used,
                json.dumps(warnings),
                now_ns,
            ),
        )

    return {
        "session_date": session_date,
        "better_group": [s.upper() for s in better_group],
        "poorer_group": [s.upper() for s in poorer_group],
        "reconstructable": sorted(snapshots.keys()),
        "per_setup": {
            s.symbol: {
                "setup_id": s.setup_id,
                "setup_type": s.setup_type,
                "discriminators": s.__disc__,  # type: ignore[attr-defined]
                "outcome_available": bool(s.outcome),
                "notes": s.notes,
            }
            for s in snapshots.values()
        },
        "comparison": comparison,
        "narrative": narrative,
        "model_used": model_used,
        "warnings": warnings,
    }


def get_latest_postmortem(session_date: str) -> Optional[dict[str, Any]]:
    """Read helper — return the most recent postmortem row for a
    session date, or None if none exists."""
    _ensure_schema()
    from services import alpha_hot_store
    with alpha_hot_store._connect() as con:  # noqa: SLF001
        row = con.execute(
            "SELECT * FROM alpha_discernment_postmortems "
            "WHERE session_date=? ORDER BY created_at DESC LIMIT 1",
            (session_date,),
        ).fetchone()
    if not row:
        return None
    return {
        "session_date": row["session_date"],
        "better_group": json.loads(row["better_group"] or "[]"),
        "poorer_group": json.loads(row["poorer_group"] or "[]"),
        "comparison": json.loads(row["comparison"] or "{}"),
        "narrative": row["narrative"] or "",
        "model_used": row["model_used"] or "none",
        "warnings": json.loads(row["warnings"] or "[]"),
        "created_at_ns": int(row["created_at"]),
    }


__all__ = [
    "run_postmortem",
    "get_latest_postmortem",
    "_compute_discriminators",
    "_compare_groups",
    "_regime_pattern_mismatch",
]
