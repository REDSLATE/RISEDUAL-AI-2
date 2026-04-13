import React, { useState } from 'react';
import { X, HelpCircle, Bot, Layers, Calculator, Search, BarChart3, TrendingUp, Shield, Brain, Radio, Webhook, Grid3X3, ChevronRight, BookOpen, Zap, Target, Activity } from 'lucide-react';
import { Badge } from './ui/badge';

const SECTIONS = [
  {
    id: 'overview', title: 'Getting Started', icon: BookOpen, color: 'text-[#3DE8D9]',
    items: [
      { title: 'Dashboard Overview', content: 'Your dashboard shows real-time market data: stock/crypto tickers, watchlist, sector heatmap, fear & greed gauge, AI predictions, and more. All data updates automatically.' },
      { title: 'Account Types', content: 'Free: Basic market data and watchlist. Pro: All AI features, smart orders, bots, market scanner, and unlimited predictions. Trial: 7-day Pro access via referral.' },
      { title: 'Navigation', content: 'Use the top navbar to access all tools. Click your avatar (top-right) for the full menu: Smart Orders, Risk Calculator, Market Scanner, Trading Bots, and more.' },
    ],
  },
  {
    id: 'smart-orders', title: 'Smart Orders', icon: Layers, color: 'text-[#3DE8D9]',
    items: [
      { title: 'What are Smart Orders?', content: 'Advanced orders with ladder entries, trailing stop-loss, trailing take-profit, break-even protection, and multi-TP levels. Think of it as Altrady-style execution built into RISEDUAL.' },
      { title: 'Ladder Entry', content: 'Split your entry across multiple price levels (2-10 levels). Choose equal distribution, or weight toward bottom/top prices. Great for DCA into positions.' },
      { title: 'Trailing Stop-Loss', content: 'Your stop-loss follows the price up (for buys). Set a trailing % — as the price rises, the SL automatically adjusts. Emergency SL provides a hard floor.' },
      { title: 'Multi Take-Profit', content: 'Set up to 5 TP levels, each selling a % of your position. Example: TP1 at +5% (sell 40%), TP2 at +10% (sell 30%), TP3 at +15% (sell 30%). Each TP can trail.' },
      { title: 'Break-Even Protection', content: 'After your first TP hits, the stop-loss automatically moves to your entry price — so you can never lose on the trade.' },
      { title: '3 Execution Modes', content: 'Paper: Simulated with your paper portfolio. Live: Real execution through connected broker (owner only). Simulate/Preview: See projected R:R without executing.' },
    ],
  },
  {
    id: 'risk-calc', title: 'Risk Calculator', icon: Calculator, color: 'text-[#3DE8D9]',
    items: [
      { title: 'Position Sizing', content: 'Enter your entry, stop-loss, and take-profit prices. The calculator tells you exactly how many shares to buy based on your risk tolerance.' },
      { title: '% Risk Method', content: 'Risk a fixed % of your account per trade (default 2%). Quick presets: 1%, 2%, 3%, 5%. This is the most common professional approach.' },
      { title: 'Fixed Dollar Method', content: 'Set a fixed dollar amount you are willing to lose per trade (e.g., $500). Position size is calculated from that.' },
      { title: 'Kelly Criterion', content: 'Optimal sizing based on your win rate and average win/loss ratio. Capped at 25% to prevent over-leverage. Best for experienced traders with tracked statistics.' },
      { title: 'R:R Gauge', content: 'The visual gauge shows your risk:reward ratio. Green (2:1+) = great setup. Yellow (1:1) = break-even. Red (<1:1) = risk exceeds reward.' },
      { title: 'Apply to Smart Order', content: 'Click "Apply to Smart Order" to transfer your calculated position size, SL, and TP directly into a new Smart Order.' },
    ],
  },
  {
    id: 'scanner', title: 'Market Scanner', icon: Search, color: 'text-violet-400',
    items: [
      { title: 'Pre-built Strategies', content: '10 ready-made scans: RSI Oversold/Overbought, MACD Cross, Bollinger Squeeze, EMA Golden Cross, Volume Spike, 52-Week High/Low, Momentum Breakout.' },
      { title: 'Custom Rule Builder', content: 'Build your own scan rules using 23+ indicators with AND/OR logic. Example: "RSI < 30 AND Volume > 2x avg AND Trend = bullish". Save rules for reuse.' },
      { title: 'Available Indicators', content: 'RSI (14/7), MACD (line/signal/histogram), Bollinger Bands, SMA (20/50/200), EMA (9/21/50), ATR, Volume Ratio, 52-Week levels, Price Changes (1d/5d/20d), Trend.' },
      { title: 'AI Validation', content: 'Click "AI Validate" on any scan results. Our adversarial AI (GPT-5.2) evaluates each signal with a confidence score (0-100), verdict, risk level, and recommended action.' },
      { title: 'Signal Strength', content: 'Each match shows a strength % bar. Higher strength = more extreme the condition. Example: RSI at 15 scores higher than RSI at 28 (both oversold, but 15 is more extreme).' },
    ],
  },
  {
    id: 'bots', title: 'Trading Bots', icon: Bot, color: 'text-amber-400',
    items: [
      { title: 'Important: All Bots Start OFF', content: 'Every bot you create defaults to OFF. You must manually toggle it ON to activate. This prevents accidental trades. Toggle is the green/gray switch on each bot card.' },
      { title: 'Grid Bot', content: 'Auto-trades within a price range. Set upper/lower prices and grid levels. Bot buys at lower grid lines and sells at upper ones. Ideal for sideways/ranging markets (especially crypto).' },
      { title: 'How Grid Bot Works', content: 'Example: BTC Grid with $68K-$72K range, 5 levels. Buy orders at $68K, $69K, $70K. Sell orders at $71K, $72K. When a buy fills, it flips to a sell. Continuous profit in ranges.' },
      { title: 'Signal Bot', content: 'Connects to the Market Scanner. When AI validates a signal above your confidence threshold, the bot auto-creates a Smart Order with trailing SL and TP.' },
      { title: 'Signal Bot Config', content: 'Min Confidence: Only execute when AI scores above this % (default 70). Auto SL/TP: Automatically sets stop-loss and take-profit as % from entry.' },
      { title: 'TradingView Webhook Bot', content: 'Receives POST webhooks from TradingView alerts. Set up alerts in TradingView → send to your unique webhook URL → bot executes the trade.' },
      { title: 'Webhook Setup', content: '1. Create a Webhook bot. 2. Copy the webhook URL. 3. In TradingView, create an alert → set webhook URL → payload: {"action":"buy","symbol":"AAPL","qty":10}.' },
      { title: 'Bot Safety', content: 'All bots operate in Paper mode by default. Rate limits prevent runaway trades. The toggle switch provides instant ON/OFF control. No trades execute while OFF.' },
    ],
  },
  {
    id: 'ai-features', title: 'AI Features', icon: Brain, color: 'text-violet-400',
    items: [
      { title: 'AI War Room', content: 'Multi-model consensus engine. Runs your query through multiple AI models and provides a unified analysis. Great for complex market decisions.' },
      { title: 'AI Hypothesis', content: 'Enter a ticker to get an AI-generated bull/bear hypothesis with confidence score. Consensus mode runs 3 models for higher accuracy.' },
      { title: 'RiseDualGPT Chat', content: 'Your AI trading assistant with persistent memory. It remembers your preferences, favorite tickers, and trading style. Pin important insights for recall.' },
      { title: 'Market Predictions', content: 'AI analyzes macro data, news, crypto, insider trades, world events, and generates directional predictions with confidence scores and catalysts.' },
      { title: 'Ticker-Specific Predictions', content: 'Search for any ticker in Market Predictions to get AI analysis specific to that stock/crypto.' },
    ],
  },
  {
    id: 'trading', title: 'Trading Tools', icon: TrendingUp, color: 'text-lime-400',
    items: [
      { title: 'Paper Trading', content: 'Practice with $100,000 virtual money. Buy/sell stocks and crypto without real risk. Track your P&L in the Paper Trading panel.' },
      { title: 'Broker Connections', content: 'Connect real brokers (Alpaca, etc.) via OAuth for live trading. Owner accounts can execute real trades through Smart Orders.' },
      { title: 'Strategy Builder', content: 'Create custom trading strategies with entry/exit rules. Backtest them against historical data to see how they would have performed.' },
      { title: 'Strategy Marketplace', content: 'Browse and use strategies shared by other traders. Rate and review strategies to help the community.' },
      { title: 'Dark Pool Data', content: 'Real dark pool transaction data via Polygon.io. See where institutional money is flowing.' },
      { title: 'Options Flow', content: 'Track unusual options activity — large block trades, sweeps, and unusual volume that may signal institutional moves.' },
    ],
  },
  {
    id: 'data', title: 'Market Data', icon: BarChart3, color: 'text-blue-400',
    items: [
      { title: 'Watchlist', content: 'Track your favorite tickers with real-time prices. Add/remove symbols. Watchlist Intelligence provides AI-powered insights on your holdings.' },
      { title: 'Sector Heatmap', content: 'Visual overview of all market sectors. Green = gaining, Red = losing. Click sectors for detailed analysis.' },
      { title: 'Fear & Greed Gauge', content: 'Market sentiment indicator from 0 (Extreme Fear) to 100 (Extreme Greed). Useful for contrarian timing.' },
      { title: 'Whale Radar', content: 'Real-time whale transaction monitoring. Tracks large crypto moves and institutional activity.' },
      { title: 'Order Flow Heatmap', content: 'Live bid/ask volume visualization. See where buy and sell pressure is concentrated at each price level.' },
    ],
  },
];

