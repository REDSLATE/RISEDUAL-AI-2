"""
Kraken Equity Shadow Compare — Phase 0
======================================

Pulls public market data for Kraken xStocks (tokenized US equity) and
compares mid-price + spread against our existing primary equity quote
provider (Alpaca / Alpha Vantage). Writes one row per (symbol × tick)
into ``kraken_equity_shadow_compare`` and fires a Slack alert when
divergence breaches the configured bps threshold.

Design rules:

* **No orders, no auth.** Phase 0 hits only ``/0/public/Ticker`` and
  ``/0/public/AssetPairs`` — neither needs a Kraken API key. Production
  credentials in ``KRAKEN_API_KEY`` / ``KRAKEN_API_SECRET`` are reserved
  for later phases (paper, live equity orders).
* **Default OFF.** ``KRAKEN_SHADOW_ENABLED=1`` is the only way the
  scheduler hooks fire. Cold-start pods stay silent until ops opts in.
* **Rate-limit-respecting batching.** ~520 symbols / 100 per batch /
  one batch every 60s = 5 batches over a 5-min window, well under
  Kraken's ~1 call-per-second public limit.
* **Symbology resolved via AssetPairs.** We never hardcode pair codes.
  Discovery hits ``/0/public/AssetPairs?asset_class=tokenized_asset``,
  caches the canonical-symbol → kraken-pair mapping in
  ``xstock_pair_metadata`` (TTL: 24h), and refreshes lazily. Symbols
  with no Kraken xStock listing are skipped (logged once, never
  retried within the same warm window).
* **xStocks trade outside RTH** (extended-hours + weekends on-chain),
  but Alpaca returns its last-close during off-hours. We tag every
  comparison with a ``market_session`` so the analyst can filter
  divergences that are real signal vs. expected mid-spread between a
  staled Alpaca close and a still-trading xStock book.

Public entry points
-------------------
* ``run_kraken_shadow_compare_once(db)`` — one tick: discover (if
  cache stale), pull batched tickers, compare with Alpaca, persist,
  optionally Slack-alert. Idempotent and safe to call repeatedly.
* ``summarize_today(db)`` — admin / burn-in card aggregator. Returns
  ``{rows, max_bps, p95_bps, divergent_count, alerts_fired,
  last_run_at, sample}`` for the operator UI.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, time, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# ── Config knobs (env-tunable; safe defaults) ────────────────────

ENV_ENABLED = "KRAKEN_SHADOW_ENABLED"
ENV_BPS_ALERT = "KRAKEN_SHADOW_DIVERGENCE_BPS_ALERT"
ENV_BATCH_SIZE = "KRAKEN_SHADOW_BATCH_SIZE"
ENV_BATCH_DELAY = "KRAKEN_SHADOW_BATCH_DELAY_SECONDS"
ENV_PAIR_CACHE_TTL = "KRAKEN_SHADOW_PAIR_CACHE_TTL_HOURS"
ENV_TOP_N = "KRAKEN_SHADOW_TOP_N_ML"
ENV_INCLUDE_SP500 = "KRAKEN_SHADOW_INCLUDE_SP500"

KRAKEN_REST_BASE = "https://api.kraken.com"
PAIR_METADATA_COLL = "xstock_pair_metadata"
COMPARE_COLL = "kraken_equity_shadow_compare"
SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "sp500_constituents.json"


def _is_enabled() -> bool:
    return (os.environ.get(ENV_ENABLED, "").strip().lower()
            in ("1", "true", "yes", "on"))


def _bps_alert_threshold() -> float:
    try:
        return float(os.environ.get(ENV_BPS_ALERT, "50"))
    except ValueError:
        return 50.0


def _batch_size() -> int:
    try:
        return max(1, int(os.environ.get(ENV_BATCH_SIZE, "100")))
    except ValueError:
        return 100


def _batch_delay() -> float:
    try:
        return max(0.0, float(os.environ.get(ENV_BATCH_DELAY, "60")))
    except ValueError:
        return 60.0


def _pair_cache_ttl_hours() -> float:
    try:
        return max(1.0, float(os.environ.get(ENV_PAIR_CACHE_TTL, "24")))
    except ValueError:
        return 24.0


def _top_n_ml() -> int:
    try:
        return max(0, int(os.environ.get(ENV_TOP_N, "20")))
    except ValueError:
        return 20


def _include_sp500() -> bool:
    return (os.environ.get(ENV_INCLUDE_SP500, "true").strip().lower()
            in ("1", "true", "yes", "on"))


# ── Universe loader ──────────────────────────────────────────────


def load_universe() -> list[str]:
    """Top-N ML watchlist + (optionally) the full S&P 500 seed list,
    deduplicated, normalized to upper-case canonical tickers."""
    try:
        raw = json.loads(SEED_PATH.read_text())
    except Exception as exc:
        logger.warning("[kraken_shadow] universe seed missing: %s", exc)
        return []
    tickers = raw.get("tickers", []) if isinstance(raw, dict) else raw
    if not isinstance(tickers, list):
        return []

    out: list[str] = []
    seen: set[str] = set()
    top_n = _top_n_ml()
    include_full = _include_sp500()

    # Top-N first (preserves caller intent — small fast batch comes
    # before the long tail, so a 1-batch tick can still snapshot the
    # ML watchlist if the rest of the run is starved).
    for i, t in enumerate(tickers):
        if not isinstance(t, str):
            continue
        sym = t.strip().upper()
        if not sym or sym in seen:
            continue
        if i < top_n or include_full:
            seen.add(sym)
            out.append(sym)
    return out


# ── Symbology — discover + persist Kraken xStock pair codes ──────


def _expected_pair_candidates(symbol: str) -> list[str]:
    """Symbols Kraken's tokenized_asset universe might use for our
    canonical ticker. Order matters: first match wins.

    Most xStocks trade as ``<SYM>USD`` (e.g. ``AAPLUSD``). A few have
    ``X`` or ``T`` prefixes / suffixes that Kraken adds to disambiguate
    against legacy crypto tickers — for those, AssetPairs ``altname``
    contains the canonical symbol with ``/USD`` separator. We try the
    plain form first because it matches the dominant pattern.
    """
    sym = symbol.upper().strip().replace(".", "")
    return [
        f"{sym}USD",       # AAPLUSD, NVDAUSD, MSFTUSD
        f"{sym}xUSD",      # potential "x" suffix Kraken sometimes uses
        f"X{sym}USD",      # potential X-prefix legacy convention
    ]


def _resolve_pair_from_assetpairs(
    symbol: str,
    asset_pairs_payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Search Kraken's AssetPairs response for the canonical pair code.

    Strategy:
    1. Direct lookup of expected candidates (fast path, ~98% hits).
    2. Match by ``altname == "<SYM>/USD"`` for the long tail.
    3. Match by ``base == "<SYM>"`` AND ``quote == "USD"`` AND
       ``aclass_base == "tokenized_asset"`` as a final guard.
    """
    if not asset_pairs_payload or not isinstance(asset_pairs_payload, dict):
        return None
    candidates = _expected_pair_candidates(symbol)
    sym = symbol.upper().strip().replace(".", "")
    expected_altnames = {f"{sym}/USD", f"{sym}USD"}

    for cand in candidates:
        if cand in asset_pairs_payload:
            row = asset_pairs_payload[cand]
            if isinstance(row, dict):
                return {
                    "canonical_symbol": symbol.upper().strip(),
                    "kraken_pair": cand,
                    "altname": row.get("altname", ""),
                    "wsname": row.get("wsname", ""),
                    "status": row.get("status", "online"),
                    "tick_size": row.get("tick_size"),
                }

    for pair_code, row in asset_pairs_payload.items():
        if not isinstance(row, dict):
            continue
        altname = (row.get("altname") or "").upper()
        base = (row.get("base") or "").upper()
        quote = (row.get("quote") or "").upper()
        aclass_base = (row.get("aclass_base") or "").lower()
        if altname in expected_altnames or (
            base == sym and quote == "USD" and aclass_base == "tokenized_asset"
        ):
            return {
                "canonical_symbol": symbol.upper().strip(),
                "kraken_pair": pair_code,
                "altname": row.get("altname", ""),
                "wsname": row.get("wsname", ""),
                "status": row.get("status", "online"),
                "tick_size": row.get("tick_size"),
            }
    return None


