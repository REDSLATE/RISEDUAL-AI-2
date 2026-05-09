"""Probes + classifiers for the alpha-retrain join diagnostic.

Strangler-split out of ``scripts/diagnose_alpha_retrain_join``
(2026-05-09) to keep the runnable script under its 400-line
preferred ceiling. Lives under ``services/diagnostics/`` because
it's a library — not runnable on its own. Same authority-boundary
fence applies: read-only, no broker / executor / pipeline imports,
no env mutation, no Mongo writes.

Holds:
  * pure helpers (symbol field detect, lane inference, candidate
    generation, safe-field projection)
  * collection / decision-log probes
  * match-attempt walker
  * hypothesis classifier
  * recommendation builder
  * dataclasses (``CollectionProbe``, ``DiagnosticResult``)
  * reason-string constants
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ── Constants ──────────────────────────────────────────────

SOURCE_TAG = "alpha_retrain_diagnostic"
SYMBOL_FIELDS = ("symbol", "ticker", "pair")
SAMPLES_PER_REASON = 3

REASON_NO_SYMBOL = "no_symbol_field"
REASON_NO_DECISION_LOG = "no_decision_log_in_window"
REASON_NORMALIZATION_MISS = "symbol_normalization_mismatch"
REASON_LANE_MISMATCH = "lane_mismatch"
REASON_DECISION_TOO_RECENT = "decision_log_post_dates_trade"
REASON_PNL_MISSING = "no_pnl_field"


# ── Pure helpers ───────────────────────────────────────────


def _safe_field(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Project a document to safe / printable fields only.

    Drops `_id`, large nested structures, bytes-like payloads.
    Used when surfacing sample unmatched rows so the diagnostic
    output stays readable + does not leak unbounded payloads.
    """
    safe: Dict[str, Any] = {}
    for k, v in (doc or {}).items():
        if k == "_id":
            continue
        if isinstance(v, dict):
            safe[k] = f"<dict keys={list(v.keys())[:6]}>"
            continue
        if isinstance(v, list):
            safe[k] = f"<list len={len(v)}>"
            continue
        if isinstance(v, bytes):
            safe[k] = f"<bytes {len(v)}b>"
            continue
        if isinstance(v, str) and len(v) > 80:
            safe[k] = v[:77] + "..."
        else:
            safe[k] = v
    return safe


def _detect_symbol_field(doc: Dict[str, Any]) -> Optional[str]:
    for f in SYMBOL_FIELDS:
        v = doc.get(f)
        if isinstance(v, str) and v.strip():
            return f
    return None


def _normalize_symbol(symbol: str, lane: str) -> str:
    """``str.upper().strip()`` — same as extract_rows in
    ``retrain_alpha_models``. ``lane`` is accepted for symmetry but
    no per-lane casing rules apply today.
    """
    return (symbol or "").upper().strip()


def _crypto_symbol_candidates(bare: str) -> List[str]:
    """Crypto-only: alpha_decision_log writes ``BTC-USD`` / ``ETH-USD``
    while crypto_paper_trades writes bare ``BTC`` / ``ETH``. Surface
    both so the diagnostic can quantify normalization-driven misses
    separately from real "no decision log" misses.
    """
    bare = bare.upper().strip()
    if not bare:
        return []
    return [bare, f"{bare}-USD", f"{bare}/USD", f"{bare}USD", f"{bare}USDT"]


def _trade_lane(coll: str, doc: Dict[str, Any]) -> str:
    """Derive the lane the trade belongs to.

    paper_trades         → "equity" (no lane field)
    crypto_paper_trades  → "crypto" (asset_class consistently "crypto")
    Falls back to whatever the doc's own ``lane`` field says.
    """
    lane = str(doc.get("lane") or "").lower()
    if lane in ("equity", "crypto"):
        return lane
    if coll == "crypto_paper_trades":
        return "crypto"
    if coll == "paper_trades":
        return "equity"
    return "equity"


def _trade_pnl(coll: str, doc: Dict[str, Any]) -> Optional[float]:
    """Locate the realized-pnl field for each collection schema."""
    for f in ("realized_pnl_usd", "pnl_usd", "pnl"):
        v = doc.get(f)
        if isinstance(v, (int, float)):
            return float(v)
    return None


# ── Dataclasses ───────────────────────────────────────────


@dataclass
class CollectionProbe:
    name: str
    total: int = 0
    in_window: int = 0
    symbol_field: Optional[str] = None
    has_lane_field: bool = False
    pnl_field: Optional[str] = None
    sample_keys: List[str] = field(default_factory=list)


