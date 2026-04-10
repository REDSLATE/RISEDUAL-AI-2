import logging
from typing import Dict, List, Optional
from emergentintegrations.llm.chat import LlmChat, UserMessage
import json
from datetime import datetime

logger = logging.getLogger(__name__)

class MarketPredictionService:
    """AI-powered market prediction using scraped data"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.system_message = """You are an expert financial analyst and market prediction AI. 
        Analyze market data including:
        - Financial news headlines and sentiment
        - Social media trends (Reddit, Twitter)
        - Insider trading activity
        - Crypto market data and whale movements
        - World events and geopolitical impacts
        - Foreign market indices and commodities
        - Congressional stock trades and government filings
        - Federal Reserve announcements
        - Real estate market indicators
        
        Provide predictions with:
        1. Market direction (BULLISH/BEARISH/NEUTRAL)
        2. Confidence score (0-100%)
        3. Key signals supporting the prediction
        4. Price targets and timeframes
        5. Risk factors
        6. Geopolitical impact assessment
        
        Be data-driven, objective, and always mention uncertainty."""
    
    async def analyze_market(self, financial_news: List[Dict], crypto_data: List[Dict], 
                            insider_trades: List[Dict], social_sentiment: List[Dict],
                            real_estate_data: Optional[Dict] = None,
                            world_events: Optional[Dict] = None,
                            foreign_markets: Optional[Dict] = None,
                            gov_filings: Optional[Dict] = None) -> Dict:
        """Comprehensive market analysis using multi-agent crew + vector memory."""
        from services.crew_definitions import run_prediction_crew

        # Query vector memory for similar past market regimes
        memory_context = ""
        try:
            from services.market_memory_service import get_prediction_context
            current_snapshot = self._build_regime_snapshot(
                financial_news, crypto_data, insider_trades, social_sentiment,
                world_events, foreign_markets
            )
            memory_context = await get_prediction_context("MARKET", current_snapshot, n_results=3)
            if memory_context:
                logger.info(f"Injecting {memory_context.count('Case')} historical regime(s) into prediction prompt")
        except Exception as e:
            logger.warning(f"Memory context fetch skipped: {e}")

        try:
            result = await run_prediction_crew(
                financial_news, crypto_data, insider_trades, social_sentiment,
                real_estate_data, world_events, foreign_markets, gov_filings,
                api_key=self.api_key,
                memory_context=memory_context,
            )
            return result
        except Exception as e:
            logger.error(f"Crew prediction failed, falling back to single-agent: {e}")
            return await self._single_agent_prediction(
                financial_news, crypto_data, insider_trades, social_sentiment,
                real_estate_data, world_events, foreign_markets, gov_filings,
                memory_context=memory_context,
            )

    def _build_regime_snapshot(self, news, crypto, trades, social, world_events, foreign_markets) -> Dict:
        """Build a compact regime description from current market data for memory queries."""
        snapshot = {"metrics": {}, "sentiment": {}}

        # Derive simple sentiment from news
        pos = neg = 0
        for item in (news or [])[:10]:
            title = item.get('title', '').lower()
            pos += sum(1 for w in ['surge', 'gain', 'rally', 'rise', 'bull'] if w in title)
            neg += sum(1 for w in ['fall', 'drop', 'crash', 'decline', 'bear'] if w in title)
        if pos > neg:
            snapshot["sentiment"]["news"] = f"Positive ({pos} bullish vs {neg} bearish headlines)"
        elif neg > pos:
            snapshot["sentiment"]["news"] = f"Negative ({neg} bearish vs {pos} bullish headlines)"
        else:
            snapshot["sentiment"]["news"] = "Mixed/Neutral"

        # Crypto pulse
        if crypto:
            snapshot["macro_context"] = f"{len(crypto)} crypto signals tracked"

        # World events
        if world_events:
            high = world_events.get('high_impact_events', [])
            if high:
                snapshot["macro_context"] = f"{len(high)} high-impact world events"

        # Live Fear & Greed (non-blocking best-effort)
        try:
            import asyncio
            from services.market_sentiment_service import get_fear_greed_index
            fg = asyncio.get_event_loop().run_until_complete(get_fear_greed_index()) if not asyncio.get_event_loop().is_running() else {"value": 50, "classification": "Neutral"}
            snapshot["sentiment"]["fg_index"] = fg.get("value", 50)
            snapshot["sentiment"]["fg_label"] = fg.get("classification", "Neutral")
        except Exception:
            snapshot["sentiment"]["fg_index"] = 50

        return snapshot

    async def _single_agent_prediction(self, financial_news, crypto_data,
                                        insider_trades, social_sentiment,
                                        real_estate_data=None, world_events=None,
                                        foreign_markets=None, gov_filings=None,
                                        memory_context="") -> Dict:
        """Fallback single-agent prediction if crew fails."""
        try:
            # Prepare data summary for AI
            data_summary = self._prepare_data_summary(
                financial_news, crypto_data, insider_trades, social_sentiment,
                real_estate_data, world_events, foreign_markets, gov_filings
            )

            # Inject historical memory context
            if memory_context:
                data_summary += f"\n\n{memory_context}"
            
            # Get AI analysis
            chat = LlmChat(
                api_key=self.api_key,
                session_id=f"prediction_{datetime.utcnow().timestamp()}",
                system_message=self.system_message
            ).with_model("openai", "gpt-5.2")
            
            prompt = f"""Analyze the following market data and provide a comprehensive prediction:

