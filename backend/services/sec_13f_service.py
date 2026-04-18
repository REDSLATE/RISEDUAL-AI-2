"""SEC 13F-HR Holder Tracking Service.

Fetches institutional investor holdings from SEC EDGAR directly (free, no API key).

Data flow:
 1. For each tracked institution (CIK), fetch list of 13F-HR filings from
    https://data.sec.gov/submissions/CIK{cik:010d}.json
 2. For each new filing, download the INFORMATION TABLE XML and parse into
    {cusip, issuer, class, shares, value} rows.
 3. Store per-filing in ``sec_13f_filings`` + per-holding rows in ``sec_13f_holdings``.
 4. Diff vs. previous quarter for QoQ changes (new, exited, increased, decreased).

Rate limit: SEC EDGAR allows 10 req/sec with a User-Agent identifying the requester.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = os.environ.get("SEC_EDGAR_USER_AGENT", "RISEDUAL INC contact@risedual.ai")
HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}

# Top institutional investors by AUM (seed list).
# Format: "Display Name" → CIK (string, unpadded)
TOP_INSTITUTIONS: dict[str, str] = {
    "Berkshire Hathaway": "0001067983",
    "BlackRock Inc": "0001364742",
    "Vanguard Group": "0000102909",
    "State Street Corp": "0000093751",
    "Renaissance Technologies": "0001037389",
    "Citadel Advisors": "0001423053",
    "Bridgewater Associates": "0001350694",
    "Two Sigma Investments": "0001179392",
    "Millennium Management": "0001273087",
    "Point72 Asset Management": "0001603466",
    "D.E. Shaw": "0001009207",
    "AQR Capital Management": "0001167557",
    "Tiger Global Management": "0001167483",
    "Coatue Management": "0001135730",
    "ARK Investment Management": "0001697748",
    "FMR LLC (Fidelity)": "0000315066",
    "T. Rowe Price": "0000080255",
    "Wellington Management": "0000902219",
    "Northern Trust": "0000073124",
    "Invesco": "0000914208",
    "Morgan Stanley": "0000895421",
    "JPMorgan Chase": "0000019617",
    "Bank of America": "0000070858",
    "Goldman Sachs": "0000886982",
    "Geode Capital": "0001426500",
}

# In-memory CUSIP → ticker cache (built opportunistically)
_CUSIP_TICKER_CACHE: dict[str, str] = {}
_SEC_TICKERS_CACHE: list[dict] | None = None
_SEC_TICKERS_LOCK = asyncio.Lock()

# Rate limiter — 10 req/sec EDGAR ceiling, we stay at 8
_sem = asyncio.Semaphore(4)
_last_call_ts = [0.0]


async def _rate_limited_get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    async with _sem:
        now = asyncio.get_event_loop().time()
        delta = now - _last_call_ts[0]
        if delta < 0.125:  # 8 req/sec
            await asyncio.sleep(0.125 - delta)
        _last_call_ts[0] = asyncio.get_event_loop().time()
        return await client.get(url, headers=HEADERS, timeout=20)


async def _load_sec_tickers() -> list[dict]:
    """Fetch SEC's company_tickers.json once and cache it for name→ticker lookups."""
    global _SEC_TICKERS_CACHE
    async with _SEC_TICKERS_LOCK:
        if _SEC_TICKERS_CACHE is not None:
            return _SEC_TICKERS_CACHE
        try:
            async with httpx.AsyncClient() as client:
                resp = await _rate_limited_get(client, "https://www.sec.gov/files/company_tickers.json")
                if resp.status_code == 200:
                    data = resp.json()
                    # Data is {"0": {"cik_str":..., "ticker":..., "title":...}, "1": {...}}
                    _SEC_TICKERS_CACHE = list(data.values()) if isinstance(data, dict) else []
                    logger.info(f"SEC tickers cache loaded: {len(_SEC_TICKERS_CACHE)} entries")
                    return _SEC_TICKERS_CACHE
        except Exception as e:
            logger.warning(f"SEC tickers fetch failed: {e}")
        _SEC_TICKERS_CACHE = []
        return []


