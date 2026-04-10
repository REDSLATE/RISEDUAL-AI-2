"""
CrewAI-style crews for War Room, Hypothesis, and Market Predictions.

Each crew defines specialized agents and their tasks, then uses the
CrewEngine to run them sequentially or in parallel.
"""

import json
import logging
from typing import Dict, Optional
from datetime import datetime, timezone

from services.crew_engine import CrewEngine, AgentConfig

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  WAR ROOM CREW
# ──────────────────────────────────────────────

WAR_ROOM_AGENTS = [
    AgentConfig(
        role="Fundamental Analyst",
        goal="Evaluate company financials, earnings trends, and valuation metrics",
        backstory="CFA with 15 years at Goldman Sachs. Specializes in earnings surprise patterns, P/E expansion, and margin analysis. You catch what Wall Street misses in the footnotes.",
    ),
    AgentConfig(
        role="Technical Analyst",
        goal="Identify chart patterns, momentum signals, and key price levels",
        backstory="Former prop trader who built algorithmic pattern recognition systems. Expert in EMA crossovers, RSI divergences, Fibonacci retracements, and volume profile analysis.",
    ),
    AgentConfig(
        role="Sentiment & Insider Analyst",
        goal="Assess insider trading patterns, institutional sentiment, and smart money flows",
        backstory="SEC filing specialist who tracks Form 4 insider transactions, 13F institutional holdings, and congressional trades. You know when insiders are quietly loading up or dumping.",
    ),
]

WAR_ROOM_SYNTHESIZER = AgentConfig(
    role="Chief Investment Strategist",
    goal="Synthesize all agent findings into a definitive investment verdict with composite score",
    backstory="20-year hedge fund CIO who integrates fundamental, technical, and sentiment signals into actionable trade recommendations. You weigh conflicting signals and identify the highest-conviction plays.",
)


async def run_war_room_crew(symbol: str, overview: Dict, earnings: Dict,
                            insiders: Dict, api_key: str) -> Dict:
    """Run the War Room multi-agent crew for a stock symbol."""
    engine = CrewEngine(api_key)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")

    # Build data context for each agent
    overview_str = json.dumps(overview, indent=1, default=str) if overview else "No company data available"
    earnings_str = json.dumps(earnings, indent=1, default=str) if earnings else "No earnings data"
    insiders_str = json.dumps(insiders, indent=1, default=str) if insiders else "No insider data"

    tasks = [
        # Task for Fundamental Analyst
        f"Analyze {symbol}'s fundamentals and earnings.\n\nCOMPANY DATA:\n{overview_str}\n\nEARNINGS HISTORY:\n{earnings_str}\n\nProvide: valuation assessment, earnings momentum, growth outlook, and a fundamental score 0-100.",

        # Task for Technical Analyst
        f"Analyze {symbol}'s technical setup.\n\nPRICE DATA:\n{overview_str}\n\nIdentify: trend direction, key support/resistance, momentum indicators, chart patterns, and a technical score 0-100.",

        # Task for Sentiment Analyst
        f"Analyze insider and institutional activity for {symbol}.\n\nINSIDER TRADES:\n{insiders_str}\n\nAssess: insider buy/sell ratio, notable transactions, smart money direction, and a sentiment score 0-100.",
    ]

    synth_prompt = f"""Synthesize all three analyses for {symbol} into a final investment verdict.

Output ONLY valid JSON:
{{
  "verdict": "STRONG BUY / BUY / HOLD / SELL / STRONG SELL",
  "composite_score": 0-100,
  "confidence": 0-100,
  "fundamental_score": 0-100,
  "technical_score": 0-100,
  "sentiment_score": 0-100,
  "key_thesis": "2-3 sentence investment thesis",
  "bull_case": "1-2 sentences",
  "bear_case": "1-2 sentences",
  "catalysts": ["catalyst1", "catalyst2", "catalyst3"],
  "risks": ["risk1", "risk2", "risk3"],
  "price_target_short": "1-2 week target",
  "price_target_medium": "1-3 month target",
  "trade_recommendation": "Specific entry/exit/position size advice"
}}"""

    result = await engine.run_parallel_crew(
        WAR_ROOM_AGENTS, tasks, WAR_ROOM_SYNTHESIZER, synth_prompt,
        session_suffix=f"warroom_{symbol}_{ts}"
    )

    # Parse the synthesis JSON
    parsed = _parse_json_output(result["synthesis"])
    parsed["agent_analyses"] = [
        {"role": a["role"], "summary": a["output"][:500]}
        for a in result["agent_outputs"]
    ]
    parsed["multi_agent"] = True
    parsed["agents_used"] = len(WAR_ROOM_AGENTS) + 1

    return parsed


