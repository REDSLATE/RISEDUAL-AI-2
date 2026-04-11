import React from 'react';
import { TrendingUp, TrendingDown, Zap } from 'lucide-react';
import { Card } from '../ui/card';
import { Badge } from '../ui/badge';
import { LoadingState } from './MacroShared';

const ForeignMarketsTab = ({ data, loading, changedSymbols = new Set() }) => {
  if (loading || !data) return <LoadingState text="Fetching global markets..." />;

  const regions = [
    { key: 'asia', label: 'Asia-Pacific', emoji: '\u{1F30F}' },
    { key: 'europe', label: 'Europe', emoji: '\u{1F30D}' },
    { key: 'americas', label: 'Americas', emoji: '\u{1F30E}' },
  ];

  return (
    <div className="space-y-5" data-testid="foreign-markets-tab">
      {data.correlation_signals?.length > 0 && (
        <Card className="bg-gradient-to-r from-amber-950/30 to-orange-950/20 border-amber-800/40 p-4 rounded-xl">
          <h3 className="text-amber-300 text-xs font-bold uppercase tracking-wider mb-2 flex items-center gap-2">
            <Zap className="w-3.5 h-3.5" /> Pre-Market Correlation Signals
          </h3>
          <div className="space-y-1">
            {data.correlation_signals.slice(0, 4).map((sig, i) => (
              <div key={sig.signal || i} className="flex items-center gap-2 text-sm">
                {sig.change_percent > 0
                  ? <TrendingUp className="w-3.5 h-3.5 text-lime-400 flex-shrink-0" />
                  : <TrendingDown className="w-3.5 h-3.5 text-orange-400 flex-shrink-0" />}
                <span className="text-slate-300">{sig.signal}</span>
                <Badge className={`ml-auto text-[10px] ${sig.severity === 'high' ? 'bg-orange-700 text-orange-400 border-orange-700' : 'bg-amber-900/40 text-amber-300 border-amber-800'}`}>
                  {sig.severity}
                </Badge>
              </div>
            ))}
          </div>
        </Card>
      )}

      {regions.map(region => {
        const items = data[region.key] || [];
        if (!items.length) return null;
        return (
          <div key={region.key}>
            <h3 className="text-slate-300 text-sm font-semibold mb-3 flex items-center gap-2">
              <span>{region.emoji}</span> {region.label}
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
              {items.map((mkt) => (
                <MarketCard key={mkt.symbol || mkt.name} market={mkt} isPulsing={changedSymbols.has(mkt.symbol)} />
              ))}
            </div>
          </div>
        );
      })}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {data.commodities?.length > 0 && (
          <div>
            <h3 className="text-slate-300 text-sm font-semibold mb-3">Commodities</h3>
            <div className="grid grid-cols-2 gap-3">
              {data.commodities.map((c) => <MarketCard key={c.symbol || c.name} market={c} compact isPulsing={changedSymbols.has(c.symbol)} />)}
            </div>
          </div>
        )}
        {data.currencies?.length > 0 && (
          <div>
            <h3 className="text-slate-300 text-sm font-semibold mb-3">Currencies</h3>
            <div className="grid grid-cols-2 gap-3">
              {data.currencies.map((c) => <MarketCard key={c.symbol || c.name} market={c} compact isPulsing={changedSymbols.has(c.symbol)} />)}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

const getMarketStateLabel = (state) => state === 'REGULAR' ? 'OPEN' : (state || 'CLOSED');
const getMarketStateStyle = (state) => {
  if (state === 'REGULAR') return 'bg-lime-600 text-lime-400';
  if (state === 'PRE') return 'bg-amber-900/40 text-amber-300';
  return 'bg-slate-700 text-slate-400';
};
const getMarketCardBg = (isPulsing, isHot, isUp) => {
  if (isPulsing) return 'ring-2 ring-[#3DE8D9]/50 animate-pulse';
  if (isHot) return isUp ? 'bg-green-500/20 border-lime-700/30' : 'bg-red-500/15 border-orange-700/30';
  return 'bg-slate-700/40 border-slate-400/30/30';
};

const MarketCard = ({ market, compact, isPulsing }) => {
  const isUp = market.change_percent >= 0;
  const absPct = Math.abs(market.change_percent || 0).toFixed(2);
  const isHot = Math.abs(market.change_percent || 0) >= 2;

  return (
    <Card className={`p-3 rounded-xl border transition-all hover:border-slate-600 ${getMarketCardBg(isPulsing, isHot, isUp)}`} data-testid={`market-card-${market.symbol}`}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-white text-xs font-semibold truncate">{market.name}</span>
        <span className={`text-[10px] px-1.5 py-0.5 rounded ${getMarketStateStyle(market.market_state)}`}>{getMarketStateLabel(market.market_state)}</span>
      </div>
      <div className="flex items-end justify-between">
        <span className="text-white text-lg font-bold tabular-nums">
          {market.price ? (market.region === 'Currency' ? market.price.toFixed(4) : market.price.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})) : 'N/A'}
        </span>
        <div className={`flex items-center gap-1 text-xs font-semibold ${isUp ? 'text-lime-400' : 'text-orange-400'}`}>
          {isUp ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
          {isUp ? '+' : '-'}{absPct}%
        </div>
      </div>
      {!compact && (
        <div className="mt-2 w-full bg-slate-700/40 rounded-full h-1">
          <div className={`h-1 rounded-full ${isUp ? 'bg-green-500' : 'bg-red-500'}`}
            style={{ width: `${Math.min(Math.abs(market.change_percent || 0) * 10, 100)}%` }} />
        </div>
      )}
    </Card>
  );
};

export default ForeignMarketsTab;