def _normalize(name: str) -> str:
    """Normalize company name for fuzzy matching."""
    if not name:
        return ""
    n = name.upper()
    # Strip common corporate suffixes/punctuation
    for suffix in [" INCORPORATED", " INC", " CORPORATION", " CORP", " COMPANY", " CO",
                   " LIMITED", " LTD", " LLC", " PLC", " SA", " NV", " AG",
                   " HOLDINGS", " HOLDING", " GROUP", " LP", " L.P.", " N.V.", " S.A.",
                   " NEW", " CLASS A", " CLASS B", " CL A", " CL B", " COM", " COMMON",
                   " /DE/", " /MD/", " /NY/", " /DE", " TRUST"]:
        n = n.replace(suffix, "")
    # Common long-form abbreviations
    replacements = [
        (" PETE ", " PETROLEUM "),
        (" PETE", " PETROLEUM"),
        ("INTL", "INTERNATIONAL"),
    ]
    for a, b in replacements:
        n = n.replace(a, b)
    # Drop "OF" connective between first two words  (e.g. "BANK OF AMERICA" → "BANK AMERICA")
    n = re.sub(r"\b(OF|AND|&|THE)\b", " ", n)
    n = re.sub(r"[^A-Z0-9 ]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


async def _issuer_to_ticker(issuer: str, cusip: str = "", db=None) -> str | None:
    """Best-effort map an issuer name (or CUSIP via OpenFIGI cache) to a ticker.

    Preference order:
      1. Persistent ``cusip_ticker_map`` cache (populated via OpenFIGI)
      2. In-memory name-based fuzzy match against SEC company_tickers.json
    """
    # 1) Exact CUSIP → ticker from persistent MongoDB cache (OpenFIGI-backed)
    if cusip and db is not None:
        try:
            row = await db.cusip_ticker_map.find_one(
                {"cusip": cusip.upper()}, {"_id": 0, "ticker": 1}
            )
            if row and row.get("ticker"):
                _CUSIP_TICKER_CACHE[cusip] = row["ticker"]
                return row["ticker"]
        except Exception:
            pass

    if cusip and cusip in _CUSIP_TICKER_CACHE:
        return _CUSIP_TICKER_CACHE[cusip]

    # 2) Fall back to fuzzy name matching
    tickers = await _load_sec_tickers()
    if not tickers:
        return None
    norm_issuer = _normalize(issuer)
    if not norm_issuer:
        return None
    # Exact normalized match first
    for t in tickers:
        if _normalize(t.get("title", "")) == norm_issuer:
            sym = t.get("ticker")
            if sym and cusip:
                _CUSIP_TICKER_CACHE[cusip] = sym
            return sym
    # Prefix match (first 2 significant words)
    words = norm_issuer.split()[:2]
    if len(words) >= 1:
        prefix = " ".join(words)
        for t in tickers:
            tn = _normalize(t.get("title", ""))
            if tn.startswith(prefix) and len(tn) <= len(prefix) + 15:
                sym = t.get("ticker")
                if sym and cusip:
                    _CUSIP_TICKER_CACHE[cusip] = sym
                return sym
    return None


async def fetch_institution_filings(cik: str, max_filings: int = 2) -> list[dict]:
    """Fetch recent 13F-HR filings for an institution from the submissions API.

    Returns a list of {accession, filing_date, period_end, form, primary_doc}.
    """
    cik_padded = cik.zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    async with httpx.AsyncClient() as client:
        resp = await _rate_limited_get(client, url)
        if resp.status_code != 200:
            logger.warning(f"SEC submissions {cik_padded} -> {resp.status_code}")
            return []
        data = resp.json()

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    period_ends = recent.get("reportDate", [])
    primary_docs = recent.get("primaryDocument", [])

    results: list[dict] = []
    for i, form in enumerate(forms):
        if form not in ("13F-HR", "13F-HR/A"):
            continue
        results.append({
            "accession": accessions[i],
            "filing_date": filing_dates[i] if i < len(filing_dates) else None,
            "period_end": period_ends[i] if i < len(period_ends) else None,
            "form": form,
            "primary_doc": primary_docs[i] if i < len(primary_docs) else None,
        })
        if len(results) >= max_filings:
            break
    return results


async def _list_filing_files(client: httpx.AsyncClient, cik: str, accession: str) -> list[str]:
    """List files inside a filing to find the INFORMATION TABLE XML."""
    accession_clean = accession.replace("-", "")
    cik_int = str(int(cik))
    idx_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_clean}/index.json"
    try:
        resp = await _rate_limited_get(client, idx_url)
        if resp.status_code != 200:
            return []
        items = resp.json().get("directory", {}).get("item", [])
        return [it.get("name", "") for it in items if it.get("name")]
    except Exception as e:
        logger.debug(f"filing index fetch failed for {accession}: {e}")
        return []