# ──────────────────────────────────────────────
#  INVESTMENT HYPOTHESIS CREW
# ──────────────────────────────────────────────

HYPOTHESIS_AGENTS = [
    AgentConfig(
        role="Macro Economist",
        goal="Assess how world events, foreign markets, and Fed policy affect the ticker",
        backstory="Former Fed economist and IMF advisor. Expert at connecting geopolitical dots to market moves. You see second-order effects that others miss.",
        model_name="gpt-4o-mini",
    ),
    AgentConfig(
        role="Quantitative Researcher",
        goal="Analyze financial data, earnings, and statistical patterns for the ticker",
        backstory="MIT PhD in quantitative finance. Built factor models at Renaissance Technologies. You find alpha in data that humans overlook.",
        model_name="gpt-4o-mini",
    ),
    AgentConfig(
        role="Congressional & Insider Tracker",
        goal="Identify smart money moves from congressional trades, insider filings, and institutional flows",
        backstory="Former SEC investigator turned analyst. You track every Form 4, Schedule 13D, and congressional disclosure for trading signals.",
        model_name="gpt-4o-mini",
    ),
]

HYPOTHESIS_SYNTHESIZER = AgentConfig(
    role="Chief Hypothesis Architect",
    goal="Produce a definitive investment hypothesis by weighing macro, quant, and insider signals",
    backstory="Portfolio manager running a $5B multi-strategy fund. You combine macro, quant, and flow analysis into a single coherent thesis with specific price targets and catalysts.",
)


async def run_hypothesis_crew(symbol: str, data: Dict, api_key: str) -> Dict:
    """Run the Investment Hypothesis multi-agent crew."""
    engine = CrewEngine(api_key)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")

    # Format data sections
    news = _fmt_list(data.get("news", []), "title", "source", limit=8)
    events = json.dumps(data.get("world_events", {}), indent=1, default=str)[:2000]
    markets = json.dumps(data.get("foreign_markets", {}), indent=1, default=str)[:2000]
    gov = json.dumps(data.get("gov_filings", {}), indent=1, default=str)[:2000]
    social = _fmt_list(data.get("social", []), "title", "score", limit=5)
    crypto = _fmt_list(data.get("crypto", []), "symbol", "sentiment", limit=5)

    tasks = [
        # Macro Economist
        f"Analyze macro conditions affecting {symbol}.\n\nWORLD EVENTS:\n{events}\n\nFOREIGN MARKETS:\n{markets}\n\nFED & GOV:\n{gov}\n\nAssess: macro headwinds/tailwinds, sector rotation implications, and geopolitical risk for {symbol}.",

        # Quant Researcher
        f"Quantitative analysis of {symbol}.\n\nFINANCIAL NEWS:\n{news}\n\nSOCIAL SENTIMENT:\n{social}\n\nCRYPTO SIGNALS:\n{crypto}\n\nProvide: statistical edge assessment, sentiment scoring, and data-driven price outlook.",

        # Congressional Tracker
        f"Track smart money for {symbol}.\n\nCONGRESSIONAL TRADES:\n{gov}\n\nProvide: notable insider/congressional activity, institutional positioning signals, and conviction level.",
    ]

    synth_prompt = f"""Produce a definitive investment hypothesis for {symbol}.

Output ONLY valid JSON:
{{
  "verdict": "BUY / SELL / HOLD",
  "confidence": 0-100,
  "price_target_short": "1-2 week target",
  "price_target_medium": "1-3 month target",
  "thesis": "2-3 paragraph investment thesis using findings from all agents",
  "catalysts": ["catalyst1", "catalyst2", "catalyst3", "catalyst4", "catalyst5"],
  "risks": ["risk1", "risk2", "risk3", "risk4", "risk5"],
  "congressional_activity": "Summary of relevant congressional/insider trades",
  "sector_impact": "How macro conditions specifically affect this ticker",
  "technical_outlook": "Data-driven price assessment",
  "summary": "One-line verdict"
}}"""

    result = await engine.run_parallel_crew(
        HYPOTHESIS_AGENTS, tasks, HYPOTHESIS_SYNTHESIZER, synth_prompt,
        session_suffix=f"hypothesis_{symbol}_{ts}"
    )

    parsed = _parse_json_output(result["synthesis"])
    parsed["symbol"] = symbol.upper()
    parsed["multi_agent"] = True
    parsed["agents_used"] = len(HYPOTHESIS_AGENTS) + 1
    parsed["agent_analyses"] = [
        {"role": a["role"], "summary": a["output"][:500]}
        for a in result["agent_outputs"]
    ]

    return parsed