const HelpCenter = ({ onClose }) => {
  const [activeSection, setActiveSection] = useState('overview');
  const [activeItem, setActiveItem] = useState(null);

  const section = SECTIONS.find(s => s.id === activeSection);

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm" data-testid="help-center">
      <div className="w-full max-w-4xl max-h-[90vh] overflow-hidden bg-[#0B1426] border border-slate-600/30 rounded-2xl flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-400/20 shrink-0">
          <div className="flex items-center gap-2">
            <HelpCircle className="w-5 h-5 text-[#3DE8D9]" />
            <h2 className="text-white font-bold text-base">Help Center</h2>
            <span className="text-slate-400 text-[10px]">RISEDUAL AI User Manual</span>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white"><X className="w-5 h-5" /></button>
        </div>

        <div className="flex flex-1 min-h-0">
          {/* Sidebar */}
          <div className="w-56 border-r border-slate-400/15 overflow-y-auto py-2 shrink-0" data-testid="help-sidebar">
            {SECTIONS.map(s => {
              const Icon = s.icon;
              return (
                <button key={s.id} onClick={() => { setActiveSection(s.id); setActiveItem(null); }}
                  className={`w-full flex items-center gap-2 px-4 py-2.5 text-left transition-colors ${
                    activeSection === s.id ? 'bg-slate-800/60 text-white' : 'text-slate-400 hover:text-white hover:bg-slate-800/30'
                  }`} data-testid={`help-section-${s.id}`}>
                  <Icon className={`w-4 h-4 ${s.color} shrink-0`} />
                  <span className="text-xs font-medium">{s.title}</span>
                  <Badge className="text-[8px] bg-slate-700/50 text-slate-400 ml-auto">{s.items.length}</Badge>
                </button>
              );
            })}
          </div>

          {/* Content */}
          <div className="flex-1 overflow-y-auto p-5" data-testid="help-content">
            {section && (
              <div className="space-y-2">
                <div className="flex items-center gap-2 mb-4">
                  <section.icon className={`w-6 h-6 ${section.color}`} />
                  <h3 className="text-white text-lg font-bold">{section.title}</h3>
                </div>

                {section.items.map((item, i) => (
                  <button key={`${section.id}-${i}`}
                    onClick={() => setActiveItem(activeItem === i ? null : i)}
                    className={`w-full text-left rounded-xl border transition-all ${
                      activeItem === i ? 'bg-slate-800/60 border-[#3DE8D9]/30' : 'bg-slate-800/20 border-slate-600/15 hover:border-slate-500/30'
                    }`}>
                    <div className="flex items-center justify-between px-4 py-3">
                      <span className="text-white text-sm font-medium">{item.title}</span>
                      <ChevronRight className={`w-4 h-4 text-slate-400 transition-transform ${activeItem === i ? 'rotate-90' : ''}`} />
                    </div>
                    {activeItem === i && (
                      <div className="px-4 pb-3 border-t border-slate-600/15 pt-2">
                        <p className="text-slate-300 text-xs leading-relaxed">{item.content}</p>
                      </div>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default HelpCenter;
