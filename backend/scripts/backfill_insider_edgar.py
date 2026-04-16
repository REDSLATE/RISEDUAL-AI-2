"""Backfill ``insider_activity`` from SEC EDGAR Form 4 filings.

Uses the EDGAR API (no key required, 10 req/sec with User-Agent) to:
1. Look up each ticker's CIK via ``data.sec.gov/submissions``
2. Fetch all Form 4 filing accession numbers from the submissions JSON
3. Download + parse each Form 4 XML for transaction details
4. Compute monthly normalized insider activity scores
5. Bulk-update MongoDB ``features_snapshots``

The score formula is identical to backfill_insider_activity.py:
    insider_activity = (buy_shares - sell_shares) / (buy_shares + sell_shares)
                     ∈ [-1.0, 1.0]

Rate limiting: EDGAR allows 10 req/sec with a User-Agent identifying the requester.
Disk cache: Parsed transactions are cached to ``~/.risedual/edgar_cache/<TICKER>.json``.

Usage:
    python scripts/backfill_insider_edgar.py
    python scripts/backfill_insider_edgar.py --ticker AAPL --force-fetch
    python scripts/backfill_insider_edgar.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("edgar_insider")

# ── Configuration ─────────────────────────────────────────────────────────────

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

MONGO_URI = os.getenv("MONGO_URL", os.getenv("MONGO_URI", "mongodb://localhost:27017"))
DB_NAME = os.getenv("DB_NAME", "risedual_db")
COLLECTION = "features_snapshots"

USER_AGENT = "RISEDUAL risedual@risedual.ai"
SEC_BASE = "https://data.sec.gov"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

CACHE_DIR: Path = Path.home() / ".risedual" / "edgar_cache"

EQUITY_TICKERS: list[str] = [
    "AAPL", "MSFT", "NVDA", "GOOG", "AMZN", "META", "TSLA", "AVGO", "ORCL",
    "JPM", "V", "MA", "BAC", "WFC", "GS", "AXP", "BLK",
    "UNH", "LLY", "JNJ", "TMO", "ABT", "SYK",
    "XOM", "CVX", "GE", "HON", "CAT", "UPS", "LMT", "RTX",
    "WMT", "HD", "COST", "TGT", "SBUX", "MCD", "NKE", "PG", "KO",
]

BUY_CODES = {"P", "A", "M", "C", "G", "J", "K"}
SELL_CODES = {"S", "D", "F"}
BATCH_SIZE = 500
MAX_FILINGS_PER_TICKER = 2000
CONCURRENT_XML_FETCHES = 1  # sequential to respect EDGAR 10 req/sec


# ── Rate limiter (10 req/sec for EDGAR) ──────────────────────────────────────

class _RateLimiter:
    def __init__(self, max_per_sec: float = 3.0):  # EDGAR strict limit
        self._interval = 1.0 / max_per_sec
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


# ── CIK lookup ────────────────────────────────────────────────────────────────

async def lookup_cik(
    client: httpx.AsyncClient,
    ticker: str,
    limiter: _RateLimiter,
) -> str | None:
    """Look up a ticker's CIK from SEC tickers.json."""
    await limiter.acquire()
    try:
        resp = await client.get(
            f"{SEC_BASE}/submissions/CIK{_ticker_to_cik_cache.get(ticker, '0000000000')}.json",
            timeout=15.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            tickers = [t.upper() for t in data.get("tickers", [])]
            if ticker.upper() in tickers:
                return data["cik"]
    except Exception:
        pass

    # Fallback: search the ticker mapping
    await limiter.acquire()
    try:
        resp = await client.get(
            "https://www.sec.gov/files/company_tickers.json",
            timeout=15.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            for entry in data.values():
                if entry.get("ticker", "").upper() == ticker.upper():
                    cik = str(entry["cik_str"]).zfill(10)
                    _ticker_to_cik_cache[ticker] = cik
                    return cik
    except Exception as exc:
        log.warning("[%s] CIK lookup failed: %s", ticker, exc)

    return None


# Pre-populated CIK cache for common tickers
_ticker_to_cik_cache: dict[str, str] = {
    "AAPL": "0000320193", "MSFT": "0000789019", "NVDA": "0001045810",
    "GOOG": "0001652044", "AMZN": "0001018724", "META": "0001326801",
    "TSLA": "0001318605", "AVGO": "0001649338", "ORCL": "0001341439",
    "JPM": "0000019617", "V": "0001403161", "MA": "0001141391",
    "BAC": "0000070858", "WFC": "0000072971", "GS": "0000886982",
    "AXP": "0000004962", "BLK": "0001364742", "UNH": "0000731766",
    "LLY": "0000059478", "JNJ": "0000200406", "TMO": "0000097745",
    "ABT": "0000001800", "SYK": "0000310764", "XOM": "0000034088",
    "CVX": "0000093410", "GE": "0000040554", "HON": "0000773840",
    "CAT": "0000018230", "UPS": "0001090727", "LMT": "0000936468",
    "RTX": "0000101829", "WMT": "0000104169", "HD": "0000354950",
    "COST": "0000909832", "TGT": "0000027419", "SBUX": "0000829224",
    "MCD": "0000063908", "NKE": "0000320187", "PG": "0000080424",
    "KO": "0000021344",
}


# ── Fetch Form 4 filings list ────────────────────────────────────────────────

async def fetch_form4_accessions(
    client: httpx.AsyncClient,
    cik: str,
    limiter: _RateLimiter,
    max_filings: int = MAX_FILINGS_PER_TICKER,
) -> list[dict]:
    """Fetch Form 4 filing accession numbers + dates from EDGAR submissions."""
    accessions = []
    await limiter.acquire()

    try:
        resp = await client.get(
            f"{SEC_BASE}/submissions/CIK{cik}.json",
            timeout=20.0,
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accession_nums = recent.get("accessionNumber", [])

        for i, form in enumerate(forms):
            if form == "4" and len(accessions) < max_filings:
                accessions.append({
                    "accession": accession_nums[i],
                    "date": dates[i],
                })

        # Check for older filings in separate files
        older_files = data.get("filings", {}).get("files", [])
        for file_info in older_files[:5]:  # limit to 5 older filing pages
            if len(accessions) >= max_filings:
                break
            fname = file_info.get("name")
            if not fname:
                continue
            await limiter.acquire()
            try:
                resp2 = await client.get(
                    f"{SEC_BASE}/submissions/{fname}",
                    timeout=20.0,
                )
                if resp2.status_code == 200:
                    old_data = resp2.json()
                    old_forms = old_data.get("form", [])
                    old_dates = old_data.get("filingDate", [])
                    old_accessions = old_data.get("accessionNumber", [])
                    for j, f in enumerate(old_forms):
                        if f == "4" and len(accessions) < max_filings:
                            accessions.append({
                                "accession": old_accessions[j],
                                "date": old_dates[j],
                            })
            except Exception:
                pass

    except Exception as exc:
        log.warning("Failed to fetch submissions for CIK %s: %s", cik, exc)

    return accessions


# ── Parse Form 4 XML ─────────────────────────────────────────────────────────

def parse_form4_xml(xml_text: str, ticker: str) -> list[dict]:
    """Extract transactions from a Form 4 XML filing."""
    transactions = []
    try:
        root = ET.fromstring(xml_text)
        # Strip namespaces
        for elem in root.iter():
            elem.tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

        issuer_ticker = (root.findtext(".//issuerTradingSymbol") or "").upper()
        reporter = root.findtext(".//rptOwnerName") or ""

        for txn in root.findall(".//nonDerivativeTransaction"):
            date = txn.findtext(".//transactionDate/value", "")
            code = txn.findtext(".//transactionCoding/transactionCode", "")
            shares_str = txn.findtext(".//transactionAmounts/transactionShares/value", "0")
            price_str = txn.findtext(".//transactionAmounts/transactionPricePerShare/value", "0")
            acq_disp = txn.findtext(".//transactionAmounts/transactionAcquiredDisposedCode/value", "")

            try:
                shares = abs(float(shares_str))
            except (ValueError, TypeError):
                shares = 0.0
            try:
                price = float(price_str)
            except (ValueError, TypeError):
                price = 0.0

            if shares > 0 and date:
                transactions.append({
                    "ticker": issuer_ticker or ticker,
                    "date": date,
                    "code": code.upper(),
                    "acq_disp": acq_disp.upper(),
                    "shares": shares,
                    "price": price,
                    "reporter": reporter,
                })

    except ET.ParseError:
        pass
    except Exception as exc:
        log.debug("XML parse error: %s", exc)

    return transactions


# ── Fetch + parse Form 4 XMLs ────────────────────────────────────────────────

async def fetch_and_parse_form4(
    client: httpx.AsyncClient,
    cik: str,
    accession: str,
    ticker: str,
    limiter: _RateLimiter,
) -> list[dict]:
    """Download a single Form 4 XML and parse transactions."""
    acc_nodash = accession.replace("-", "")
    cik_num = cik.lstrip("0") or "0"

    # Try common XML filenames
    for fname in ["form4.xml"]:
        await limiter.acquire()
        url = f"{ARCHIVES_BASE}/{cik_num}/{acc_nodash}/{fname}"
        try:
            resp = await client.get(url, timeout=10.0)
            if resp.status_code == 429:
                log.debug("[%s] 429 — sleeping 30s", ticker)
                await asyncio.sleep(30)
                await limiter.acquire()
                resp = await client.get(url, timeout=10.0)
            if resp.status_code == 200 and "<ownershipDocument" in resp.text:
                return parse_form4_xml(resp.text, ticker)
        except Exception:
            pass

    # Single fallback try with doc4.xml
    await limiter.acquire()
    try:
        url = f"{ARCHIVES_BASE}/{cik_num}/{acc_nodash}/doc4.xml"
        resp = await client.get(url, timeout=10.0)
        if resp.status_code == 429:
            await asyncio.sleep(30)
            return []
        if resp.status_code == 200 and "<ownershipDocument" in resp.text:
            return parse_form4_xml(resp.text, ticker)
    except Exception:
        pass

    return []


# ── Process one ticker ────────────────────────────────────────────────────────

async def process_ticker(
    ticker: str,
    db: Any,
    client: httpx.AsyncClient,
    limiter: _RateLimiter,
    force_fetch: bool,
    force_write: bool,
    dry_run: bool,
) -> dict[str, Any]:
    """Full pipeline for one ticker: CIK → filings → XML → scores → MongoDB."""
    t0 = time.perf_counter()
    result = {"ticker": ticker, "filings": 0, "transactions": 0, "rows_updated": 0, "error": None}

    # Check cache
    cache_path = CACHE_DIR / f"{ticker}.json"
    all_transactions: list[dict] = []

    if not force_fetch and cache_path.exists():
        try:
            all_transactions = json.loads(cache_path.read_text())
            log.info("[%s] Loaded %d cached transactions", ticker, len(all_transactions))
        except Exception:
            all_transactions = []

    if not all_transactions:
        # Look up CIK
        cik = _ticker_to_cik_cache.get(ticker)
        if not cik:
            cik = await lookup_cik(client, ticker, limiter)
        if not cik:
            result["error"] = "CIK not found"
            return result

        # Get Form 4 accessions
        accessions = await fetch_form4_accessions(client, cik, limiter)
        result["filings"] = len(accessions)

        if not accessions:
            log.info("[%s] No Form 4 filings found", ticker)
            return result

        log.info("[%s] Found %d Form 4 filings", ticker, len(accessions))

        # Fetch XMLs sequentially (respect EDGAR rate limit)
        for acc_info in accessions:
            txns = await fetch_and_parse_form4(
                client, cik, acc_info["accession"], ticker, limiter
            )
            all_transactions.extend(txns)
            if len(all_transactions) % 100 == 0 and all_transactions:
                log.info("[%s] Progress: %d transactions from %d filings so far",
                         ticker, len(all_transactions), accessions.index(acc_info) + 1)

        # Cache to disk
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(all_transactions, separators=(",", ":")))

    result["transactions"] = len(all_transactions)

    if not all_transactions:
        return result

    # Compute monthly scores
    monthly_buys: dict[str, float] = defaultdict(float)
    monthly_sells: dict[str, float] = defaultdict(float)

    for txn in all_transactions:
        date = txn.get("date", "")
        if len(date) < 7:
            continue
        month_key = date[:7]
        code = txn.get("code", "").upper()
        acq_disp = txn.get("acq_disp", "").upper()
        shares = txn.get("shares", 0)

        if code in BUY_CODES or acq_disp == "A":
            monthly_buys[month_key] += shares
        elif code in SELL_CODES or acq_disp == "D":
            monthly_sells[month_key] += shares

    monthly_scores: dict[str, float] = {}
    for month in set(monthly_buys.keys()) | set(monthly_sells.keys()):
        b = monthly_buys.get(month, 0)
        s = monthly_sells.get(month, 0)
        total = b + s
        monthly_scores[month] = round((b - s) / total, 6) if total > 0 else 0.0

    # Update MongoDB
    coll = db[COLLECTION]
    query: dict[str, Any] = {"ticker": ticker}
    if not force_write:
        query["$or"] = [{"insider_activity": None}, {"insider_source": {"$ne": "edgar_form4"}}]

    rows = await coll.find(query, {"_id": 1, "timestamp": 1}).to_list(length=None)

    ops: list[UpdateOne] = []
    for row in rows:
        ts = row.get("timestamp")
        if ts is None:
            continue
        month_key = ts.strftime("%Y-%m") if hasattr(ts, "strftime") else str(ts)[:7]
        score = monthly_scores.get(month_key)
        if score is None:
            continue
        ops.append(UpdateOne(
            {"_id": row["_id"]},
            {"$set": {"insider_activity": score, "insider_source": "edgar_form4"}},
        ))

    if ops and not dry_run:
        for i in range(0, len(ops), BATCH_SIZE):
            batch = ops[i:i + BATCH_SIZE]
            r = await coll.bulk_write(batch, ordered=False)
            result["rows_updated"] += r.modified_count
    elif ops:
        result["rows_updated"] = len(ops)

    elapsed = time.perf_counter() - t0
    log.info(
        "[%s] %d filings → %d txns → %d monthly scores → %d rows updated (%.1fs)",
        ticker, result["filings"], len(all_transactions),
        len(monthly_scores), result["rows_updated"], elapsed,
    )
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill insider_activity from SEC EDGAR Form 4")
    parser.add_argument("--ticker", type=str, default=None)
    parser.add_argument("--force-fetch", action="store_true")
    parser.add_argument("--force-write", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    tickers = [args.ticker.upper()] if args.ticker else EQUITY_TICKERS
    limiter = _RateLimiter(max_per_sec=9.0)

    print(f"\n{'='*60}")
    print("  SEC EDGAR FORM 4 INSIDER ACTIVITY BACKFILL")
    print(f"{'='*60}")
    print(f"  Tickers      : {len(tickers)}")
    print(f"  Max filings  : {MAX_FILINGS_PER_TICKER}/ticker")
    print(f"  Rate limit   : 9 req/sec (EDGAR fair use)")
    print(f"  Cache        : {CACHE_DIR}")
    print(f"  Dry run      : {args.dry_run}")
    print(f"{'='*60}\n")

    mongo = AsyncIOMotorClient(MONGO_URI)
    db = mongo[DB_NAME]
    summaries: list[dict] = []

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as client:
        for i, ticker in enumerate(tickers, 1):
            log.info("--- [%d/%d] %s ---", i, len(tickers))
            s = await process_ticker(
                ticker, db, client, limiter,
                force_fetch=args.force_fetch,
                force_write=args.force_write,
                dry_run=args.dry_run,
            )
            summaries.append(s)

    mongo.close()

    total_filings = sum(s["filings"] for s in summaries)
    total_txns = sum(s["transactions"] for s in summaries)
    total_updated = sum(s["rows_updated"] for s in summaries)
    failed = [s for s in summaries if s["error"]]

    print(f"\n{'='*60}")
    print("  EDGAR FORM 4 BACKFILL COMPLETE")
    print(f"{'='*60}")
    print(f"  Tickers processed : {len(summaries)}")
    print(f"  Total Form 4s     : {total_filings:,}")
    print(f"  Total transactions: {total_txns:,}")
    print(f"  Rows updated      : {total_updated:,}")
    print(f"  Failed            : {len(failed)}")
    if failed:
        for s in failed:
            print(f"    {s['ticker']:<10} {s['error']}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