# ──────────────────────────────────────────────
#  MARKET PREDICTION CREW
# ──────────────────────────────────────────────

PREDICTION_AGENTS = [
    AgentConfig(
        role="News & Sentiment Analyst",
        goal="Assess market sentiment from financial news, social media, and crypto signals",
        backstory="Ex-Bloomberg news analyst who built NLP sentiment engines processing 10K articles/day. You detect narrative shifts before they hit mainstream media.",
        model_name="gpt-4o-mini",
    ),
    AgentConfig(
        role="Global Macro Strategist",
        goal="Analyze foreign markets, commodities, and geopolitical events for directional signals",
        backstory="Macro strategist at Bridgewater Associates. Expert in cross-market correlations, carry trades, and geopolitical risk premiums. You see how Tokyo moves New York.",
        model_name="gpt-4o-mini",
    ),
    AgentConfig(
        role="Institutional Flow Analyst",
        goal="Track insider trades, congressional activity, dark pool flows, and institutional positioning",
        backstory="Built the dark pool surveillance system at a major exchange. You see where institutional money is flowing before the market catches on.",
        model_name="gpt-4o-mini",
    ),
]

PREDICTION_SYNTHESIZER = AgentConfig(
    role="Chief Market Strategist",
    goal="Produce a high-conviction market forecast combining sentiment, macro, and flow analysis",
    backstory="CIO of a top-performing global macro hedge fund. You synthesize cross-asset signals into precise directional calls with defined risk parameters. Your track record: 73% accuracy on weekly calls.",
)


async def run_prediction_crew(
    financial_news, crypto_data, insider_trades, social_sentiment,
    real_estate_data=None, world_events=None, foreign_markets=None,
    gov_filings=None, api_key: str = ""
) -> Dict:
    """Run the Market Prediction multi-agent crew."""
    engine = CrewEngine(api_key)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")

    # Format data for each agent
    news_str = _fmt_list(financial_news, "title", "source", limit=8)
    social_str = _fmt_list(social_sentiment, "title", "score", limit=5)
    crypto_str = _fmt_list(crypto_data, "symbol", "sentiment", limit=5)
    trades_str = _fmt_list(insider_trades, "ticker", "trade_type", limit=5)
    events_str = json.dumps(world_events or {}, indent=1, default=str)[:2000]
    markets_str = json.dumps(foreign_markets or {}, indent=1, default=str)[:2000]
    gov_str = json.dumps(gov_filings or {}, indent=1, default=str)[:2000]
    re_str = json.dumps(real_estate_data or {}, indent=1, default=str)[:1500]

    tasks = [
        # Sentiment Analyst
        f"Analyze market sentiment.\n\nFINANCIAL NEWS:\n{news_str}\n\nSOCIAL MEDIA:\n{social_str}\n\nCRYPTO SIGNALS:\n{crypto_str}\n\nProvide: overall sentiment score (-100 to +100), narrative direction, and key sentiment drivers.",

        # Macro Strategist
        f"Analyze global macro conditions.\n\nWORLD EVENTS:\n{events_str}\n\nFOREIGN MARKETS:\n{markets_str}\n\nREAL ESTATE:\n{re_str}\n\nProvide: macro risk assessment, cross-market correlations, sector rotation signals, and directional bias.",

        # Flow Analyst
        f"Analyze institutional flows.\n\nINSIDER TRADES:\n{trades_str}\n\nCONGRESSIONAL & GOV:\n{gov_str}\n\nProvide: smart money direction, notable positioning changes, dark pool signal, and conviction level.",
    ]

    synth_prompt = """Produce a definitive market forecast by synthesizing sentiment, macro, and flow analyses.

Output ONLY valid JSON:
{
  "overall_direction": "BULLISH / BEARISH / NEUTRAL",
  "confidence_score": 0-100,
  "timeframes": {
    "intraday": {"direction": "...", "target": "..."},
    "short_term": {"direction": "...", "target": "..."},
    "medium_term": {"direction": "...", "target": "..."}
  },
  "key_signals": ["signal1", "signal2", "signal3", "signal4", "signal5"],
  "risk_factors": ["risk1", "risk2", "risk3"],
  "crypto_outlook": "...",
  "stock_outlook": "...",
  "geopolitical_impact": "...",
  "institutional_flow": "Summary of smart money positioning",
  "agent_consensus": "Did all agents agree or were there conflicts?",
  "summary": "3-sentence executive summary"
}"""

    result = await engine.run_parallel_crew(
        PREDICTION_AGENTS, tasks, PREDICTION_SYNTHESIZER, synth_prompt,
        session_suffix=f"prediction_{ts}"
    )

    parsed = _parse_json_output(result["synthesis"])
    parsed["timestamp"] = datetime.now(timezone.utc).isoformat()
    parsed["multi_agent"] = True
    parsed["agents_used"] = len(PREDICTION_AGENTS) + 1
    parsed["data_sources"] = {
        "news_articles": len(financial_news),
        "crypto_signals": len(crypto_data),
        "insider_trades": len(insider_trades),
        "social_posts": len(social_sentiment),
        "world_events": (world_events or {}).get("total_events", 0),
        "foreign_markets": len((foreign_markets or {}).get("asia", []) + (foreign_markets or {}).get("europe", []) + (foreign_markets or {}).get("americas", [])),
        "congressional_trades": (gov_filings or {}).get("congressional_count", 0),
        "fed_announcements": (gov_filings or {}).get("fed_count", 0),
    }
    parsed["agent_analyses"] = [
        {"role": a["role"], "summary": a["output"][:500]}
        for a in result["agent_outputs"]
    ]

    return parsed


