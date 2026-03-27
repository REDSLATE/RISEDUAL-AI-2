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
        - Technical indicators
        
        Provide predictions with:
        1. Market direction (BULLISH/BEARISH/NEUTRAL)
        2. Confidence score (0-100%)
        3. Key signals supporting the prediction
        4. Price targets and timeframes
        5. Risk factors
        
        Be data-driven, objective, and always mention uncertainty."""
    
    async def analyze_market(self, financial_news: List[Dict], crypto_data: List[Dict], 
                            insider_trades: List[Dict], social_sentiment: List[Dict],
                            real_estate_data: Optional[Dict] = None) -> Dict:
        """Comprehensive market analysis using all data sources"""
        try:
            # Prepare data summary for AI
            data_summary = self._prepare_data_summary(
                financial_news, crypto_data, insider_trades, social_sentiment, real_estate_data
            )
            
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
  "summary": "Brief analysis summary"
}}"""
            
            response = await chat.send_message(UserMessage(text=prompt))
            
            # Parse AI response
            try:
                prediction = json.loads(response)
            except:
                # If not valid JSON, return structured default
                prediction = {
                    'overall_direction': self._simple_sentiment_analysis(financial_news),
                    'confidence_score': 70,
                    'summary': response[:500],
                    'key_signals': ['AI analysis in progress'],
                    'timestamp': datetime.utcnow().isoformat()
                }
            
            prediction['timestamp'] = datetime.utcnow().isoformat()
            prediction['data_sources'] = {
                'news_articles': len(financial_news),
                'crypto_signals': len(crypto_data),
                'insider_trades': len(insider_trades),
                'social_posts': len(social_sentiment)
            }
            
            return prediction
            
        except Exception as e:
            logger.error(f"Market prediction error: {str(e)}")
            return self._fallback_prediction()
    
    def _prepare_data_summary(self, news: List, crypto: List, trades: List, social: List, real_estate: Dict = None) -> str:
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
        
        # Real estate data
        if real_estate:
            summary += "\nREAL ESTATE MARKET INDICATORS:\n"
            if real_estate.get('housing'):
                housing = real_estate['housing']
                summary += f"- Housing Market: {housing.get('market_health', 'N/A')}\n"
                summary += f"  Sources: {len(housing.get('sources', []))} data providers\n"
            
            if real_estate.get('commercial'):
                commercial = real_estate['commercial']
                summary += f"- Commercial RE:\n"
                summary += f"  Office: {commercial.get('office', {}).get('trend', 'N/A')}\n"
                summary += f"  Industrial: {commercial.get('industrial', {}).get('trend', 'N/A')}\n"
            
            if real_estate.get('mortgage_rates'):
                rates = real_estate['mortgage_rates']
                summary += f"- Mortgage Rates: {rates.get('30_year_fixed', 'N/A')}\n"
            
            if real_estate.get('trends'):
                trends = real_estate['trends']
                summary += f"- Market Implications:\n"
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