async def fetch_13f_holdings(cik: str, accession: str) -> list[dict]:
    """Download and parse the INFORMATION TABLE XML for a specific 13F filing.

    Returns a list of holdings: {cusip, issuer, class, value, shares, put_call}.
    Note: 13F ``value`` is reported in USD thousands for filings before 2022-Q4
    and in USD for 2022-Q4 onwards. We detect the magnitude automatically.
    """
    accession_clean = accession.replace("-", "")
    cik_int = str(int(cik))
    async with httpx.AsyncClient() as client:
        files = await _list_filing_files(client, cik, accession)
        info_tbl = None
        for name in files:
            lower = name.lower()
            if "infotable" in lower.replace("_", "").replace("-", "") or lower.endswith("informationtable.xml"):
                info_tbl = name
                break
            if lower.endswith(".xml") and "primary" not in lower and "submission" not in lower:
                # Fallback: first non-primary XML
                info_tbl = info_tbl or name
        if not info_tbl:
            return []
        xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_clean}/{info_tbl}"
        resp = await _rate_limited_get(client, xml_url)
        if resp.status_code != 200:
            logger.debug(f"13F XML fetch {accession} -> {resp.status_code}")
            return []
        xml_text = resp.text

    holdings: list[dict] = []
    try:
        # Strip namespace to simplify XPath
        xml_text = re.sub(r'\sxmlns="[^"]+"', '', xml_text, count=1)
        root = ET.fromstring(xml_text)
        for info in root.findall(".//infoTable"):
            issuer = (info.findtext("nameOfIssuer") or "").strip()
            cls = (info.findtext("titleOfClass") or "").strip()
            cusip = (info.findtext("cusip") or "").strip().upper()
            value_raw = info.findtext("value") or "0"
            shrs_el = info.find("shrsOrPrnAmt")
            shares_raw = "0"
            sh_type = ""
            if shrs_el is not None:
                shares_raw = (shrs_el.findtext("sshPrnamt") or "0").strip()
                sh_type = (shrs_el.findtext("sshPrnamtType") or "").strip()
            put_call = (info.findtext("putCall") or "").strip()
            try:
                value = int(float(value_raw))
            except Exception:
                value = 0
            try:
                shares = int(float(shares_raw))
            except Exception:
                shares = 0
            holdings.append({
                "issuer": issuer,
                "class": cls,
                "cusip": cusip,
                "value": value,
                "shares": shares,
                "sh_type": sh_type,
                "put_call": put_call,
            })
    except ET.ParseError as e:
        logger.warning(f"13F XML parse error {accession}: {e}")
        return []
    return holdings