# ──────────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────────

def _parse_json_output(text: str) -> Dict:
    """Extract JSON from LLM output, handling markdown fences."""
    clean = text.strip()
    if "```json" in clean:
        clean = clean.split("```json")[1].split("```")[0].strip()
    elif "```" in clean:
        clean = clean.split("```")[1].split("```")[0].strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        logger.warning("Failed to parse crew synthesis JSON, returning raw text")
        return {"raw_analysis": text, "parse_error": True}


def _fmt_list(items: list, key1: str, key2: str, limit: int = 5) -> str:
    """Format a list of dicts into readable lines."""
    if not items:
        return "No data available"
    lines = []
    for item in items[:limit]:
        v1 = item.get(key1, "N/A")
        v2 = item.get(key2, "")
        lines.append(f"- {v1}" + (f" ({v2})" if v2 else ""))
    return "\n".join(lines)


# ──────────────────────────────────────────────
#  SECTOR SENTIMENT CREW
# ──────────────────────────────────────────────

SECTOR_SENTIMENT_AGENTS = [
    AgentConfig(
        role="Technical Sector Analyst",
        goal="Score each S&P 500 sector's technical momentum and trend strength",
        backstory="Quantitative strategist who built sector rotation models at AQR. You analyze price momentum, mean reversion, and relative strength across all 11 GICS sectors to identify rotation opportunities.",
        model_name="gpt-4o-mini",
    ),
    AgentConfig(
        role="Macro Sector Strategist",
        goal="Assess macro tailwinds and headwinds for each sector based on economic conditions",
        backstory="Chief strategist at a $50B pension fund. You evaluate how interest rates, inflation, GDP growth, and fiscal policy differentially impact each sector. Your sector allocation calls consistently outperform.",
        model_name="gpt-4o-mini",
    ),
    AgentConfig(
        role="Flow & Sentiment Analyst",
        goal="Track institutional money flows, ETF fund flows, and market sentiment per sector",
        backstory="Former ETF market maker who monitors sector ETF creation/redemption flows daily. You see where institutional money is rotating before the price moves.",
        model_name="gpt-4o-mini",
    ),
]

SECTOR_SENTIMENT_SYNTHESIZER = AgentConfig(
    role="Chief Sector Strategist",
    goal="Produce a definitive sentiment score (-1.0 to 1.0) for each sector by synthesizing technical, macro, and flow analyses",
    backstory="CIO running a sector rotation hedge fund with a 15-year track record. You synthesize all signals into precise sector scores that drive $2B in allocation decisions.",
)


def calculate_heatmap_sentiment(agent_results):
    """Aggregates Multi-Agent scores for the sector heatmap.
    agent_results: List of floats from [-1.0, 1.0]
    Returns: 0 to 100 for the UI."""
    avg_score = sum(agent_results) / len(agent_results)
    heatmap_value = (avg_score + 1) * 50
    return round(heatmap_value, 2)


