"""Public Demo Data endpoint — serves read-only data for unauthenticated visitors.

GET /api/demo/dashboard — aggregated demo data for the public landing page demo
"""
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/demo", tags=["demo"])

db = None

def set_db(database):
    global db
    db = database


@router.get("/dashboard")
async def demo_dashboard():
    """Public demo dashboard data — no auth required.
    Provides a snapshot of ML signals, predictions, FRED indicators, and platform stats."""

    # ML gate status
    gate = {}
    try:
        from services.ml_orchestrator_service import get_gate_status
        gate = await get_gate_status(db)
    except Exception:
        gate = {"highest_tier": "tier2_paper", "tiers": {}}

    # Recent predictions from DB
    predictions = []
    try:
        cursor = db.predictions.find(
            {}, {"_id": 0, "symbol": 1, "direction": 1, "confidence": 1, "timestamp": 1}
        ).sort("timestamp", -1).limit(8)
        async for doc in cursor:
            predictions.append({
                "symbol": doc.get("symbol"),
                "direction": doc.get("direction"),
                "confidence": round(doc.get("confidence", 0), 3),
            })
    except Exception:
        pass

    # FRED indicators
    fred_data = []
    try:
        from services.fred_service import get_macro_indicators
        fred = await get_macro_indicators()
        for ind in fred.get("indicators", [])[:6]:
            fred_data.append({
                "id": ind["id"],
                "name": ind["name"],
                "value": ind["value"],
                "unit": ind["unit"],
                "change_pct": ind.get("change_pct"),
                "category": ind["category"],
            })
    except Exception:
        pass

    # Paper trade stats
    paper_stats = {"total_trades": 0, "win_rate": 0, "total_pnl": 0}
    try:
        total = await db.paper_trades.count_documents({})
        wins = await db.paper_trades.count_documents({"pnl": {"$gt": 0}})
        pipeline = [{"$group": {"_id": None, "total_pnl": {"$sum": "$pnl"}}}]
        async for doc in db.paper_trades.aggregate(pipeline):
            paper_stats["total_pnl"] = round(doc.get("total_pnl", 0), 2)
        paper_stats["total_trades"] = total
        paper_stats["win_rate"] = round((wins / total) * 100, 1) if total > 0 else 0
    except Exception:
        pass

    # Platform stats
    snapshot_count = 0
    try:
        snapshot_count = await db.features_snapshots.count_documents({})
    except Exception:
        pass

    return {
        "ml": {
            "highest_tier": gate.get("highest_tier", "tier2_paper"),
            "model_version": "v4",
            "accuracy": 62.1,
            "sharpe": 1.56,
            "max_drawdown": 11.2,
            "features": 17,
        },
        "predictions": predictions,
        "fred": fred_data,
        "paper_stats": paper_stats,
        "platform": {
            "training_samples": snapshot_count,
            "tickers_covered": 80,
            "data_years": 15,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


DEMO_SYSTEM_PROMPT = """You are RISEDUAL AI's trading assistant demo. You help visitors understand the platform and answer questions about markets, trading concepts, and the RISEDUAL platform.

Key facts about RISEDUAL AI:
- ML Signal Engine: XGBoost v4, 62.1% accuracy, Sharpe 1.56, 11.2% max drawdown
- 276K+ training samples across 80 tickers, 15 years of data
- Dual AI system: Strategist generates signals, Auditor kills bad ones
- FRED/ALFRED macro economic data integration
- SEC EDGAR fundamentals via StockFit
- Autonomous paper trading (Tier 2 active)
- $55/month Pro plan, no contracts

Rules:
- Be helpful, concise, and knowledgeable about markets
- Mention RISEDUAL features naturally when relevant
- If asked about specific trades, remind them this is a demo and not financial advice
- Keep responses under 200 words
- Do not provide specific buy/sell recommendations
- Encourage joining the waitlist for full access"""

DEMO_RATE_LIMIT = {}
DEMO_RATE_MAX = 20  # max messages per IP per hour


@router.post("/chat")
async def demo_chat(request: Request):
    """Free demo chat powered by NVIDIA Nemotron Nano 9B v2 via OpenRouter.
    Rate-limited to prevent abuse. No auth required."""
    import os
    import httpx

    # Rate limiting by IP
    client_ip = request.client.host if request.client else "unknown"
    now = datetime.now(timezone.utc)
    hour_key = now.strftime("%Y%m%d%H")
    rate_key = f"{client_ip}:{hour_key}"

    count = DEMO_RATE_LIMIT.get(rate_key, 0)
    if count >= DEMO_RATE_MAX:
        return {"error": "Demo rate limit reached. Join the waitlist for unlimited AI access!", "limit": True}
    DEMO_RATE_LIMIT[rate_key] = count + 1

    # Clean old rate limit entries
    stale = [k for k in DEMO_RATE_LIMIT if not k.endswith(hour_key)]
    for k in stale:
        del DEMO_RATE_LIMIT[k]

    # Parse request
    try:
        body = await request.json()
        messages = body.get("messages", [])
        if not messages:
            return {"error": "No messages provided"}
    except Exception:
        return {"error": "Invalid request"}

    # Limit conversation length for demo
    messages = messages[-6:]  # Keep last 6 messages

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return {"error": "Demo chat not configured"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://risedual.ai",
                    "X-Title": "RISEDUAL AI Demo",
                },
                json={
                    "model": "nvidia/nemotron-nano-9b-v2:free",
                    "messages": [
                        {"role": "system", "content": DEMO_SYSTEM_PROMPT},
                        *[{"role": m.get("role", "user"), "content": m.get("content", "")} for m in messages],
                    ],
                    "max_tokens": 500,
                    "temperature": 0.7,
                },
            )

            if resp.status_code != 200:
                logger.warning(f"OpenRouter demo chat failed: {resp.status_code}")
                return {"error": "Demo AI temporarily unavailable. Try again shortly."}

            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

            return {
                "response": content,
                "model": "NVIDIA Nemotron Nano 9B",
                "remaining": DEMO_RATE_MAX - DEMO_RATE_LIMIT.get(rate_key, 0),
            }

    except Exception as e:
        logger.warning(f"Demo chat error: {e}")
        return {"error": "Demo AI temporarily unavailable. Try again shortly."}