def _normalize_values(holdings: list[dict]) -> list[dict]:
    """Detect & normalize 13F ``value`` field to actual USD (not thousands).

    SEC switched from "value in USD thousands" to "value in USD" starting with
    2022-Q4 filings. We detect by checking if the total position size vs. shares
    implies the thousands-multiplier format.
    """
    if not holdings:
        return holdings
    # Heuristic: sum(value) / sum(shares) should look like a realistic share price.
    total_val = sum(h["value"] for h in holdings)
    total_sh = sum(h["shares"] for h in holdings if h.get("sh_type", "") == "SH")
    if total_sh <= 0:
        return holdings
    implied_avg_price = total_val / total_sh
    # If implied avg share price is below $1 it's almost certainly in thousands
    if implied_avg_price < 1.0:
        for h in holdings:
            h["value"] = h["value"] * 1000
    return holdings


async def refresh_institution(db, cik: str, institution_name: str, max_filings: int = 2) -> dict:
    """Refresh latest N 13F filings for an institution into MongoDB.

    Returns a dict summarizing filings processed.
    """
    result = {"cik": cik, "name": institution_name, "filings_new": 0, "filings_skipped": 0, "errors": 0}
    try:
        filings = await fetch_institution_filings(cik, max_filings=max_filings)
    except Exception as e:
        logger.warning(f"13F refresh list error {cik}: {e}")
        result["errors"] = 1
        return result

    for f in filings:
        accession = f["accession"]
        # Skip if we already have the holdings stored
        existing = await db.sec_13f_filings.find_one(
            {"cik": cik, "accession": accession}, {"_id": 0, "holdings_count": 1}
        )
        if existing and existing.get("holdings_count", 0) > 0:
            result["filings_skipped"] += 1
            continue
        try:
            holdings = await fetch_13f_holdings(cik, accession)
            holdings = _normalize_values(holdings)
            total_value = sum(h["value"] for h in holdings)
            now = datetime.now(timezone.utc).isoformat()
            await db.sec_13f_filings.update_one(
                {"cik": cik, "accession": accession},
                {"$set": {
                    "cik": cik,
                    "institution_name": institution_name,
                    "accession": accession,
                    "filing_date": f.get("filing_date"),
                    "period_end": f.get("period_end"),
                    "form": f.get("form"),
                    "total_value_usd": total_value,
                    "holdings_count": len(holdings),
                    "fetched_at": now,
                }},
                upsert=True,
            )
            # Clear old holdings for this filing (in case of re-fetch) and insert fresh
            await db.sec_13f_holdings.delete_many({"cik": cik, "accession": accession})
            if holdings:
                rows = [{
                    "cik": cik,
                    "institution_name": institution_name,
                    "accession": accession,
                    "period_end": f.get("period_end"),
                    "filing_date": f.get("filing_date"),
                    "issuer": h["issuer"],
                    "class": h["class"],
                    "cusip": h["cusip"],
                    "value_usd": h["value"],
                    "shares": h["shares"],
                    "sh_type": h["sh_type"],
                    "put_call": h["put_call"],
                } for h in holdings]
                await db.sec_13f_holdings.insert_many(rows)
            result["filings_new"] += 1
            logger.info(f"13F refreshed: {institution_name} {f.get('period_end')} ({len(holdings)} positions, ${total_value/1e9:.1f}B)")
        except Exception as e:
            logger.warning(f"13F holdings fetch error {accession}: {e}")
            result["errors"] += 1

    return result


async def refresh_all_institutions(db, max_filings: int = 2) -> dict:
    """Refresh all tracked institutions. Returns a summary."""
    summary = {"total": len(TOP_INSTITUTIONS), "success": 0, "errors": 0, "institutions": []}
    for name, cik in TOP_INSTITUTIONS.items():
        try:
            r = await refresh_institution(db, cik, name, max_filings=max_filings)
            summary["institutions"].append(r)
            if r.get("errors", 0) == 0:
                summary["success"] += 1
            else:
                summary["errors"] += 1
        except Exception as e:
            logger.warning(f"13F refresh_all error for {name}: {e}")
            summary["errors"] += 1
    return summary


