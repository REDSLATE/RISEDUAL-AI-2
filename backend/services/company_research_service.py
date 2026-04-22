import asyncio
import logging
import json
from typing import Optional
from services.market_data_service import MarketDataService
from services.financial_scraping_service import FinancialScrapingService
from services.ai_service import AIService
from services.price_provider import get_overview_sync

logger = logging.getLogger(__name__)

class CompanyResearchService:
    def __init__(self) -> None:
        self.market_data = MarketDataService()
        self.scraper = FinancialScrapingService()
        self.ai = AIService()

    async def get_company_overview(self, symbol: str) -> Optional[dict]:
        """Fetch company fundamentals via smart price provider (AV -> yfinance)."""
        try:
            data = await asyncio.to_thread(get_overview_sync, symbol)
            if not data or 'Symbol' not in data:
                return None

            return {
                'symbol': data.get('Symbol', symbol),
                'name': data.get('Name', ''),
                'description': data.get('Description', ''),
                'exchange': data.get('Exchange', ''),
                'sector': data.get('Sector', ''),
                'industry': data.get('Industry', ''),
                'market_cap': data.get('MarketCapitalization', ''),
                'pe_ratio': data.get('PERatio', ''),
                'forward_pe': data.get('ForwardPE', ''),
                'eps': data.get('EPS', ''),
                'dividend_yield': data.get('DividendYield', ''),
                'beta': data.get('Beta', ''),
                'week_52_high': data.get('52WeekHigh', ''),
                'week_52_low': data.get('52WeekLow', ''),
                'revenue_ttm': data.get('RevenueTTM', ''),
                'profit_margin': data.get('ProfitMargin', ''),
                'operating_margin': data.get('OperatingMarginTTM', ''),
                'return_on_equity': data.get('ReturnOnEquityTTM', ''),
                'price_to_book': data.get('PriceToBookRatio', ''),
                'price_to_sales': data.get('PriceToSalesRatioTTM', ''),
                'analyst_target': data.get('AnalystTargetPrice', ''),
                'analyst_rating': data.get('AnalystRatingStrongBuy', ''),
                'headquarters': data.get('Address', ''),
                'fiscal_year_end': data.get('FiscalYearEnd', ''),
                'ceo': data.get('CEO', data.get('OfficerTitle', '')),
                'employees': data.get('FullTimeEmployees', ''),
            }
        except Exception as e:
            logger.error(f"Error fetching company overview for {symbol}: {str(e)}")
            return None

    async def research_company(self, symbol: str, session_id: str) -> dict:
        """Full Perplexity-style company research with AI synthesis."""
        overview = await self.get_company_overview(symbol)
        quote = await self.market_data.get_quote(symbol)
        news_data = await self.scraper.scrape_financial_news()

        company_name = overview.get('name', symbol) if overview else symbol
        relevant_news = self._filter_relevant_news(news_data, symbol, company_name)
        data_context, sources = self._build_research_context(symbol, company_name, overview, quote, relevant_news)

        prompt = self._build_synthesis_prompt(symbol, company_name, sources, data_context)
        ai_response = await self.ai.chat(prompt, f"research_{session_id}_{symbol}")
        synthesis_text = ai_response["text"] if isinstance(ai_response, dict) else ai_response
        provider_meta = ai_response.get("provider") if isinstance(ai_response, dict) else None

        return {
            'symbol': symbol.upper(),
            'company_name': company_name,
            'overview': overview,
            'quote': quote,
            'synthesis': synthesis_text,
            'sources': sources,
            'news_count': len(relevant_news),
            'provider': provider_meta,
        }

    def _filter_relevant_news(self, news_data, symbol: str, company_name: str) -> list:
        """Filter news articles relevant to the target company."""
        all_articles = news_data if isinstance(news_data, list) else []
        relevant = [a for a in all_articles
                    if symbol.lower() in a.get('title', '').lower()
                    or company_name.lower() in a.get('title', '').lower()]
        return relevant if relevant else all_articles[:5]

    def _build_research_context(self, symbol: str, company_name: str, overview, quote, news: list) -> tuple:
        """Build data context string and sources list for AI synthesis."""
        ctx = f"""
COMPANY RESEARCH REQUEST: {symbol}

=== COMPANY FUNDAMENTALS (Alpha Vantage) ===
{json.dumps(overview, indent=2) if overview else 'No fundamental data available.'}

=== REAL-TIME QUOTE ===
{json.dumps(quote, indent=2) if quote else 'No quote data available.'}

=== RECENT NEWS ({len(news)} articles) ===
"""
        sources = []
        for i, article in enumerate(news[:8]):
            src_num = i + 1
            ctx += f"[{src_num}] {article.get('title', 'Untitled')} — {article.get('source', 'Unknown')}\n"
            sources.append({
                'number': src_num,
                'title': article.get('title', 'Untitled'),
                'source': article.get('source', 'Unknown'),
                'url': article.get('url', ''),
            })
        return ctx, sources

    def _build_synthesis_prompt(self, symbol: str, company_name: str, sources: list, data_context: str) -> str:
        """Build the AI synthesis prompt."""
        return f"""You are a Perplexity-style financial research assistant. Given the data below, write a comprehensive but concise research report about {symbol} ({company_name}).

FORMAT YOUR RESPONSE EXACTLY LIKE THIS (use markdown):

## {company_name} ({symbol})

**Company Overview**: [2-3 sentence summary of what the company does, sector, headquarters]

**Key Executives**: CEO and leadership info if available.

**Market Data**:
- Market Cap: [value]
- P/E Ratio: [value] | Forward P/E: [value]
- EPS: [value]
- Revenue (TTM): [value]
- Dividend Yield: [value]
- 52-Week Range: [low] - [high]
- Beta: [value]
- Analyst Target: [value]

**Financial Health**: [2-3 sentences on margins, ROE, valuation metrics]

**Recent Developments**: [Summarize relevant news, cite sources as [1], [2], etc.]

**Outlook**: [2-3 sentence forward-looking analysis based on the data]

Sources are numbered [1] through [{len(sources)}]. CITE them inline like Perplexity does.

{data_context}"""
