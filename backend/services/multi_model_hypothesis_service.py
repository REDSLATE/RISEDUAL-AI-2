"""Multi-model hypothesis service — runs the 4 RISEDUAL brain
personas individually or as a 4-way consensus.

Personas are defined in ``services.brain_persona_service``. Each brain
has its own underlying LLM provider for genuine consensus diversity
(Alpha→GPT-5.2, Camaro→Claude Sonnet 4.5, Chevelle→Gemini 2.5 Flash,
RedEye→GPT-5.2 with contrarian prompt).

Doctrine V3 / 2026-05-14: when a brain produces a hypothesis we ALSO
fold in any recent matching opinion that brain has posted on Mission
Control, so the brain stays consistent with its operator-side voice.
"""
import json
import asyncio
import logging
from typing import Any


from emergentintegrations.llm.chat import LlmChat, UserMessage

from services.brain_persona_service import BRAINS, brain_runtime

logger = logging.getLogger(__name__)

# Backwards-compat alias — older code paths read ``MODELS``. Now points
# at the brain registry, which carries the same shape (label/weight/
# provider/model).
MODELS: dict[str, dict[str, Any]] = BRAINS

SYSTEM_MESSAGE = """You are an elite Wall Street quantitative analyst and AI research engine.
When given a ticker symbol (stock or crypto), produce a comprehensive investment hypothesis using ALL provided data.

Your hypothesis MUST include:
1. VERDICT: BUY / SELL / HOLD
2. CONFIDENCE: 0-100%
3. PRICE TARGET: Near-term (1-2 weeks) and medium-term (1-3 months) targets
4. THESIS: 2-3 paragraph investment thesis explaining the reasoning
5. KEY CATALYSTS: Top 3-5 positive catalysts
6. KEY RISKS: Top 3-5 risk factors
7. CONGRESSIONAL ACTIVITY: Any relevant congressional trades in this ticker or sector
8. SECTOR IMPACT: How world events and macro conditions affect this specific ticker
9. TECHNICAL OUTLOOK: Brief technical assessment based on available data

Be specific, cite actual data points from the provided information, and always acknowledge uncertainty.
Output in JSON format:
{
  "verdict": "BUY/SELL/HOLD",
  "confidence": 0-100,
  "price_target_short": "...",
  "price_target_medium": "...",
  "thesis": "...",
  "catalysts": ["...", "..."],
  "risks": ["...", "..."],
  "congressional_activity": "...",
  "sector_impact": "...",
  "technical_outlook": "...",
  "summary": "One-line summary"
}"""


def _build_prompt(symbol: str, data: dict) -> str:
    """Build the analysis prompt with all macro data."""
    sections = []

    news = data.get("news", [])
    if news:
        items = [f"- [{n.get('source','N/A')}] {n.get('title','N/A')}" for n in news[:8]]
        sections.append("FINANCIAL NEWS:\n" + "\n".join(items))
    else:
        sections.append("FINANCIAL NEWS:\nNo news data available")

    events = data.get("world_events", {})
    if events:
        items = []
        for e in events.get("high_impact_events", [])[:5]:
            sectors = ", ".join([s["sector"] for s in e.get("affected_sectors", [])[:3]])
            items.append(f"- {e.get('title','N/A')} (Sectors: {sectors})")
        sections.append("WORLD EVENTS:\n" + ("\n".join(items) or "No high-impact events"))
    else:
        sections.append("WORLD EVENTS:\nNo world events data")

    markets = data.get("foreign_markets", {})
    if markets:
        items = []
        for region in ["asia", "europe", "americas"]:
            for m in markets.get(region, [])[:3]:
                chg = m.get("change_percent", 0)
                arrow = "+" if chg >= 0 else ""
                items.append(f"- {m.get('name','N/A')}: {arrow}{chg}%")
        for c in markets.get("commodities", [])[:3]:
            items.append(f"- {c.get('name','N/A')}: ${c.get('price',0)} ({c.get('change_percent',0)}%)")
        sections.append("FOREIGN MARKETS:\n" + ("\n".join(items) or "No market data"))
    else:
        sections.append("FOREIGN MARKETS:\nNo foreign market data")

    gov = data.get("gov_filings", {})
    if gov:
        congress = [f"- {t.get('description','N/A')}" for t in gov.get("congressional_trades", [])[:8]]
        sections.append("CONGRESSIONAL TRADES:\n" + ("\n".join(congress) or "No congressional trades"))

        # Insider trades (from Finnhub SEC Form 4 filings)
        insider_list = gov.get("insider_trades", [])
        if not insider_list:
            insider_list = [{"description": f"{t.get('name','?')} {t.get('action','?')} {t.get('shares',0)} shares @ ${t.get('price','?')}"} for t in gov.get("insider_transactions", [])[:8]]
        insiders = [f"- {t.get('description','N/A')}" for t in insider_list[:8]]
        sections.append("INSIDER TRADES (SEC Form 4):\n" + ("\n".join(insiders) or "No insider filings"))

        fed = [f"- {a.get('title','N/A')}" for a in gov.get("fed_announcements", [])[:3]]
        sections.append("FED ANNOUNCEMENTS:\n" + ("\n".join(fed) or "No Fed announcements"))

        # Upcoming earnings from Finnhub
        earnings = gov.get("upcoming_earnings", [])
        if earnings:
            items = [f"- {e.get('symbol','?')} on {e.get('date','?')} (EPS est: {e.get('eps_estimate','N/A')})" for e in earnings[:10]]
            sections.append("UPCOMING EARNINGS (next 14 days):\n" + "\n".join(items))

        # Company news from Finnhub
        finnhub_news = gov.get("company_news_finnhub", [])
        if finnhub_news:
            items = [f"- [{a.get('source','?')}] {a.get('headline','N/A')}" for a in finnhub_news[:5]]
            sections.append("COMPANY-SPECIFIC NEWS (Finnhub):\n" + "\n".join(items))
    else:
        sections.append("CONGRESSIONAL TRADES:\nNo data")

    social = data.get("social", [])
    if social:
        items = [f"- {s.get('title','N/A')} (Score: {s.get('score',0)})" for s in social[:5]]
        sections.append("SOCIAL SENTIMENT:\n" + "\n".join(items))

    crypto = data.get("crypto", [])
    if crypto:
        items = [f"- {c.get('symbol','N/A')}: {c.get('sentiment', c.get('price','N/A'))}" for c in crypto[:5]]
        sections.append("CRYPTO DATA:\n" + "\n".join(items))

    body = "\n\n".join(sections)
    return f"Generate an investment hypothesis for **{symbol.upper()}**.\n\nAVAILABLE DATA:\n\n{body}\n\nProvide your hypothesis for {symbol.upper()} in JSON format."