@dataclass
class DiagnosticResult:
    window_days: int
    git_sha: str
    timestamp: str
    paper_trades: CollectionProbe = field(
        default_factory=lambda: CollectionProbe(name="paper_trades"),
    )
    crypto_paper_trades: CollectionProbe = field(
        default_factory=lambda: CollectionProbe(name="crypto_paper_trades"),
    )
    decision_log: CollectionProbe = field(
        default_factory=lambda: CollectionProbe(name="alpha_decision_log"),
    )
    decision_log_oldest: Optional[str] = None
    decision_log_newest: Optional[str] = None
    decision_log_span_days: Optional[float] = None
    decision_log_distinct_keys: List[Tuple[str, str]] = field(default_factory=list)
    matched: int = 0
    unmatched: int = 0
    unmatched_by_reason: Dict[str, int] = field(default_factory=dict)
    samples_by_reason: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    hypotheses: Dict[str, str] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)


# ── Probes ────────────────────────────────────────────────


async def _probe_collection(
    db: Any, name: str, cutoff: datetime,
) -> CollectionProbe:
    probe = CollectionProbe(name=name)
    probe.total = await db[name].estimated_document_count()
    if probe.total == 0:
        return probe
    probe.in_window = await db[name].count_documents({
        "$or": [
            {"closed_at": {"$gte": cutoff}},
            {"updated_at": {"$gte": cutoff}},
            {"created_at": {"$gte": cutoff}},
            {"opened_at": {"$gte": cutoff}},
        ],
    })
    sample = await db[name].find_one({})
    if sample:
        probe.symbol_field = _detect_symbol_field(sample)
        probe.has_lane_field = "lane" in sample
        for f in ("realized_pnl_usd", "pnl_usd", "pnl", "r_multiple"):
            if f in sample:
                probe.pnl_field = f
                break
        probe.sample_keys = sorted(sample.keys())[:20]
    return probe


async def _probe_decision_log(
    db: Any, cutoff: datetime,
) -> Tuple[CollectionProbe, Optional[datetime], Optional[datetime],
          List[Tuple[str, str]]]:
    probe = CollectionProbe(name="alpha_decision_log")
    probe.total = await db.alpha_decision_log.estimated_document_count()
    probe.in_window = await db.alpha_decision_log.count_documents({
        "created_at": {"$gte": cutoff},
    })
    if probe.total == 0:
        return probe, None, None, []

    oldest = await db.alpha_decision_log.find_one({}, sort=[("created_at", 1)])
    newest = await db.alpha_decision_log.find_one({}, sort=[("created_at", -1)])
    sample = await db.alpha_decision_log.find_one({})
    if sample:
        probe.symbol_field = _detect_symbol_field(sample)
        probe.has_lane_field = "lane" in sample
        probe.sample_keys = sorted(sample.keys())[:20]

    distinct: List[Tuple[str, str]] = []
    pipeline = [{"$group": {"_id": {"symbol": "$symbol", "lane": "$lane"}}}]
    async for d in db.alpha_decision_log.aggregate(pipeline):
        sym = str(d["_id"].get("symbol") or "").upper()
        ln = str(d["_id"].get("lane") or "").lower()
        if sym:
            distinct.append((sym, ln))

    return probe, oldest.get("created_at"), newest.get("created_at"), distinct