async def _fetch_xstock_assetpairs(http_get) -> dict[str, Any]:
    """Pull /0/public/AssetPairs?asset_class=tokenized_asset.

    ``http_get`` is an injectable async callable so tests can stub
    network I/O without touching httpx.
    """
    payload = await http_get(
        f"{KRAKEN_REST_BASE}/0/public/AssetPairs",
        params={"asset_class": "tokenized_asset"},
    )
    if not isinstance(payload, dict):
        return {}
    if payload.get("error"):
        logger.warning("[kraken_shadow] AssetPairs returned error: %s", payload["error"])
    return payload.get("result") or {}


async def discover_pair_metadata(
    db: Any,
    universe: Iterable[str],
    *,
    http_get,
    now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Resolve each universe symbol to its Kraken xStock pair code,
    cache the result in ``xstock_pair_metadata``, and return the live
    map ``{canonical_symbol: metadata_doc}``.

    Idempotent. Cache TTL is ``KRAKEN_SHADOW_PAIR_CACHE_TTL_HOURS``
    (default 24h) — symbols whose row is fresh skip the upstream call.
    """
    if db is None:
        return {}
    now = now or datetime.now(timezone.utc)
    ttl = timedelta(hours=_pair_cache_ttl_hours())

    cached_map: dict[str, dict[str, Any]] = {}
    cursor = db[PAIR_METADATA_COLL].find(
        {"canonical_symbol": {"$in": list(universe)}},
        {"_id": 0},
    )
    async for row in cursor:
        cached_map[row["canonical_symbol"]] = row

    needs_refresh: list[str] = []
    for sym in universe:
        row = cached_map.get(sym)
        if not row:
            needs_refresh.append(sym)
            continue
        last_seen = row.get("last_seen")
        if not isinstance(last_seen, datetime):
            needs_refresh.append(sym)
            continue
        # Mongo can return naive UTC; coerce to aware before subtracting.
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        if now - last_seen > ttl:
            needs_refresh.append(sym)

    if not needs_refresh:
        return cached_map

    asset_pairs = await _fetch_xstock_assetpairs(http_get)
    for sym in needs_refresh:
        resolved = _resolve_pair_from_assetpairs(sym, asset_pairs)
        if resolved is None:
            # Persist a "not_listed" sentinel so we don't keep
            # hammering AssetPairs every tick for the same symbol.
            await db[PAIR_METADATA_COLL].update_one(
                {"canonical_symbol": sym},
                {"$set": {
                    "canonical_symbol": sym,
                    "kraken_pair": None,
                    "status": "not_listed",
                    "last_seen": now,
                }},
                upsert=True,
            )
            cached_map[sym] = {
                "canonical_symbol": sym,
                "kraken_pair": None,
                "status": "not_listed",
                "last_seen": now,
            }
            continue
        resolved["last_seen"] = now
        await db[PAIR_METADATA_COLL].update_one(
            {"canonical_symbol": sym},
            {"$set": resolved},
            upsert=True,
        )
        cached_map[sym] = resolved
    return cached_map


# ── Ticker fetch + parsing ───────────────────────────────────────


def parse_ticker_row(raw_row: dict[str, Any]) -> dict[str, float] | None:
    """Convert one Kraken Ticker dict into ``{ask, bid, last, mid,
    spread_bps}``. Returns None on malformed input.

    Spread bps = (ask - bid) / mid × 10_000. A zero / missing mid
    short-circuits to None — a divergence on a zero price is meaningless.
    """
    if not isinstance(raw_row, dict):
        return None
    try:
        ask = float(raw_row["a"][0])
        bid = float(raw_row["b"][0])
        last = float(raw_row["c"][0])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    mid = (ask + bid) / 2 if ask > 0 and bid > 0 else last
    if mid <= 0:
        return None
    spread_bps = ((ask - bid) / mid) * 10_000 if ask > 0 and bid > 0 else 0.0
    return {
        "ask": ask,
        "bid": bid,
        "last": last,
        "mid": mid,
        "spread_bps": round(spread_bps, 2),
    }


async def fetch_kraken_ticker_batch(
    pair_codes: list[str],
    *,
    http_get,
) -> dict[str, dict[str, float]]:
    """Pull one batch from /0/public/Ticker. Returns
    ``{kraken_pair: parsed_row}``. Failures degrade to empty dict so the
    caller can keep marching through the rest of the universe."""
    if not pair_codes:
        return {}
    payload = await http_get(
        f"{KRAKEN_REST_BASE}/0/public/Ticker",
        params={
            "pair": ",".join(pair_codes),
            "asset_class": "tokenized_asset",
        },
    )
    if not isinstance(payload, dict):
        return {}
    if payload.get("error"):
        logger.warning("[kraken_shadow] Ticker batch error: %s", payload["error"])
    result = payload.get("result") or {}
    out: dict[str, dict[str, float]] = {}
    for pair_code, row in result.items():
        parsed = parse_ticker_row(row)
        if parsed is not None:
            out[pair_code] = parsed
    return out


# ── Market-session classifier ────────────────────────────────────


_US_HOLIDAYS_2026 = frozenset({
    (1, 1), (1, 19), (2, 16), (3, 27), (5, 25),
    (7, 3), (9, 7), (11, 26), (12, 25),
})

_PRE_OPEN = time(4, 0)
_RTH_OPEN = time(9, 30)
_RTH_CLOSE = time(16, 0)
_AFT_CLOSE = time(20, 0)


def market_session(now_utc: datetime) -> str:
    """Classify ``now_utc`` into ``regular | pre | post | closed``
    using static ET offsets. Conservative — we'd rather mislabel
    boundary minutes than depend on a heavyweight market-calendar
    library for a Phase 0 shadow comparator.
    """
    et_offset = timedelta(hours=-5)  # EST baseline; DST drift acceptable here
    et = (now_utc + et_offset).replace(tzinfo=None)
    if et.weekday() >= 5:
        return "closed"
    if (et.month, et.day) in _US_HOLIDAYS_2026:
        return "closed"
    t = et.time()
    if _PRE_OPEN <= t < _RTH_OPEN:
        return "pre"
    if _RTH_OPEN <= t < _RTH_CLOSE:
        return "regular"
    if _RTH_CLOSE <= t < _AFT_CLOSE:
        return "post"
    return "closed"


# ── Slack alert ──────────────────────────────────────────────────


async def _slack_alert_divergence(
    *,
    symbol: str,
    kraken_pair: str,
    divergence_bps: float,
    alpaca_mid: float,
    kraken_mid: float,
    session: str,
) -> bool:
    """Best-effort Slack notify when divergence breaches the threshold.
    Mirrors the integrity_mitigation_service pattern. Never raises."""
    webhook_url = (os.environ.get("SLACK_WEBHOOK_URL") or "").strip()
    if not webhook_url:
        return False
    try:
        import httpx
        title = (
            f"RISEDUAL · Kraken xStock divergence · {symbol} "
            f"({divergence_bps:.1f} bps)"
        )
        blocks = [
            {"type": "header",
             "text": {"type": "plain_text", "text": title[:150]}},
            {"type": "section",
             "fields": [
                 {"type": "mrkdwn", "text": f"*kraken_pair*\n`{kraken_pair}`"},
                 {"type": "mrkdwn", "text": f"*market_session*\n`{session}`"},
                 {"type": "mrkdwn", "text": f"*alpaca_mid*\n`{alpaca_mid:.2f}`"},
                 {"type": "mrkdwn", "text": f"*kraken_mid*\n`{kraken_mid:.2f}`"},
             ]},
        ]
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                webhook_url,
                json={"text": title, "blocks": blocks},
            )
            return 200 <= resp.status_code < 300
    except Exception as exc:  # noqa: BLE001
        logger.warning("[kraken_shadow] slack notify crashed: %s", exc)
        return False


# ── HTTP helper (default httpx async GET) ────────────────────────


async def _httpx_get(url: str, *, params: dict[str, Any] | None = None) -> Any:
    import httpx
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            logger.warning(
                "[kraken_shadow] HTTP %d on %s: %s",
                resp.status_code, url, resp.text[:200],
            )
            return {}
        try:
            return resp.json()
        except Exception:
            return {}


# ── Compare + persist ────────────────────────────────────────────


def compute_divergence_bps(alpaca_mid: float, kraken_mid: float) -> float:
    """Symmetric divergence measured as bps off Alpaca's mid. Uses
    Alpaca as the denominator because it's our primary feed; the
    shadow lane is the challenger."""
    if alpaca_mid <= 0:
        return 0.0
    return abs(kraken_mid - alpaca_mid) / alpaca_mid * 10_000


async def run_kraken_shadow_compare_once(
    db: Any,
    *,
    http_get=None,
    alpaca_quote_fn=None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One full tick: discover → batch fetch → compare → persist →
    alert. Returns a summary dict so the scheduler / admin can log it.

    ``http_get`` and ``alpaca_quote_fn`` are injectable for tests.
    """
    if not _is_enabled():
        return {"ok": False, "reason": "disabled"}
    if db is None:
        return {"ok": False, "reason": "no_db"}

    http_get = http_get or _httpx_get
    if alpaca_quote_fn is None:
        from services.price_provider import get_quote
        alpaca_quote_fn = get_quote

    now = now or datetime.now(timezone.utc)
    session = market_session(now)
    universe = load_universe()
    if not universe:
        return {"ok": False, "reason": "empty_universe"}

    pair_map = await discover_pair_metadata(
        db, universe, http_get=http_get, now=now,
    )
    listed_pairs: list[tuple[str, str]] = [
        (sym, meta["kraken_pair"])
        for sym, meta in pair_map.items()
        if meta.get("kraken_pair") and meta.get("status") != "not_listed"
    ]
    if not listed_pairs:
        return {
            "ok": True,
            "rows_written": 0,
            "session": session,
            "note": "no_listed_xstocks",
        }

    batch_size = _batch_size()
    delay = _batch_delay()
    rows_written = 0
    alerts_fired = 0
    alert_threshold = _bps_alert_threshold()

    for i in range(0, len(listed_pairs), batch_size):
        chunk = listed_pairs[i : i + batch_size]
        chunk_pairs = [p for _, p in chunk]
        kraken_quotes = await fetch_kraken_ticker_batch(
            chunk_pairs, http_get=http_get,
        )

        for symbol, kraken_pair in chunk:
            kraken = kraken_quotes.get(kraken_pair)
            if not kraken:
                continue
            try:
                alpaca = await alpaca_quote_fn(symbol)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[kraken_shadow] alpaca quote failed %s: %s", symbol, exc)
                alpaca = None
            alpaca_price = float((alpaca or {}).get("price") or 0.0)
            if alpaca_price <= 0:
                continue
            divergence_bps = compute_divergence_bps(
                alpaca_price, kraken["mid"],
            )
            doc = {
                "symbol": symbol,
                "kraken_pair": kraken_pair,
                "kraken_mid": kraken["mid"],
                "kraken_spread_bps": kraken["spread_bps"],
                "alpaca_mid": alpaca_price,
                "divergence_bps": round(divergence_bps, 2),
                "market_session": session,
                "fetched_at": now,
                "alert_fired": False,
            }
            if (
                divergence_bps >= alert_threshold
                and session == "regular"
            ):
                fired = await _slack_alert_divergence(
                    symbol=symbol,
                    kraken_pair=kraken_pair,
                    divergence_bps=divergence_bps,
                    alpaca_mid=alpaca_price,
                    kraken_mid=kraken["mid"],
                    session=session,
                )
                doc["alert_fired"] = fired
                if fired:
                    alerts_fired += 1
            await db[COMPARE_COLL].insert_one(doc)
            rows_written += 1

        if i + batch_size < len(listed_pairs) and delay > 0:
            await asyncio.sleep(delay)

    return {
        "ok": True,
        "rows_written": rows_written,
        "alerts_fired": alerts_fired,
        "session": session,
        "universe_size": len(universe),
        "listed_count": len(listed_pairs),
    }


# ── Burn-in / admin summary ──────────────────────────────────────


async def summarize_today(db: Any) -> dict[str, Any]:
    """Aggregate today's compare rows for the admin burn-in card.

    Returns
    -------
    dict
        ``{rows, max_bps, p95_bps, divergent_count, alerts_fired,
        last_run_at, session_counts, sample}`` — empty-ish on no data.
    """
    if db is None:
        return {"rows": 0}
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    cursor = db[COMPARE_COLL].find(
        {"fetched_at": {"$gte": day_start}},
        {"_id": 0},
    ).sort("fetched_at", -1)
    rows = await cursor.to_list(length=10_000)
    if not rows:
        return {
            "rows": 0,
            "max_bps": 0.0,
            "p95_bps": 0.0,
            "divergent_count": 0,
            "alerts_fired": 0,
            "last_run_at": None,
            "session_counts": {},
            "sample": [],
            "threshold_bps": _bps_alert_threshold(),
            "enabled": _is_enabled(),
        }

    bps_values = sorted(float(r.get("divergence_bps") or 0.0) for r in rows)
    threshold = _bps_alert_threshold()
    divergent = sum(1 for v in bps_values if v >= threshold)
    alerts = sum(1 for r in rows if r.get("alert_fired"))
    p95_idx = max(0, int(len(bps_values) * 0.95) - 1)
    session_counts: dict[str, int] = {}
    for r in rows:
        s = r.get("market_session", "unknown")
        session_counts[s] = session_counts.get(s, 0) + 1

    sample = []
    for r in rows[:5]:
        sample.append({
            "symbol": r.get("symbol"),
            "divergence_bps": r.get("divergence_bps"),
            "alpaca_mid": r.get("alpaca_mid"),
            "kraken_mid": r.get("kraken_mid"),
            "market_session": r.get("market_session"),
        })

    last_run = rows[0].get("fetched_at")
    if isinstance(last_run, datetime):
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
        last_run_iso = last_run.isoformat()
    else:
        last_run_iso = None

    return {
        "rows": len(rows),
        "max_bps": round(bps_values[-1], 2) if bps_values else 0.0,
        "p95_bps": round(bps_values[p95_idx], 2) if bps_values else 0.0,
        "divergent_count": divergent,
        "alerts_fired": alerts,
        "last_run_at": last_run_iso,
        "session_counts": session_counts,
        "sample": sample,
        "threshold_bps": threshold,
        "enabled": _is_enabled(),
    }


async def ensure_indexes(db: Any) -> dict[str, str]:
    """Create the indexes the compare/persist path relies on. Safe to
    call repeatedly — Mongo no-ops when the index already exists."""
    if db is None:
        return {"status": "no_db"}
    try:
        await db[PAIR_METADATA_COLL].create_index(
            "canonical_symbol", unique=True,
        )
        await db[COMPARE_COLL].create_index([("symbol", 1), ("fetched_at", -1)])
        await db[COMPARE_COLL].create_index([("fetched_at", -1)])
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[kraken_shadow] ensure_indexes failed: %s", exc)
        return {"status": "error", "error": str(exc)}
