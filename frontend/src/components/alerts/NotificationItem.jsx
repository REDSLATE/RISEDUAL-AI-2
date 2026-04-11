import React from 'react';
import { TrendingUp, TrendingDown, Minus, Star, AlertTriangle } from 'lucide-react';

const verdictIcon = (verdict) => {
  if (verdict === 'BUY') return <TrendingUp className="w-4 h-4 text-emerald-400" />;
  if (verdict === 'SELL') return <TrendingDown className="w-4 h-4 text-red-400" />;
  return <Minus className="w-4 h-4 text-amber-400" />;
};

const verdictColor = (verdict) => {
  if (verdict === 'BUY') return 'text-emerald-400';
  if (verdict === 'SELL') return 'text-red-400';
  return 'text-amber-400';
};

const formatDate = (dateStr) => {
  if (!dateStr) return '';
  return new Date(dateStr).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
};

const ToxicSpikeNotification = ({ n, index }) => {
  const meta = n.metadata || {};
  const tickers = meta.affected_tickers || [];
  return (
    <div className={`px-4 py-3 transition-colors ${!n.read ? 'bg-red-500/5 border-l-2 border-red-500' : 'hover:bg-slate-800/40'}`}
      data-testid={`notification-toxic-${index}`}>
      <div className="flex items-start gap-3">
        <div className="mt-0.5 flex-shrink-0">
          <AlertTriangle className="w-4 h-4 text-red-400" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 mb-0.5">
            <span className="text-red-400 text-sm font-semibold">{n.title || 'Toxic Spikes'}</span>
            {!n.read && <div className="w-1.5 h-1.5 bg-red-500 rounded-full" />}
          </div>
          <p className="text-slate-300 text-xs leading-relaxed">{n.message}</p>
          {tickers.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1.5">
              {tickers.slice(0, 6).map(t => (
                <span key={t} className="text-[10px] bg-red-500/10 text-red-400 border border-red-500/20 rounded px-1.5 py-0.5">
                  {t}
                </span>
              ))}
              {tickers.length > 6 && <span className="text-[10px] text-slate-500">+{tickers.length - 6}</span>}
            </div>
          )}
          <p className="text-slate-600 text-[10px] mt-1">{formatDate(n.created_at)}</p>
        </div>
      </div>
    </div>
  );
};

const VerdictNotification = ({ n, index }) => (
  <div className={`px-4 py-3 transition-colors ${!n.read ? 'bg-[#35D6C8]/5' : 'hover:bg-slate-800/40'}`}
    data-testid={`notification-${index}`}>
    <div className="flex items-start gap-3">
      <div className="mt-0.5 flex-shrink-0">{verdictIcon(n.new_verdict)}</div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 mb-0.5">
          <span className="text-white text-sm font-semibold">{n.symbol}</span>
          {n.in_watchlist && <Star className="w-3 h-3 text-amber-400 fill-amber-400" />}
          {!n.read && <div className="w-1.5 h-1.5 bg-[#35D6C8] rounded-full" />}
        </div>
        <p className="text-slate-300 text-xs">
          Verdict changed: <span className={verdictColor(n.old_verdict)}>{n.old_verdict}</span>
          {' \u2192 '}
          <span className={verdictColor(n.new_verdict)}>{n.new_verdict}</span>
        </p>
        {n.confidence > 0 && (
          <p className="text-slate-500 text-[10px] mt-0.5">Confidence: {n.confidence}%</p>
        )}
        <p className="text-slate-600 text-[10px] mt-0.5">{formatDate(n.created_at)}</p>
      </div>
    </div>
  </div>
);

const NotificationItem = ({ notification: n, index }) => {
  if (n.type === 'toxic_spike') {
    return <ToxicSpikeNotification n={n} index={index} />;
  }
  return <VerdictNotification n={n} index={index} />;
};

export default NotificationItem;