def _parse_json_response(text: str) -> dict:
    """Extract JSON from LLM response text."""
    raw = text.strip() if isinstance(text, str) else text.text.strip()
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()
    return json.loads(raw)


async def _fetch_recent_mc_opinion(model_key: str, symbol: str) -> str:
    """Best-effort lookup of this brain's recent MC opinions on ``symbol``.

    Returns a short text snippet to inject into the system prompt so the
    brain stays consistent with what it's been saying publicly. Always
    returns a string (empty if MC unreachable / brain has no take).
    Never raises.
    """
    runtime = brain_runtime(model_key)
    if not runtime:
        return ""
    try:
        from services import risedual_monorepo_client as mc
        resp = await mc.read_opinions(runtime=runtime, symbol=symbol, limit=3)
    except Exception as e:  # noqa: BLE001
        logger.debug("[brain_persona] MC lookup failed for %s: %s", runtime, e)
        return ""
    if not isinstance(resp, dict) or resp.get("error"):
        return ""
    items = resp.get("items") or []
    if not items:
        return ""
    bullets: list[str] = []
    for op in items[:3]:
        stance = (op.get("stance") or "").strip()
        body = (op.get("body") or "").strip()
        if body:
            bullets.append(f"- ({stance}) {body[:240]}")
    if not bullets:
        return ""
    return (
        "\n\nYour recent operator-side opinions on this symbol (for "
        "internal consistency — do not quote them verbatim):\n"
        + "\n".join(bullets)
    )


async def _run_single_model(api_key: str, model_key: str, symbol: str, prompt: str) -> dict:
    """Run hypothesis generation on a single brain persona.

    Uses the brain's ``system_prompt`` so each persona has a genuinely
    different voice. Folds in the brain's recent MC opinions on the
    symbol when available.
    """
    cfg = MODELS[model_key]
    raw_text = ""
    try:
        system = cfg.get("system_prompt") or SYSTEM_MESSAGE
        mc_note = await _fetch_recent_mc_opinion(model_key, symbol)
        # The shared SYSTEM_MESSAGE (output schema) is appended so every
        # brain still returns the same structured JSON the UI expects.
        full_system = f"{system}\n\n{SYSTEM_MESSAGE}{mc_note}"

        chat = LlmChat(
            api_key=api_key,
            session_id=f"hypothesis_{model_key}_{symbol}",
            system_message=full_system,
        ).with_model(cfg["provider"], cfg["model"])

        response = await chat.send_message(UserMessage(text=prompt))
        raw_text = response if isinstance(response, str) else response.text
        hypothesis = _parse_json_response(raw_text)
        hypothesis["model"] = cfg["label"]
        hypothesis["model_key"] = model_key
        hypothesis["symbol"] = symbol.upper()
        return hypothesis

    except json.JSONDecodeError:
        return {
            "model": cfg["label"],
            "model_key": model_key,
            "symbol": symbol.upper(),
            "verdict": "NEUTRAL",
            "confidence": 50,
            "thesis": raw_text[:500] or "Analysis completed but structured parsing failed",
            "summary": f"{cfg['label']} analysis completed — parsing failed",
            "catalysts": [],
            "risks": [],
        }
    except Exception as e:
        logger.error(f"{cfg['label']} hypothesis error for {symbol}: {e}")
        return {
            "model": cfg["label"],
            "model_key": model_key,
            "symbol": symbol.upper(),
            "verdict": "ERROR",
            "confidence": 0,
            "thesis": f"Model encountered an error: {str(e)[:100]}",
            "summary": f"{cfg['label']} failed",
            "catalysts": [],
            "risks": [],
            "error": True,
        }


