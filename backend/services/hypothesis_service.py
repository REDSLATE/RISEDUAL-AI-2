import logging

from emergentintegrations.llm.chat import LlmChat, UserMessage
import json

logger = logging.getLogger(__name__)


class HypothesisService:
    """Per-ticker AI hypothesis engine using all scraped macro data"""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.system_message = """You are an elite Wall Street quantitative analyst and AI research engine for RISEDUALAI.
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

    async def generate_hypothesis(self, symbol: str, data: dict) -> dict:
        """Generate AI hypothesis using multi-agent crew."""
        from services.crew_definitions import run_hypothesis_crew
        try:
            result = await run_hypothesis_crew(symbol, data, self.api_key)
            return result
        except Exception as e:
            logger.error(f"Crew hypothesis failed for {symbol}, falling back to single-agent: {e}")
            return await self._single_agent_hypothesis(symbol, data)

    async def _single_agent_hypothesis(self, symbol: str, data: dict) -> dict:
        """Fallback single-agent hypothesis if crew fails."""
        try:
            chat = LlmChat(api_key=self.api_key, session_id=f"hypothesis_{symbol}", system_message=self.system_message).with_model("openai", "gpt-5.2")

            prompt = f"""Generate an investment hypothesis for **{symbol.upper()}**.

AVAILABLE DATA:

FINANCIAL NEWS:
{self._format_news(data.get('news', []))}

WORLD EVENTS:
{self._format_events(data.get('world_events', {}))}

FOREIGN MARKETS:
{self._format_markets(data.get('foreign_markets', {}))}

CONGRESSIONAL TRADES:
{self._format_congress(data.get('gov_filings', {}))}

INSIDER TRADES (SEC):
{self._format_insiders(data.get('gov_filings', {}))}

FED ANNOUNCEMENTS:
{self._format_fed(data.get('gov_filings', {}))}

SOCIAL SENTIMENT:
{self._format_social(data.get('social', []))}

CRYPTO DATA:
{self._format_crypto(data.get('crypto', []))}

Provide your hypothesis for {symbol.upper()} in JSON format."""

            response = await chat.send_message(UserMessage(text=prompt))

            # Response is a string
            text = response.strip() if isinstance(response, str) else response.text.strip()
            if '```json' in text:
                text = text.split('```json')[1].split('```')[0].strip()
            elif '```' in text:
                text = text.split('```')[1].split('```')[0].strip()

            hypothesis = json.loads(text)
            hypothesis['symbol'] = symbol.upper()
            hypothesis['multi_agent'] = False
            return hypothesis

        except json.JSONDecodeError:
            raw = text if 'text' in dir() else "Analysis unavailable"
            return {
                "symbol": symbol.upper(),
                "verdict": "NEUTRAL",
                "confidence": 50,
                "thesis": raw,
                "summary": "AI analysis completed but structured parsing failed",
                "catalysts": [],
                "risks": [],
                "multi_agent": False,
            }
        except Exception as e:
            logger.error(f"Fallback hypothesis error for {symbol}: {str(e)}")
            raise

    def _format_news(self, news: list) -> str:
        if not news:
            return "No news data available"
        lines = []
        for n in news[:8]:
            lines.append(f"- [{n.get('source', 'N/A')}] {n.get('title', 'N/A')}")
        return "\n".join(lines)

    def _format_events(self, events: dict) -> str:
        if not events:
            return "No world events data"
        lines = []
        for e in events.get('high_impact_events', [])[:5]:
            sectors = ', '.join([s['sector'] for s in e.get('affected_sectors', [])[:3]])
            lines.append(f"- {e.get('title', 'N/A')} (Sectors: {sectors})")
        return "\n".join(lines) or "No high-impact events"

    def _format_markets(self, markets: dict) -> str:
        if not markets:
            return "No foreign market data"
        lines = []
        for region in ['asia', 'europe', 'americas']:
            for m in markets.get(region, [])[:3]:
                chg = m.get('change_percent', 0)
                arrow = '+' if chg >= 0 else ''
                lines.append(f"- {m.get('name', 'N/A')}: {arrow}{chg}%")
        for c in markets.get('commodities', [])[:3]:
            lines.append(f"- {c.get('name', 'N/A')}: ${c.get('price', 0)} ({c.get('change_percent', 0)}%)")
        return "\n".join(lines) or "No market data"

    def _format_congress(self, gov: dict) -> str:
        if not gov:
            return "No congressional trade data"
        lines = []
        for t in gov.get('congressional_trades', [])[:8]:
            lines.append(f"- {t.get('description', 'N/A')}")
        return "\n".join(lines) or "No congressional trades"

    def _format_insiders(self, gov: dict) -> str:
        if not gov:
            return "No insider trade data"
        lines = []
        for t in gov.get('insider_trades', [])[:5]:
            lines.append(f"- {t.get('description', 'N/A')}")
        return "\n".join(lines) or "No insider filings"

    def _format_fed(self, gov: dict) -> str:
        if not gov:
            return "No Fed data"
        lines = []
        for a in gov.get('fed_announcements', [])[:3]:
            lines.append(f"- {a.get('title', 'N/A')}")
        return "\n".join(lines) or "No Fed announcements"

    def _format_social(self, social: list) -> str:
        if not social:
            return "No social sentiment data"
        lines = []
        for s in social[:5]:
            lines.append(f"- {s.get('title', 'N/A')} (Score: {s.get('score', 0)})")
        return "\n".join(lines)

    def _format_crypto(self, crypto: list) -> str:
        if not crypto:
            return "No crypto data"
        lines = []
        for c in crypto[:5]:
            lines.append(f"- {c.get('symbol', 'N/A')}: {c.get('sentiment', c.get('price', 'N/A'))}")
        return "\n".join(lines)
