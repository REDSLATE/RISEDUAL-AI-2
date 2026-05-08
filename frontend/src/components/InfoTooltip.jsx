import React from 'react';
import { HelpCircle } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from './ui/tooltip';

const TOOLTIPS = {
  watchlist: "Track your favorite tickers with real-time prices and AI-powered insights",
  'watchlist-intelligence': "AI analyzes your watchlist holdings for patterns, risks, and opportunities",
  'ai-war-room': "Multi-model AI consensus engine — runs your query through multiple AI models",
  'ai-hypothesis': "Enter any ticker to get AI-generated bull/bear hypothesis with confidence scores",
  'ai-intelligence': "AI-powered chart pattern detection and 30-second stock briefs",
  'sector-heatmap': "Visual overview of all market sectors — green gaining, red losing",
  'fear-greed': "Market sentiment from 0 (Extreme Fear) to 100 (Extreme Greed)",
  'pnl-tracker': "Track your realized and unrealized P&L across paper and live trades",
  'live-insights': "Real-time AI-generated trading insights and market commentary",
  'order-flow': "Live bid/ask volume — see where buy and sell pressure is concentrated",
  'whale-radar': "Real-time whale transaction monitoring — large crypto and institutional moves",
  'trading-bots': "Grid, Signal, and Webhook bots — all start OFF. Toggle on when ready",
  'market-prediction': "AI analyzes macro data, news, insider trades, and world events for predictions",
  'company-research': "Deep-dive into any company — financials, news, insider activity",
  'macro-dashboard': "Global economic indicators, yields, currencies, and commodity trends",
  'options-radar': "Track unusual options activity — large blocks and sweeps",
  'options-flow': "Real-time options flow screening with volume and open interest analysis",
  'dark-pool': "Real dark pool transaction data — see where institutional money flows",
  'crypto': "Crypto market overview with prices, dominance charts, and whale alerts",
  'referral': "Invite friends for Pro trial. Top referrers climb the leaderboard",
  'paper-trading': "Practice with $100K virtual money. Test strategies risk-free",
  'smart-orders': "Ladder entries, trailing SL/TP, break-even protection, multi-TP levels",
  'risk-calculator': "Calculate position size based on risk tolerance — %, fixed $, or Kelly criterion",
  'market-scanner': "10 pre-built strategies + custom rule builder with 23 indicators + AI validation",
};

const InfoTooltip = ({ id, side = "top" }) => {
  const text = TOOLTIPS[id];
  if (!text) return null;

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button className="inline-flex items-center justify-center w-5 h-5 rounded-full text-slate-500 hover:text-[#3DE8D9] hover:bg-slate-700/50 transition-colors ml-1" data-testid={`tooltip-${id}`}>
            <HelpCircle className="w-3.5 h-3.5" />
          </button>
        </TooltipTrigger>
        <TooltipContent side={side} className="max-w-[250px] bg-slate-900 border-slate-700 text-slate-200 text-xs px-3 py-2">
          <p>{text}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
};

export default InfoTooltip;
export { TOOLTIPS };
