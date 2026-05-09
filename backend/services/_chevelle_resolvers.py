"""Pure label-resolver helpers for the Chevelle memory labeler.

────────────────────────────────────────────────────────────────────
AUTHORITY-BOUNDARY CONTRACT (mirrors parent module)
────────────────────────────────────────────────────────────────────

Strangler-split out of ``services.chevelle_memory_labeler`` so the
parent module fits under the ``core-governance`` 600-line preferred
ceiling. Identical no-broker / no-executor / no-DB-write firewall —
the ``test_chevelle_memory_labeler.py`` static checks scan BOTH
files for forbidden tokens.

Every helper here is a PURE function — no I/O, no side-effects,
deterministic output for a given input row. The only external
import is ``services.event_aware_regime_labeler`` (lazily, inside
``resolve_event_era``) so the labeler can map our 9-bucket era
taxonomy onto the canonical event taxonomy without a hard import
dependency at module-load time.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from services.chevelle_memory_labels import (
    DataQuality,
    EventEra,
    FailureMode,
    OutcomeLabel,
    PUBLIC_LAUNCH_FLOOR_ISO,
    RECENT_PAPER_DAYS,
    TRUST_CURRENT_MACRO_PROXY,
    TRUST_HISTORICAL_PAPER_TRADE,
    TRUST_LIVE_REAL_FILL,
    TRUST_QUARANTINED,
    TRUST_RECENT_PAPER_TRADE,
    TRUST_SYNTHETIC_BACKTEST,
    TRUST_TOXIC_MEMORY,
)


_KNOWN_LANES = {"equity", "crypto", "options", "macro", "unknown"}


def normalize_symbol(raw: Any) -> str:
    """Uppercase, strip whitespace, normalize crypto pair forms.

    Examples
    --------
    >>> normalize_symbol("btc-usd")
    'BTC'
    >>> normalize_symbol(" BTC/USD ")
    'BTC'
    >>> normalize_symbol("AAPL")
    'AAPL'
    """
    if raw is None:
        return ""
    s = str(raw).strip().upper()
    if not s:
        return ""
    for quote in ("-USD", "/USD", "-USDT", "/USDT"):
        if s.endswith(quote):
            s = s[: -len(quote)]
            break
    return s


def coerce_dt(v: Any) -> Optional[datetime]:
    """Best-effort coerce timestamp values into a UTC datetime.

    Returns ``None`` on any unparseable input. The caller decides
    whether ``None`` should reject the row or just lower data_quality.
    """
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        if not v.strip():
            return None
        raw = v.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                return datetime.fromisoformat(raw + "T00:00:00+00:00")
            except ValueError:
                return None
    return None


def resolve_lane(row: dict) -> str:
    """Read ``lane`` from the row, fall back to inference."""
    lane = row.get("lane")
    if isinstance(lane, str) and lane.strip().lower() in _KNOWN_LANES:
        return lane.strip().lower()

    asset = (row.get("asset_type") or row.get("asset_class") or "")
    if isinstance(asset, str):
        asset = asset.strip().lower()
        if asset in _KNOWN_LANES:
            return asset
        if asset == "stock":
            return "equity"

    sym = normalize_symbol(row.get("symbol") or row.get("ticker"))
    if sym in {"BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "LINK"}:
        return "crypto"
    return "unknown"


def resolve_source(row: dict) -> Optional[str]:
    """Read the ``source`` token off the row. Returns None when
    absent — caller treats absence as a hard rejection.
    """
    src = row.get("source")
    if isinstance(src, str) and src.strip():
        return src.strip().lower()
    for alt in ("source_engine", "data_source", "origin"):
        v = row.get(alt)
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    return None


def resolve_outcome_label(row: dict) -> OutcomeLabel:
    """Map realized P&L / R-multiple into the four buckets."""
    if row.get("status") == "open" and row.get("closed_at") is None:
        return OutcomeLabel.UNRESOLVED

    pnl = None
    for key in ("pnl_usd", "realized_pnl_usd", "pnl", "r_multiple"):
        val = row.get(key)
        if val is not None:
            try:
                pnl = float(val)
                break
            except (TypeError, ValueError):
                continue

    if pnl is None:
        return OutcomeLabel.UNRESOLVED
    if pnl > 0.001:
        return OutcomeLabel.WIN
    if pnl < -0.001:
        return OutcomeLabel.LOSS
    return OutcomeLabel.NEUTRAL


def resolve_failure_mode(
    row: dict, outcome: OutcomeLabel,
) -> FailureMode:
    """Map row-level failure metadata into the coarse-grained tags."""
    raw = row.get("failure_mode") or row.get("failure_code")
    if isinstance(raw, str) and raw.strip():
        token = raw.strip().lower()
        if "blowup" in token or "tail" in token:
            return FailureMode.BLOWUP
        if "regime" in token or "mismatch" in token:
            return FailureMode.REGIME_MISMATCH
        if "data" in token or "integrity" in token:
            return FailureMode.DATA_INTEGRITY
        if "toxic" in token:
            return FailureMode.TOXIC_HIGH_CONFIDENCE

    # Heuristic fallback: a high-confidence loss with no explicit
    # failure tag is the textbook "toxic" case.
    conf = row.get("confidence") or row.get("strategist_conf") or 0.0
    try:
        conf_f = float(conf)
    except (TypeError, ValueError):
        conf_f = 0.0
    pnl_pct = row.get("pnl_pct") or row.get("r_multiple") or 0.0
    try:
        pnl_pct_f = float(pnl_pct)
    except (TypeError, ValueError):
        pnl_pct_f = 0.0
    if (
        outcome is OutcomeLabel.LOSS
        and conf_f >= 0.85
        and pnl_pct_f <= -0.5
    ):
        return FailureMode.TOXIC_HIGH_CONFIDENCE

    return FailureMode.NONE


def resolve_event_era(opened_at: Optional[datetime]) -> EventEra:
    """Map the row's open timestamp into one of the 9 era buckets."""
    if opened_at is None:
        return EventEra.UNKNOWN_ERA

    try:
        from services.event_aware_regime_labeler import (
            EventAwareRegimeLabeler,
        )
    except Exception:  # noqa: BLE001
        return EventEra.UNKNOWN_ERA

    try:
        date_str = opened_at.strftime("%Y-%m-%d")
        event = EventAwareRegimeLabeler().tag_event(date_str)
    except Exception:  # noqa: BLE001
        return EventEra.UNKNOWN_ERA

    mapping = {
        "GFC_CRISIS": EventEra.GFC_2008,
        "FLASH_CRASH": EventEra.FLASH_CRASH_2010,
        "CHINA_DEVALUATION": EventEra.CHINA_DEVAL_2015,
        "VOL_SPIKE_2018": EventEra.VOL_SPIKE_2018,
        "COVID_CRASH": EventEra.COVID_2020,
        "COVID_RECOVERY": EventEra.COVID_2020,
        "RATE_HIKE_CYCLE": EventEra.RATE_HIKE_2022,
        "AI_BUBBLE": EventEra.AI_BUBBLE_2024_2025,
    }
    if event in mapping:
        return mapping[event]
    if event == "NONE":
        if opened_at.year >= 2026:
            return EventEra.CURRENT_REGIME
        if opened_at.year < 2008:
            return EventEra.UNKNOWN_ERA
        return EventEra.CURRENT_REGIME
    return EventEra.UNKNOWN_ERA