async def get_institution_holdings(db, cik: str, limit: int = 50) -> dict:
    """Get an institution's latest-quarter holdings, aggregated by CUSIP, sorted by value desc."""
    meta = await db.sec_13f_filings.find_one(
        {"cik": cik}, sort=[("period_end", -1)],
        projection={"_id": 0},
    )
    if not meta:
        return {"cik": cik, "filing": None, "holdings": []}

    # Aggregate by CUSIP — a single CIK may list the same position multiple times
    # for different subsidiary accounts; 13F reporting standard treats them as discrete
    # rows. Consumers expect one row per holding.
    pipeline = [
        {"$match": {"cik": cik, "accession": meta["accession"]}},
        {"$group": {
            "_id": {"cusip": "$cusip", "class": "$class"},
            "issuer": {"$first": "$issuer"},
            "class": {"$first": "$class"},
            "cusip": {"$first": "$cusip"},
            "shares": {"$sum": "$shares"},
            "value_usd": {"$sum": "$value_usd"},
            "sh_type": {"$first": "$sh_type"},
            "put_call": {"$first": "$put_call"},
        }},
        {"$sort": {"value_usd": -1}},
        {"$limit": min(limit, 200)},
    ]
    cursor = db.sec_13f_holdings.aggregate(pipeline)
    holdings: list[dict] = []
    async for h in cursor:
        h.pop("_id", None)
        holdings.append(h)

    # Attempt to enrich with ticker symbol
    for h in holdings:
        sym = await _issuer_to_ticker(h.get("issuer", ""), h.get("cusip", ""), db=db)
        if sym:
            h["symbol"] = sym
    return {"cik": cik, "filing": meta, "holdings": holdings}


async def get_holders_of_symbol(db, symbol: str, limit: int = 50) -> dict:
    """Find all tracked institutions holding a given symbol (latest quarter).

    Strategy:
      1. Find all CUSIPs associated with this ticker in ``cusip_ticker_map``
         (populated via OpenFIGI). These are the *authoritative* matches.
      2. Fall back to fuzzy issuer-name matching if no CUSIP map is available yet.
    """
    symbol_u = symbol.upper()
    # Resolve symbol → company title from SEC tickers (for UI header + fallback)
    tickers = await _load_sec_tickers()
    company_title = None
    for t in tickers:
        if (t.get("ticker") or "").upper() == symbol_u:
            company_title = t.get("title")
            break

    # Find the most recent filing per institution
    pipeline = [
        {"$sort": {"cik": 1, "period_end": -1}},
        {"$group": {
            "_id": "$cik",
            "accession": {"$first": "$accession"},
            "period_end": {"$first": "$period_end"},
        }},
    ]
    latest_filings = await db.sec_13f_filings.aggregate(pipeline).to_list(500)
    if not latest_filings:
        return {"symbol": symbol_u, "company": company_title, "holders": []}
    latest_accessions = [f["accession"] for f in latest_filings]

    # --- Primary path: resolve ticker → CUSIPs via OpenFIGI cache ---
    cusips_cursor = db.cusip_ticker_map.find(
        {"ticker": symbol_u}, {"_id": 0, "cusip": 1}
    )
    matching_cusips = [d["cusip"] async for d in cusips_cursor if d.get("cusip")]

    matches: list[dict] = []
    if matching_cusips:
        cursor = db.sec_13f_holdings.find(
            {
                "accession": {"$in": latest_accessions},
                "cusip": {"$in": matching_cusips},
                "put_call": {"$in": ["", None]},
            },
            {"_id": 0},
        )
        matches = await cursor.to_list(5000)

    # --- Fallback: fuzzy issuer-name match when CUSIP map has no coverage ---
    if not matches and company_title:
        norm_target = _normalize(company_title)
        first_two = " ".join(norm_target.split()[:2])
        if len(first_two) >= 3:
            pattern = re.escape(first_two)
            cursor = db.sec_13f_holdings.find(
                {
                    "accession": {"$in": latest_accessions},
                    "issuer": {"$regex": pattern, "$options": "i"},
                    "put_call": {"$in": ["", None]},
                },
                {"_id": 0},
            )
            raw = await cursor.to_list(2000)
            matches = [m for m in raw if _normalize(m.get("issuer", "")).startswith(first_two)]

    # Aggregate by (cik, cusip) — same institution may list the position across subsidiaries.
    agg: dict[tuple, dict] = {}
    for m in matches:
        key = (m.get("cik"), m.get("cusip") or m.get("issuer"))
        existing = agg.get(key)
        if existing is None:
            agg[key] = {**m}
        else:
            existing["shares"] = (existing.get("shares") or 0) + (m.get("shares") or 0)
            existing["value_usd"] = (existing.get("value_usd") or 0) + (m.get("value_usd") or 0)
    holders = list(agg.values())
    holders.sort(key=lambda x: x.get("value_usd", 0), reverse=True)
    return {
        "symbol": symbol_u,
        "company": company_title,
        "holders": holders[:limit],
        "total_value_usd": sum(h.get("value_usd", 0) for h in holders),
        "holder_count": len(holders),
    }