async def _build_decision_log_candidates(
    db: Any, cutoff: datetime,
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Match the same lookup the retrain script builds. Read-only."""
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    cursor = db.alpha_decision_log.find(
        {"created_at": {"$gte": cutoff}},
        projection={"_id": 0, "symbol": 1, "lane": 1, "created_at": 1},
    ).sort("created_at", -1)
    async for d in cursor:
        sym = str(d.get("symbol") or "").upper().strip()
        ln = str(d.get("lane") or "equity").lower()
        if not sym:
            continue
        out.setdefault((sym, ln), d)
    return out


async def _attempt_match(
    db: Any, *,
    coll: str,
    cutoff: datetime,
    decision_lookup: Dict[Tuple[str, str], Dict[str, Any]],
    decision_log_oldest: Optional[datetime],
    result: DiagnosticResult,
) -> None:
    """Walk every paper trade in the window. Record match outcome
    + (when unmatched) the most specific reason. NO writes.
    """
    cursor = db[coll].find(
        {"$or": [
            {"closed_at": {"$gte": cutoff}},
            {"updated_at": {"$gte": cutoff}},
            {"created_at": {"$gte": cutoff}},
            {"opened_at": {"$gte": cutoff}},
        ]},
        projection={
            "_id": 0, "symbol": 1, "ticker": 1, "pair": 1,
            "lane": 1, "asset_class": 1,
            "closed_at": 1, "opened_at": 1, "created_at": 1, "updated_at": 1,
            "pnl_usd": 1, "pnl": 1, "realized_pnl_usd": 1, "r_multiple": 1,
            "status": 1, "direction": 1, "schema_version": 1,
        },
    )
    async for trade in cursor:
        canonical_symbol: Optional[str] = None
        for f in SYMBOL_FIELDS:
            v = trade.get(f)
            if isinstance(v, str) and v.strip():
                canonical_symbol = v.upper().strip()
                break
        if not canonical_symbol:
            result.unmatched += 1
            result.unmatched_by_reason[REASON_NO_SYMBOL] = \
                result.unmatched_by_reason.get(REASON_NO_SYMBOL, 0) + 1
            samples = result.samples_by_reason.setdefault(REASON_NO_SYMBOL, [])
            if len(samples) < SAMPLES_PER_REASON:
                samples.append({"_collection": coll, **_safe_field(trade)})
            continue

        lane = _trade_lane(coll, trade)
        direct_key = (canonical_symbol, lane)
        if direct_key in decision_lookup:
            result.matched += 1
            continue

        normalization_hit = False
        if lane == "crypto":
            for cand in _crypto_symbol_candidates(canonical_symbol):
                if (cand, "crypto") in decision_lookup:
                    normalization_hit = True
                    break
        if normalization_hit:
            result.unmatched += 1
            reason = REASON_NORMALIZATION_MISS
        else:
            other_lane = "equity" if lane == "crypto" else "crypto"
            if (canonical_symbol, other_lane) in decision_lookup:
                result.unmatched += 1
                reason = REASON_LANE_MISMATCH
            else:
                trade_close = (trade.get("closed_at")
                               or trade.get("updated_at")
                               or trade.get("opened_at"))
                if (decision_log_oldest is not None
                        and isinstance(trade_close, datetime)
                        and trade_close < decision_log_oldest):
                    result.unmatched += 1
                    reason = REASON_DECISION_TOO_RECENT
                else:
                    result.unmatched += 1
                    reason = REASON_NO_DECISION_LOG

        result.unmatched_by_reason[reason] = \
            result.unmatched_by_reason.get(reason, 0) + 1
        samples = result.samples_by_reason.setdefault(reason, [])
        if len(samples) < SAMPLES_PER_REASON:
            samples.append({"_collection": coll, **_safe_field(trade)})


# ── Hypothesis classification ─────────────────────────────


def _classify_hypotheses(r: DiagnosticResult) -> None:
    h: Dict[str, str] = {}

    too_old = r.unmatched_by_reason.get(REASON_DECISION_TOO_RECENT, 0)
    h["1_paper_trades_pre_date_decision_log"] = (
        f"YES — {too_old} unmatched trades closed before the oldest ADL row"
        if too_old > 0 else "NO"
    )

    bad_keys = [r.paper_trades.symbol_field, r.crypto_paper_trades.symbol_field]
    bad_keys = [k for k in bad_keys if k and k != "symbol"]
    if bad_keys:
        h["2_unstable_join_keys"] = (
            f"YES — paper_trades uses '{r.paper_trades.symbol_field}', "
            f"crypto_paper_trades uses '{r.crypto_paper_trades.symbol_field}', "
            "alpha_decision_log uses 'symbol'"
        )
    else:
        h["2_unstable_join_keys"] = "NO"

    h["3_wrong_timestamp_field"] = (
        "N/A — current join is keyed by (symbol, lane); time is "
        "only used to bound the read window, not to correlate trade↔decision"
    )

    norm_misses = r.unmatched_by_reason.get(REASON_NORMALIZATION_MISS, 0)
    h["4_symbol_normalization_mismatch"] = (
        f"YES — {norm_misses} crypto rows would match if normalized "
        "(e.g. 'BTC' ↔ 'BTC-USD')"
        if norm_misses > 0 else "NO"
    )

    lane_misses = r.unmatched_by_reason.get(REASON_LANE_MISMATCH, 0)
    no_lane_pt = not r.paper_trades.has_lane_field
    no_lane_cpt = not r.crypto_paper_trades.has_lane_field
    if lane_misses > 0 or no_lane_pt or no_lane_cpt:
        msgs = []
        if no_lane_pt:
            msgs.append("paper_trades has NO 'lane' field — defaults to 'equity'")
        if no_lane_cpt:
            msgs.append("crypto_paper_trades has NO 'lane' field "
                        "— diagnostic infers from collection name")
        if lane_misses > 0:
            msgs.append(f"{lane_misses} unmatched trades match the OTHER lane")
        h["5_lane_mismatch"] = "YES — " + "; ".join(msgs)
    else:
        h["5_lane_mismatch"] = "NO"

    if r.decision_log_span_days is not None and r.decision_log_span_days < r.window_days:
        h["6_alpha_decision_log_only_for_newer_decisions"] = (
            f"YES — alpha_decision_log spans {r.decision_log_span_days:.2f}d "
            f"but the retrain window is {r.window_days}d"
        )
    else:
        h["6_alpha_decision_log_only_for_newer_decisions"] = "NO"

    if r.decision_log.total > 0 and r.decision_log_span_days is not None:
        if r.decision_log_span_days < 7:
            h["7_decision_log_ttl_too_short"] = (
                f"LIKELY — only {r.decision_log_span_days:.2f}d of "
                "history exists; either the retention window is short "
                "or the persistence layer is brand new"
            )
        else:
            h["7_decision_log_ttl_too_short"] = "NO"
    else:
        h["7_decision_log_ttl_too_short"] = "UNKNOWN — no ADL rows"

    diffs = []
    if r.paper_trades.symbol_field != r.crypto_paper_trades.symbol_field:
        diffs.append(
            f"symbol field: {r.paper_trades.symbol_field} (equity) "
            f"vs {r.crypto_paper_trades.symbol_field} (crypto)"
        )
    if r.paper_trades.pnl_field != r.crypto_paper_trades.pnl_field:
        diffs.append(
            f"pnl field: {r.paper_trades.pnl_field} (equity) "
            f"vs {r.crypto_paper_trades.pnl_field} (crypto)"
        )
    h["8_equity_vs_crypto_schema_drift"] = (
        "YES — " + "; ".join(diffs) if diffs else "NO"
    )

    r.hypotheses = h


def _build_recommendations(r: DiagnosticResult) -> None:
    recs: List[str] = []
    total_paper = r.paper_trades.in_window + r.crypto_paper_trades.in_window
    matched_pct = (r.matched / total_paper * 100.0) if total_paper else 0.0

    if r.decision_log.total < 50 or (
        r.decision_log_span_days is not None and r.decision_log_span_days < 1
    ):
        span_str = (
            f"{r.decision_log_span_days:.2f}d"
            if r.decision_log_span_days is not None else "unknown"
        )
        recs.append(
            "PRIMARY: alpha_decision_log is severely under-populated "
            f"({r.decision_log.total} rows total, span "
            f"{span_str} if defined). The persistence "
            "layer at services/ml/shadow_wiring or pipeline.py is the "
            "real bottleneck — confirm it is firing on every decision "
            "across both lanes. NO BACKFILL of historical ADL is "
            "possible (we don't have the upstream features to recreate)."
        )

    if r.unmatched_by_reason.get(REASON_NO_SYMBOL, 0) > 0:
        recs.append(
            "Patch extract_rows() in scripts/retrain_alpha_models.py "
            "to read ``trade.get('symbol') or trade.get('ticker') or "
            "trade.get('pair')`` so paper_trades rows (which use "
            "'ticker') are not blanket-skipped as missing_symbol."
        )

    if r.unmatched_by_reason.get(REASON_LANE_MISMATCH, 0) > 0 or \
       not r.crypto_paper_trades.has_lane_field:
        recs.append(
            "Patch extract_rows() to derive lane='crypto' for any row "
            "from ``crypto_paper_trades`` (or with ``asset_class='crypto'``) "
            "instead of defaulting to 'equity' when the field is absent."
        )

    if r.unmatched_by_reason.get(REASON_NORMALIZATION_MISS, 0) > 0:
        recs.append(
            "Add a symbol-normalization step in extract_rows() so "
            "bare crypto symbols ('BTC', 'ETH') also probe the "
            "'-USD' / 'USDT' variants the alpha_decision_log uses. "
            "Cheapest fix is to try the bare form first, then "
            "fall back to '<base>-USD'."
        )

    if r.paper_trades.pnl_field != "realized_pnl_usd":
        recs.append(
            "Patch label_from_outcome() to read pnl_usd / pnl / "
            "r_multiple instead of only ``realized_pnl_usd``. The two "
            "paper-trade collections use different field names."
        )

    if matched_pct < 5.0:
        recs.append(
            "DO NOT proceed to v2 retrain --write-artifact yet. With "
            f"only {matched_pct:.1f}% join coverage, training would "
            "fit on a non-representative slice. Build the join fixes "
            "+ wait for ADL persistence to backfill organically."
        )

    if not recs:
        recs.append("No blocking issues detected; join coverage is healthy.")
    r.recommendations = recs