def compute_data_quality(
    row: dict, *, has_required: bool,
) -> DataQuality:
    """Score row completeness — REJECTED if required missing,
    else HIGH/MEDIUM/LOW based on optional-field count."""
    if not has_required:
        return DataQuality.REJECTED

    optional_present = 0
    if row.get("confidence") is not None:
        optional_present += 1
    if row.get("direction") or row.get("side") or row.get("action"):
        optional_present += 1
    if row.get("regime"):
        optional_present += 1
    if any(
        row.get(k) is not None
        for k in ("pnl_usd", "realized_pnl_usd", "pnl", "r_multiple")
    ):
        optional_present += 1

    if optional_present >= 4:
        return DataQuality.HIGH
    if optional_present >= 2:
        return DataQuality.MEDIUM
    return DataQuality.LOW


def compute_trust_weight(
    *,
    source: Optional[str],
    opened_at: Optional[datetime],
    failure_mode: FailureMode,
    quarantined: bool,
    lane: str,
) -> float:
    """Apply the operator-mandated trust ladder.

    Precedence (high → low):
      1. quarantined → 0.00 (always wins)
      2. failure_mode in toxic set → 0.10
      3. source-based ladder (live / paper / macro / synthetic).
    """
    if quarantined:
        return TRUST_QUARANTINED

    toxic_modes = {
        FailureMode.BLOWUP,
        FailureMode.TOXIC_HIGH_CONFIDENCE,
        FailureMode.REGIME_MISMATCH,
        FailureMode.DATA_INTEGRITY,
    }
    if failure_mode in toxic_modes:
        return TRUST_TOXIC_MEMORY

    src = (source or "").lower()

    if src in {"live", "live_broker", "alpaca", "kraken", "webull"}:
        return TRUST_LIVE_REAL_FILL

    if lane == "macro" or src in {"fred", "fred_macro", "macro_snapshot"}:
        return TRUST_CURRENT_MACRO_PROXY

    # Paper trades — recent vs historical split by RECENT_PAPER_DAYS.
    # Checked BEFORE the cutover-date branch so a paper trade dated
    # before the public-launch floor still resolves as paper, not
    # synthetic.
    paper_sources = {
        "crypto_paper_bot", "crypto_paper_trader", "paper_trading",
        "ml_paper_trader", "day_trade_scanner", "options_paper",
        "options_paper_bot", "camaro", "shelly", "shadow",
        "research_shadow", "kraken_equity_shadow",
    }
    if src in paper_sources or "paper" in src:
        if opened_at is not None:
            from services.datetime_utils import ensure_utc
            opened_safe = ensure_utc(opened_at)
            if opened_safe is not None:
                age_days = (
                    datetime.now(timezone.utc) - opened_safe
                ).days
                if age_days <= RECENT_PAPER_DAYS:
                    return TRUST_RECENT_PAPER_TRADE
                return TRUST_HISTORICAL_PAPER_TRADE
        return TRUST_HISTORICAL_PAPER_TRADE

    floor_dt = datetime.fromisoformat(
        PUBLIC_LAUNCH_FLOOR_ISO + "T00:00:00+00:00"
    )
    if (
        src in {"backtest", "synthetic", "simulator", "yfinance"}
        or (opened_at is not None and opened_at < floor_dt)
    ):
        return TRUST_SYNTHETIC_BACKTEST

    return TRUST_SYNTHETIC_BACKTEST