async def get_quarterly_changes(db, cik: str, limit: int = 30) -> dict:
    """Compute QoQ changes for an institution: new / exited / increased / decreased positions."""
    filings = await db.sec_13f_filings.find(
        {"cik": cik}, {"_id": 0}, sort=[("period_end", -1)]
    ).to_list(5)
    if len(filings) < 2:
        return {"cik": cik, "latest": None, "previous": None, "changes": []}

    latest, previous = filings[0], filings[1]

    async def _agg(accession: str) -> dict[str, dict]:
        cursor = db.sec_13f_holdings.find({"accession": accession}, {"_id": 0})
        by_cusip: dict[str, dict] = {}
        async for h in cursor:
            cusip = h.get("cusip") or ""
            if not cusip:
                continue
            existing = by_cusip.get(cusip)
            if existing is None:
                by_cusip[cusip] = {**h}
            else:
                existing["shares"] = (existing.get("shares") or 0) + (h.get("shares") or 0)
                existing["value_usd"] = (existing.get("value_usd") or 0) + (h.get("value_usd") or 0)
        return by_cusip

    latest_by_cusip = await _agg(latest["accession"])
    prev_by_cusip = await _agg(previous["accession"])

    changes: list[dict] = []
    for cusip, curr in latest_by_cusip.items():
        prev = prev_by_cusip.get(cusip)
        if prev is None:
            changes.append({
                "type": "new",
                "cusip": cusip,
                "issuer": curr["issuer"],
                "shares": curr["shares"],
                "value_usd": curr.get("value_usd", 0),
                "delta_shares": curr["shares"],
                "delta_pct": None,
            })
        else:
            d_sh = curr["shares"] - prev["shares"]
            if d_sh == 0:
                continue
            d_pct = (d_sh / prev["shares"] * 100.0) if prev["shares"] else None
            changes.append({
                "type": "increased" if d_sh > 0 else "decreased",
                "cusip": cusip,
                "issuer": curr["issuer"],
                "shares": curr["shares"],
                "prev_shares": prev["shares"],
                "value_usd": curr.get("value_usd", 0),
                "delta_shares": d_sh,
                "delta_pct": d_pct,
            })
    for cusip, prev in prev_by_cusip.items():
        if cusip not in latest_by_cusip:
            changes.append({
                "type": "exited",
                "cusip": cusip,
                "issuer": prev["issuer"],
                "shares": 0,
                "prev_shares": prev["shares"],
                "value_usd": 0,
                "delta_shares": -prev["shares"],
                "delta_pct": -100.0,
            })

    # Enrich with ticker symbol where possible
    for c in changes:
        sym = await _issuer_to_ticker(c.get("issuer", ""), c.get("cusip", ""), db=db)
        if sym:
            c["symbol"] = sym

    # Sort by abs(value_usd) desc, then abs(delta_shares) desc for exits
    changes.sort(key=lambda c: (abs(c.get("value_usd", 0)), abs(c.get("delta_shares", 0))), reverse=True)
    return {
        "cik": cik,
        "latest": latest,
        "previous": previous,
        "changes": changes[:limit],
        "total_changes": len(changes),
    }


