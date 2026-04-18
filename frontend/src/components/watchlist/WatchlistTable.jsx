import React from 'react';
import { Star, X, TrendingUp, TrendingDown, Sparkles, Swords } from 'lucide-react';
import { openWarRoomForTicker } from '../../utils/deepLink';
import SparkLine from '../SparkLine';
import SmartMoneyShiftAlerts from './SmartMoneyShiftAlerts';

const scoreClasses = (score) => {
  if (score == null) return 'text-slate-500 bg-slate-700/30 border-slate-600/30';
  if (score >= 60) return 'text-emerald-400 bg-emerald-500/15 border-emerald-500/30';
  if (score <= 40) return 'text-red-400 bg-red-500/15 border-red-500/30';
  return 'text-amber-400 bg-amber-500/15 border-amber-500/30';
};

const WatchlistRow = ({ item, sm, sparkPoints, onAskAI, onRemove }) => {
  const hasScore = sm && sm.score != null;
  const tooltip = hasScore
    ? `Smart Money Score ${sm.score}/100 — ${sm.signal.toUpperCase()} (${sm.bullish_count} institutions adding · ${sm.bearish_count} trimming${sm.holder_count ? ` · ${sm.holder_count} tracked holders` : ''})`
    : 'Smart Money Score: insufficient 13F data';
  const askScoreBreakdown = () => {
    const prompt = `Break down the Smart Money Score for ${item.symbol} (currently ${sm.score}/100, ${sm.signal}). ${sm.bullish_count} tracked institutions increased their position last quarter and ${sm.bearish_count} reduced. What's the likely thesis behind the biggest moves?`;
    window.dispatchEvent(new CustomEvent('risedualai-open-chat', { detail: { prefill: prompt, autoSend: true } }));
  };
  return (
    <div className="flex items-center justify-between p-3 bg-[#1E293B] rounded-lg hover:bg-slate-700 transition-colors">
      <div className="flex items-center gap-3">
        <span className="text-white font-medium">{item.symbol}</span>
        {hasScore && (
          <button
            onClick={askScoreBreakdown}
            className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px] font-bold tabular-nums transition-colors ${scoreClasses(sm.score)} hover:brightness-125 cursor-pointer`}
            title={tooltip}
            data-testid={`watchlist-smart-score-${item.symbol}`}
          >
            SM {sm.score}
          </button>
        )}
        {sparkPoints?.length >= 2 && (
          <SparkLine
            points={sparkPoints}
            width={50}
            height={14}
            className="opacity-90 hover:opacity-100"
            data-testid={`watchlist-sparkline-${item.symbol}`}
          />
        )}
        {item.changePercent !== 0 && (
          <div className={`flex items-center gap-1 text-sm ${item.changePercent >= 0 ? 'text-lime-400' : 'text-orange-400'}`}>
            {item.changePercent >= 0 ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
            {Math.abs(item.changePercent).toFixed(2)}%
          </div>
        )}
      </div>
      <div className="flex items-center gap-2">
        {item.price > 0 && (
          <span className="text-slate-300 text-sm">${item.price.toFixed(2)}</span>
        )}
        <button
          onClick={() => onAskAI(item)}
          className="text-slate-400 hover:text-[#3DE8D9] transition-colors"
          title={`Ask AI to analyze ${item.symbol}`}
          data-testid={`watchlist-delegate-ai-${item.symbol}`}
        >
          <Sparkles className="w-4 h-4" />
        </button>
        {hasScore && (
          <button
            onClick={() => openWarRoomForTicker({ ticker: item.symbol, source: 'SM Board' })}
            className="text-slate-400 hover:text-orange-400 transition-colors"
            title={`Open ${item.symbol} in AI War Room`}
            data-testid={`watchlist-warroom-${item.symbol}`}
          >
            <Swords className="w-4 h-4" />
          </button>
        )}
        <button
          onClick={() => onRemove(item.symbol)}
          className="text-slate-400 hover:text-orange-400 transition-colors"
          title="Remove"
          data-testid={`watchlist-remove-${item.symbol}`}
        >
          <X className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
};

/**
 * Scrolling list of ticker rows, preceded by any Smart Money shift alerts
 * the user has subscribed to.
 */
const WatchlistTable = ({ watchlist, smartScores, smsShifts, smsHistory, onRemove }) => {
  const askAIForTicker = (item) => {
    const prompt = `Analyze ${item.symbol} for me: current price, technical levels (support/resistance, RSI, moving averages), recent news or catalysts, and the latest 13F institutional holder changes. Give me a concise take.`;
    window.dispatchEvent(new CustomEvent('risedualai-open-chat', { detail: { prefill: prompt, autoSend: true } }));
  };
  return (
    <>
      <SmartMoneyShiftAlerts shifts={smsShifts} />
      <div className="space-y-2 max-h-96 overflow-y-auto">
        {watchlist.length === 0 ? (
          <div className="text-center py-10 text-slate-400" data-testid="watchlist-empty">
            <div className="w-14 h-14 bg-slate-800/80 rounded-2xl flex items-center justify-center mx-auto mb-3 border border-slate-400/30/40">
              <Star className="w-7 h-7 text-slate-400" />
            </div>
            <p className="text-slate-300 text-sm font-medium mb-1">No tickers yet</p>
            <p className="text-slate-300 text-xs">Search for a stock symbol above or use the search bar to add tickers to your watchlist</p>
          </div>
        ) : (
          watchlist.map((item) => (
            <WatchlistRow
              key={item.symbol}
              item={item}
              sm={smartScores[item.symbol]}
              sparkPoints={smsHistory[item.symbol]}
              onAskAI={askAIForTicker}
              onRemove={onRemove}
            />
          ))
        )}
      </div>
    </>
  );
};

export default WatchlistTable;