{data_summary}

Provide your analysis in JSON format with:
{{
  "overall_direction": "BULLISH/BEARISH/NEUTRAL",
  "confidence_score": 0-100,
  "timeframes": {{
    "intraday": {{"direction": "...", "target": "..."}},
    "short_term": {{"direction": "...", "target": "..."}},
    "medium_term": {{"direction": "...", "target": "..."}}
  }},
  "key_signals": ["signal1", "signal2", ...],
  "risk_factors": ["risk1", "risk2", ...],
  "crypto_outlook": "...",
  "stock_outlook": "...",
  "geopolitical_impact": "Brief assessment of how world events affect markets",
  "summary": "Brief analysis summary"
}}"""
            
            response = await chat.send_message(UserMessage(text=prompt))
            
            # Parse AI response - strip markdown code blocks if present
            try:
                clean = response.strip()
                if clean.startswith('```'):
                    clean = clean.split('\n', 1)[1] if '\n' in clean else clean[3:]
                    if clean.endswith('```'):
                        clean = clean[:-3].strip()
                prediction = json.loads(clean)
            except Exception:
                # If not valid JSON, return structured default
                prediction = {
                    'overall_direction': self._simple_sentiment_analysis(financial_news),
                    'confidence_score': 70,
                    'summary': 'AI analysis complete. See signals and risk factors below.',
                    'key_signals': ['AI analysis in progress'],
                    'timestamp': datetime.utcnow().isoformat()
                }
            
            prediction['timestamp'] = datetime.utcnow().isoformat()
            prediction['data_sources'] = {
                'news_articles': len(financial_news),
                'crypto_signals': len(crypto_data),
                'insider_trades': len(insider_trades),
                'social_posts': len(social_sentiment),
                'world_events': world_events.get('total_events', 0) if world_events else 0,
                'foreign_markets': len(foreign_markets.get('asia', []) + foreign_markets.get('europe', []) + foreign_markets.get('americas', [])) if foreign_markets else 0,
                'congressional_trades': gov_filings.get('congressional_count', 0) if gov_filings else 0,
                'fed_announcements': gov_filings.get('fed_count', 0) if gov_filings else 0,
            }
            
            return prediction
            
        except Exception as e:
            logger.error(f"Market prediction error: {str(e)}")
            return self._fallback_prediction()
    
    def _prepare_data_summary(self, news: List, crypto: List, trades: List, social: List,
                              real_estate: Dict = None, world_events: Dict = None,
                              foreign_markets: Dict = None, gov_filings: Dict = None) -> str:
        """Prepare concise data summary for AI"""
        summary = "MARKET DATA SUMMARY\n\n"
        
        # News headlines
        summary += "TOP FINANCIAL NEWS:\n"
        for item in news[:5]:
            summary += f"- [{item['source']}] {item['title']}\n"
        
        # Crypto signals
        summary += "\nCRYPTO MARKET SIGNALS:\n"
        for item in crypto[:5]:
            summary += f"- {item.get('symbol', 'N/A')}: {item.get('sentiment', 'N/A')}\n"
        
        # Insider trades
        summary += "\nRECENT INSIDER TRADES:\n"
        for trade in trades[:5]:
            summary += f"- {trade.get('ticker', 'N/A')}: {trade.get('trade_type', 'N/A')} {trade.get('value', 'N/A')}\n"
        
        # Social sentiment
        summary += "\nSOCIAL MEDIA SENTIMENT:\n"
        for post in social[:3]:
            summary += f"- {post.get('title', 'N/A')} (Score: {post.get('score', 0)})\n"

        # World Events (NEW)
        if world_events:
            summary += "\nWORLD EVENTS & GEOPOLITICAL:\n"
            high_impact = world_events.get('high_impact_events', [])
            for event in high_impact[:5]:
                sectors = ', '.join([s['sector'] for s in event.get('affected_sectors', [])[:3]])
                summary += f"- [{event.get('source', 'N/A')}] {event.get('title', 'N/A')}"
                if sectors:
                    summary += f" (Sectors: {sectors})"
                summary += "\n"
            # Sector summary
            affected = world_events.get('affected_sectors', [])
            if affected:
                summary += "  Top Affected Sectors: "
                summary += ', '.join([f"{s['sector']} (impact:{s['avg_impact']})" for s in affected[:5]])
                summary += "\n"

        # Foreign Markets (NEW)
        if foreign_markets:
            summary += "\nFOREIGN MARKETS & COMMODITIES:\n"
            for region_key in ['asia', 'europe']:
                region_data = foreign_markets.get(region_key, [])
                for mkt in region_data[:3]:
                    chg = mkt.get('change_percent', 0)
                    arrow = '+' if chg >= 0 else ''
                    summary += f"- {mkt.get('name', 'N/A')}: {arrow}{chg}% ({mkt.get('market_state', '')})\n"
            # Commodities
            for comm in foreign_markets.get('commodities', [])[:3]:
                chg = comm.get('change_percent', 0)
                arrow = '+' if chg >= 0 else ''
                summary += f"- {comm.get('name', 'N/A')}: ${comm.get('price', 0)} ({arrow}{chg}%)\n"
            # Correlation signals
            signals = foreign_markets.get('correlation_signals', [])
            if signals:
                summary += "  Correlation Signals: "
                summary += '; '.join([s['signal'] for s in signals[:3]])
                summary += "\n"

        # Government Filings (NEW)
        if gov_filings:
            # Congressional trades
            cong_trades = gov_filings.get('congressional_trades', [])
            if cong_trades:
                summary += "\nCONGRESSIONAL STOCK TRADES:\n"
                for ct in cong_trades[:5]:
                    summary += f"- {ct.get('description', 'N/A')}\n"
            # Fed announcements
            fed = gov_filings.get('fed_announcements', [])
            if fed:
                summary += "\nFEDERAL RESERVE ANNOUNCEMENTS:\n"
                for fa in fed[:3]:
                    summary += f"- {fa.get('title', 'N/A')}\n"
        
        # Real estate data
        if real_estate:
            summary += "\nREAL ESTATE MARKET INDICATORS:\n"
            if real_estate.get('housing'):
                housing = real_estate['housing']
                summary += f"- Housing Market: {housing.get('market_health', 'N/A')}\n"
                summary += f"  Sources: {len(housing.get('sources', []))} data providers\n"
            
            if real_estate.get('commercial'):
                commercial = real_estate['commercial']
                summary += "- Commercial RE:\n"
                summary += f"  Office: {commercial.get('office', {}).get('trend', 'N/A')}\n"
                summary += f"  Industrial: {commercial.get('industrial', {}).get('trend', 'N/A')}\n"
            
            if real_estate.get('mortgage_rates'):
                rates = real_estate['mortgage_rates']
                summary += f"- Mortgage Rates: {rates.get('30_year_fixed', 'N/A')}\n"
            
            if real_estate.get('trends'):
                trends = real_estate['trends']
                summary += "- Market Implications:\n"
                implications = trends.get('market_implications', {})
                summary += f"  Stocks: {implications.get('stocks', 'N/A')}\n"
                summary += f"  Crypto: {implications.get('crypto', 'N/A')}\n"
        
        return summary
    
    def _simple_sentiment_analysis(self, news: List[Dict]) -> str:
        """Simple sentiment fallback"""
        positive_words = ['surge', 'gain', 'rally', 'rise', 'bull', 'up', 'growth', 'profit']
        negative_words = ['fall', 'drop', 'crash', 'decline', 'bear', 'down', 'loss', 'recession']
        
        positive_count = 0
        negative_count = 0
        
        for item in news:
            title = item.get('title', '').lower()
            positive_count += sum(1 for word in positive_words if word in title)
            negative_count += sum(1 for word in negative_words if word in title)
        
        if positive_count > negative_count * 1.5:
            return 'BULLISH'
        elif negative_count > positive_count * 1.5:
            return 'BEARISH'
        return 'NEUTRAL'
    
    def _fallback_prediction(self) -> Dict:
        """Fallback prediction if AI fails"""
        return {
            'overall_direction': 'NEUTRAL',
            'confidence_score': 50,
            'summary': 'Insufficient data for prediction. Market appears neutral.',
            'key_signals': ['Data collection in progress'],
            'risk_factors': ['Limited data available'],
            'timestamp': datetime.utcnow().isoformat()
        }