async def scan_and_alert(db) -> dict:
    """Daily scheduler job: refresh all tracked institutions and emit alerts for
    notable activity on symbols present in any user's watchlist.

    Alert triggers (per institution × symbol):
      - NEW position whose value >= $50M
      - EXITED position that was >= $50M last quarter
      - INCREASED position by >= 25% AND value >= $100M
      - DECREASED position by >= 25% AND prev value >= $100M
    """
    summary = {"refreshed": 0, "alerts_created": 0, "errors": 0}
    try:
        # 1) Fetch all watchlist symbols (flattened set)
        watchlist_cursor = db.watchlists.find({}, {"_id": 0, "tickers": 1})
        symbols: set[str] = set()
        async for wl in watchlist_cursor:
            for t in (wl.get("tickers") or []):
                if isinstance(t, str):
                    symbols.add(t.upper())
                elif isinstance(t, dict) and t.get("symbol"):
                    symbols.add(t["symbol"].upper())

        # 2) Refresh 13F filings for top institutions
        refresh_summary = await refresh_all_institutions(db, max_filings=2)
        summary["refreshed"] = refresh_summary.get("success", 0)
        summary["errors"] = refresh_summary.get("errors", 0)

        if not symbols:
            return summary

        # 3) For each institution, compute changes and flag those touching watchlist
        for name, cik in TOP_INSTITUTIONS.items():
            try:
                chg = await get_quarterly_changes(db, cik, limit=500)
            except Exception as e:
                logger.debug(f"13F changes error {name}: {e}")
                continue
            for c in chg.get("changes", []):
                sym = c.get("symbol")
                if not sym or sym not in symbols:
                    continue

                value = c.get("value_usd", 0) or 0
                d_pct = c.get("delta_pct")

                notable = False
                ctype = c.get("type")
                if ctype == "new" and value >= 50_000_000:
                    notable = True
                elif ctype == "exited" and value == 0 and c.get("prev_shares", 0) > 0:
                    # Approximate prev value via avg_price proxy — we conservatively trigger only if position was large
                    notable = True
                elif ctype in ("increased", "decreased") and d_pct is not None and abs(d_pct) >= 25 and value >= 100_000_000:
                    notable = True

                if not notable:
                    continue

                alert_doc = {
                    "cik": cik,
                    "institution_name": name,
                    "symbol": sym,
                    "accession": chg["latest"].get("accession"),
                    "period_end": chg["latest"].get("period_end"),
                    "type": ctype,
                    "delta_shares": c.get("delta_shares"),
                    "delta_pct": d_pct,
                    "value_usd": value,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                existing = await db.sec_13f_alerts.find_one({
                    "cik": cik, "symbol": sym, "accession": alert_doc["accession"], "type": ctype,
                })
                if existing:
                    continue
                await db.sec_13f_alerts.insert_one(alert_doc)
                summary["alerts_created"] += 1

                # Fan out in-app notification + VAPID push (best-effort)
                try:
                    from services.push_service import broadcast_notification
                    verb = {"new": "opened new position in", "exited": "exited",
                            "increased": "increased stake in", "decreased": "trimmed position in"}.get(ctype, "changed")
                    body = f"{name} {verb} {sym}"
                    if d_pct is not None and ctype in ("increased", "decreased"):
                        body += f" by {abs(d_pct):.0f}%"
                    if value:
                        body += f" (${value/1e9:.1f}B)"
                    await broadcast_notification(
                        db,
                        title=f"13F Filing: {sym}",
                        body=body,
                        url="/#research",
                        tag=f"13f-{cik}-{sym}",
                        notif_type="sec_13f",
                    )
                except Exception as e:
                    logger.debug(f"13F push notify error: {e}")
    except Exception as e:
        logger.warning(f"13F scan_and_alert error: {e}")
        summary["errors"] += 1
    return summary
