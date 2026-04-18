"""Fallback insider backfill via data.sec.gov/submissions API.

Uses the SEC's machine-readable bulk-access endpoint (data.sec.gov)
which is separate from www.sec.gov and not affected by its throttling.

This is a parallel/fallback to backfill_insider_edgar.py (EFTS-based).
Use this when EFTS is down or when you need faster per-ticker lookups.

Pipeline:
1. GET data.sec.gov/submissions/CIK{cik}.json → Form 4 accession list
2. For each accession, GET the filing index from data.sec.gov
3. Parse the filing index to find the XML filename
4. Fetch + parse the Form 4 XML
5. Compute monthly insider_activity scores
6. Bulk-update MongoDB

Rate limit: data.sec.gov allows 10 req/sec with User-Agent.

Usage:
    python scripts/backfill_insider_submissions.py
    python scripts/backfill_insider_submissions.py --ticker AAPL
    python scripts/backfill_insider_submissions.py --dry-run
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
log = logging.getLogger("submissions_insider")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

MONGO_URI = os.getenv("MONGO_URL", os.getenv("MONGO_URI", "mongodb://localhost:27017"))
DB_NAME = os.getenv("DB_NAME", "risedual_db")
COLLECTION = "features_snapshots"

USER_AGENT = "RISEDUAL risedual@risedual.ai"
DATA_SEC_BASE = "https://data.sec.gov"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

CACHE_DIR: Path = Path.home() / ".risedual" / "submissions_cache"
BATCH_SIZE = 500
MAX_FILINGS = 2000

BUY_CODES = {"P", "A", "M", "C", "G", "J", "K"}
SELL_CODES = {"S", "D", "F"}

# CIK mapping (same as backfill_insider_edgar.py)
TICKER_CIK: dict[str, str] = {
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

EQUITY_TICKERS = list(TICKER_CIK.keys())


class _RateLimiter:
    def __init__(self, max_per_sec: float = 8.0):
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


def parse_form4_xml(xml_text: str, ticker: str) -> list[dict]:
    """Extract transactions from Form 4 XML."""
    transactions = []
    try:
        root = ET.fromstring(xml_text)
        for elem in root.iter():
            elem.tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

        for txn in root.findall(".//nonDerivativeTransaction"):
            date = txn.findtext(".//transactionDate/value", "")
            code = txn.findtext(".//transactionCoding/transactionCode", "")
            shares_str = txn.findtext(".//transactionAmounts/transactionShares/value", "0")
            acq_disp = txn.findtext(".//transactionAmounts/transactionAcquiredDisposedCode/value", "")

            try:
                shares = abs(float(shares_str))
            except (ValueError, TypeError):
                shares = 0.0

            if shares > 0 and date:
                transactions.append({
                    "date": date,
                    "code": code.upper(),
                    "acq_disp": acq_disp.upper(),
                    "shares": shares,
                })
    except Exception:
        pass
    return transactions


async def get_form4_accessions(
    client: httpx.AsyncClient,
    cik: str,
    limiter: _RateLimiter,
) -> list[dict]:
    """Get Form 4 accessions + primary doc filenames from data.sec.gov."""
    accessions: list[dict] = []

    # Main submissions page
    await limiter.acquire()
    try:
        resp = await client.get(f"{DATA_SEC_BASE}/submissions/CIK{cik}.json", timeout=15.0)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception as exc:
        log.warning("Failed to fetch submissions for CIK %s: %s", cik, exc)
        return []

    def _extract_form4s(filing_data: dict) -> None:
        forms = filing_data.get("form", [])
        dates = filing_data.get("filingDate", [])
        acc_nums = filing_data.get("accessionNumber", [])
        primary_docs = filing_data.get("primaryDocument", [])
        for i, form in enumerate(forms):
            if form == "4" and len(accessions) < MAX_FILINGS:
                accessions.append({
                    "accession": acc_nums[i],
                    "date": dates[i],
                    "primary_doc": primary_docs[i] if i < len(primary_docs) else "",
                })

    _extract_form4s(data.get("filings", {}).get("recent", {}))

    # Older filing pages
    for file_info in data.get("filings", {}).get("files", [])[:10]:
        if len(accessions) >= MAX_FILINGS:
            break
        fname = file_info.get("name")
        if not fname:
            continue
        await limiter.acquire()
        try:
            resp2 = await client.get(f"{DATA_SEC_BASE}/submissions/{fname}", timeout=15.0)
            if resp2.status_code == 200:
                _extract_form4s(resp2.json())
        except Exception:
            pass

    return accessions


async def fetch_form4_xml(
    client: httpx.AsyncClient,
    cik: str,
    accession_info: dict,
    ticker: str,
    limiter: _RateLimiter,
) -> list[dict]:
    """Fetch and parse a single Form 4 XML."""
    acc = accession_info["accession"]
    acc_nodash = acc.replace("-", "")
    cik_num = cik.lstrip("0") or "0"
    primary_doc = accession_info.get("primary_doc", "")

    # Try primary_doc first (from submissions JSON)
    # primaryDocument can be "xslF345X03/wf-form4_167883310220723.xml" (XSLT path)
    # The actual XML is the filename part without the xsl prefix
    xml_candidates = []
    if primary_doc:
        # Strip XSL prefix if present: "xslF345X03/wf-form4_xxx.xml" → "wf-form4_xxx.xml"
        actual_name = primary_doc.split("/")[-1] if "/" in primary_doc else primary_doc
        if actual_name.endswith(".xml"):
            xml_candidates.append(actual_name)

    # Standard fallbacks
    xml_candidates.extend(["form4.xml", "doc4.xml"])

    for fname in xml_candidates:
        await limiter.acquire()
        url = f"{ARCHIVES_BASE}/{cik_num}/{acc_nodash}/{fname}"
        try:
            resp = await client.get(url, timeout=10.0)
            if resp.status_code == 429:
                await asyncio.sleep(30)
                return []
            if resp.status_code == 200 and "<ownershipDocument" in resp.text:
                return parse_form4_xml(resp.text, ticker)
        except Exception:
            pass

    return []


def compute_monthly_scores(txns: list[dict]) -> dict[str, float]:
    """Compute monthly insider_activity ∈ [-1, 1]."""
    buys: dict[str, float] = defaultdict(float)
    sells: dict[str, float] = defaultdict(float)
    for t in txns:
        d = t.get("date", "")
        if len(d) < 7:
            continue
        m = d[:7]
        code = t.get("code", "")
        acq = t.get("acq_disp", "")
        shares = t.get("shares", 0)
        if code in BUY_CODES or acq == "A":
            buys[m] += shares
        elif code in SELL_CODES or acq == "D":
            sells[m] += shares

    scores = {}
    for m in set(buys) | set(sells):
        b, s = buys.get(m, 0), sells.get(m, 0)
        total = b + s
        scores[m] = round((b - s) / total, 6) if total > 0 else 0.0
    return scores


async def process_ticker(
    ticker: str,
    db: Any,
    client: httpx.AsyncClient,
    limiter: _RateLimiter,
    force_write: bool,
    dry_run: bool,
) -> dict[str, int]:
    """Full pipeline for one ticker."""
    t0 = time.perf_counter()
    result = {"filings": 0, "transactions": 0, "rows_updated": 0}

    cik = TICKER_CIK.get(ticker)
    if not cik:
        log.warning("[%s] No CIK mapping", ticker)
        return result

    # Check cache
    cache_path = CACHE_DIR / f"{ticker}.json"
    all_txns: list[dict] = []

    if cache_path.exists():
        try:
            all_txns = json.loads(cache_path.read_text())
            log.info("[%s] Loaded %d cached transactions", ticker, len(all_txns))
        except Exception:
            pass

    if not all_txns:
        accessions = await get_form4_accessions(client, cik, limiter)
        result["filings"] = len(accessions)
        if not accessions:
            return result

        log.info("[%s] Found %d Form 4 filings via data.sec.gov", ticker, len(accessions))

        for idx, acc_info in enumerate(accessions):
            txns = await fetch_form4_xml(client, cik, acc_info, ticker, limiter)
            all_txns.extend(txns)
            if (idx + 1) % 100 == 0:
                log.info("[%s] Progress: %d txns from %d/%d filings",
                         ticker, len(all_txns), idx + 1, len(accessions))

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(all_txns, separators=(",", ":")))

    result["transactions"] = len(all_txns)
    if not all_txns:
        return result

    scores = compute_monthly_scores(all_txns)

    coll = db[COLLECTION]
    query: dict[str, Any] = {"ticker": ticker}
    if not force_write:
        query["$or"] = [{"insider_activity": None}, {"insider_source": {"$ne": "edgar_submissions"}}]

    rows = await coll.find(query, {"_id": 1, "timestamp": 1}).to_list(length=None)
    ops = []
    for row in rows:
        ts = row.get("timestamp")
        if ts is None:
            continue
        mk = ts.strftime("%Y-%m") if hasattr(ts, "strftime") else str(ts)[:7]
        sc = scores.get(mk)
        if sc is not None:
            ops.append(UpdateOne({"_id": row["_id"]}, {"$set": {"insider_activity": sc, "insider_source": "edgar_submissions"}}))

    if ops and not dry_run:
        for i in range(0, len(ops), BATCH_SIZE):
            r = await coll.bulk_write(ops[i:i+BATCH_SIZE], ordered=False)
            result["rows_updated"] += r.modified_count
    elif ops:
        result["rows_updated"] = len(ops)

    elapsed = time.perf_counter() - t0
    log.info("[%s] %d filings → %d txns → %d scores → %d rows (%.1fs)",
             ticker, result["filings"], len(all_txns), len(scores), result["rows_updated"], elapsed)
    return result


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill insider_activity via data.sec.gov submissions API")
    parser.add_argument("--ticker", type=str, default=None)
    parser.add_argument("--force-write", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    tickers = [args.ticker.upper()] if args.ticker else EQUITY_TICKERS
    limiter = _RateLimiter(max_per_sec=8.0)

    print(f"\n{'='*60}")
    print("  SEC EDGAR SUBMISSIONS API INSIDER BACKFILL")
    print(f"{'='*60}")
    print("  Endpoint     : data.sec.gov/submissions")
    print(f"  Tickers      : {len(tickers)}")
    print("  Rate limit   : 8 req/sec")
    print(f"  Cache        : {CACHE_DIR}")
    print(f"{'='*60}\n")

    mongo = AsyncIOMotorClient(MONGO_URI)
    db = mongo[DB_NAME]
    totals = {"filings": 0, "transactions": 0, "rows_updated": 0}

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        for i, ticker in enumerate(tickers, 1):
            log.info("--- [%d/%d] %s ---", i, len(tickers))
            r = await process_ticker(ticker, db, client, limiter, args.force_write, args.dry_run)
            for k in totals:
                totals[k] += r[k]

    mongo.close()

    print(f"\n{'='*60}")
    print("  SUBMISSIONS API BACKFILL COMPLETE")
    print(f"{'='*60}")
    for k, v in totals.items():
        print(f"  {k:<20}: {v:,}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