def _weighted_consensus(results: list[dict]) -> dict:
    """Compute weighted voting consensus from multiple model results."""
    valid = [r for r in results if r.get("verdict") not in ("ERROR", None)]
    if not valid:
        return {
            "verdict": "NEUTRAL",
            "confidence": 0,
            "summary": "All models failed — unable to generate consensus",
        }

    verdict_map = {"BUY": 0, "SELL": 0, "HOLD": 0, "NEUTRAL": 0}
    total_weight = 0
    weighted_confidence = 0

    for r in valid:
        key = r["model_key"]
        w = MODELS.get(key, {}).get("weight", 0.33)
        verdict = r.get("verdict", "NEUTRAL").upper()
        if verdict not in verdict_map:
            verdict = "NEUTRAL"
        verdict_map[verdict] += w
        weighted_confidence += r.get("confidence", 50) * w
        total_weight += w

    final_verdict = max(verdict_map, key=verdict_map.get)
    final_confidence = round(weighted_confidence / total_weight) if total_weight else 50

    # Merge catalysts and risks (deduplicate by similarity)
    all_catalysts = []
    all_risks = []
    for r in valid:
        all_catalysts.extend(r.get("catalysts", [])[:3])
        all_risks.extend(r.get("risks", [])[:3])

    # Simple dedup: keep unique first 6
    seen_cat = set()
    unique_catalysts = []
    for c in all_catalysts:
        short = c[:50].lower()
        if short not in seen_cat:
            seen_cat.add(short)
            unique_catalysts.append(c)
        if len(unique_catalysts) >= 6:
            break

    seen_risk = set()
    unique_risks = []
    for r in all_risks:
        short = r[:50].lower()
        if short not in seen_risk:
            seen_risk.add(short)
            unique_risks.append(r)
        if len(unique_risks) >= 6:
            break

    # Use the highest-weighted valid model's thesis and targets
    best = max(valid, key=lambda x: MODELS.get(x["model_key"], {}).get("weight", 0))

    # Agreement percentage
    agree_count = sum(1 for r in valid if r.get("verdict", "").upper() == final_verdict)
    agreement = round(agree_count / len(valid) * 100)

    return {
        "verdict": final_verdict,
        "confidence": final_confidence,
        "agreement": agreement,
        "price_target_short": best.get("price_target_short", "N/A"),
        "price_target_medium": best.get("price_target_medium", "N/A"),
        "thesis": best.get("thesis", ""),
        "catalysts": unique_catalysts,
        "risks": unique_risks,
        "congressional_activity": best.get("congressional_activity", ""),
        "sector_impact": best.get("sector_impact", ""),
        "technical_outlook": best.get("technical_outlook", ""),
        "summary": f"Consensus: {final_verdict} ({final_confidence}% confidence, {agreement}% model agreement)",
    }


async def generate_hypothesis(api_key: str, symbol: str, data: dict, model: str = "alpha") -> dict:
    """Generate hypothesis using a single brain persona, or 4-brain
    consensus when ``model='consensus'``.

    For unknown / legacy model keys we coerce to ``alpha`` (the free
    tier default).
    """
    if model == "consensus":
        prompt = _build_prompt(symbol, data)
        tasks = [_run_single_model(api_key, mk, symbol, prompt) for mk in MODELS]
        results = await asyncio.gather(*tasks)

        consensus = _weighted_consensus(results)
        consensus["symbol"] = symbol.upper()
        consensus["model"] = "Consensus"
        consensus["model_key"] = "consensus"
        consensus["individual_results"] = [
            {
                "model": r["model"],
                "model_key": r["model_key"],
                "verdict": r.get("verdict", "ERROR"),
                "confidence": r.get("confidence", 0),
                "summary": r.get("summary", ""),
                "error": r.get("error", False),
            }
            for r in results
        ]
        return consensus

    # Single brain mode — call the brain's persona LLM directly. The
    # multi-agent CrewAI path was tuned for generic LLMs and would
    # erase the brain's personality, so we skip it for branded brains.
    if model not in MODELS:
        model = "alpha"
    prompt = _build_prompt(symbol, data)
    out = await _run_single_model(api_key, model, symbol, prompt)
    out["is_pro"] = True
    return out