async def run_sector_sentiment_crew(sectors_data: list, api_key: str) -> Dict:
    """Run one multi-agent crew to score ALL sectors at once (4 LLM calls total)."""
    engine = CrewEngine(api_key)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")

    # Build a compact sector summary for the agents
    sector_lines = []
    for s in sectors_data:
        sector_lines.append(
            f"{s['symbol']} ({s['name']}): "
            f"Price=${s.get('price', 0):.2f}, "
            f"1D={s.get('change_1d', 0):+.2f}%, "
            f"1W={s.get('change_1w', 0):+.2f}%, "
            f"1M={s.get('change_1m', 0):+.2f}%, "
            f"3M={s.get('change_3m', 0):+.2f}%, "
            f"YTD={s.get('change_ytd', 0):+.2f}%, "
            f"Weight={s.get('weight', 0)}%"
        )
    sector_summary = "\n".join(sector_lines)
    symbols_list = ", ".join(s["symbol"] for s in sectors_data)

    tasks = [
        # Technical Analyst
        f"Analyze the technical momentum and trend of ALL 11 S&P 500 sector ETFs.\n\nSECTOR DATA:\n{sector_summary}\n\nFor EACH sector, assess: trend strength, momentum, relative performance vs S&P, mean reversion risk. Score each from -1.0 (extremely bearish) to +1.0 (extremely bullish).",

        # Macro Strategist
        f"Analyze macro conditions affecting ALL 11 S&P 500 sectors.\n\nSECTOR DATA:\n{sector_summary}\n\nConsider: current interest rate environment, inflation trends, economic cycle stage, fiscal policy. Score each sector from -1.0 (strong macro headwinds) to +1.0 (strong macro tailwinds).",

        # Flow Analyst
        f"Analyze institutional flows and sentiment for ALL 11 S&P 500 sectors.\n\nSECTOR DATA:\n{sector_summary}\n\nAssess: sector rotation direction, ETF flow trends, smart money positioning. Score each sector from -1.0 (heavy outflows/bearish) to +1.0 (heavy inflows/bullish).",
    ]

    synth_prompt = f"""Synthesize all three analyses and produce a final AI sentiment score for EACH of the 11 S&P 500 sector ETFs.

IMPORTANT: You must score ALL of these sectors: {symbols_list}

For each sector, average the three agent scores (technical, macro, flow) and provide a final consensus score.

Output ONLY valid JSON:
{{
  "sectors": {{
    "XLK": {{"score": 0.65, "label": "Bullish", "reasoning": "Strong momentum + AI spending tailwind"}},
    "XLF": {{"score": -0.2, "label": "Cautious", "reasoning": "Rate uncertainty weighing on banks"}},
    ...all 11 sectors...
  }},
  "rotation_call": "Brief 1-2 sentence sector rotation recommendation",
  "risk_regime": "risk-on / risk-off / mixed"
}}

Score range: -1.0 (extremely bearish) to +1.0 (extremely bullish).
Labels: Strong Sell (<-0.6), Bearish (-0.6 to -0.2), Cautious (-0.2 to 0.1), Neutral (0.1 to 0.3), Bullish (0.3 to 0.6), Strong Buy (>0.6)."""

    result = await engine.run_parallel_crew(
        SECTOR_SENTIMENT_AGENTS, tasks, SECTOR_SENTIMENT_SYNTHESIZER, synth_prompt,
        session_suffix=f"sector_sentiment_{ts}"
    )

    parsed = _parse_json_output(result["synthesis"])

    # Convert raw scores to heatmap values (0-100) using user's function
    sentiment_output = {}
    raw_sectors = parsed.get("sectors", {})
    for sym, data in raw_sectors.items():
        raw_score = float(data.get("score", 0))
        # Clamp to [-1, 1]
        raw_score = max(-1.0, min(1.0, raw_score))
        sentiment_output[sym] = {
            "score": raw_score,
            "heatmap_value": calculate_heatmap_sentiment([raw_score]),
            "label": data.get("label", "Neutral"),
            "reasoning": data.get("reasoning", ""),
        }

    return {
        "sectors": sentiment_output,
        "rotation_call": parsed.get("rotation_call", ""),
        "risk_regime": parsed.get("risk_regime", "mixed"),
        "multi_agent": True,
        "agents_used": len(SECTOR_SENTIMENT_AGENTS) + 1,
        "agent_analyses": [
            {"role": a["role"], "summary": a["output"][:500]}
            for a in result["agent_outputs"]
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
