import React from 'react';
import { TrendingUp, TrendingDown, Minus, Star } from 'lucide-react';

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

const NotificationItem = ({ notification: n, index }) => (
  <div className={`px-4 py-3 transition-colors ${!n.read ? 'bg-[#0052FF]/5' : 'hover:bg-slate-800/40'}`}
    data-testid={`notification-${index}`}>
    <div className="flex items-start gap-3">
      <div className="mt-0.5 flex-shrink-0">{verdictIcon(n.new_verdict)}</div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 mb-0.5">
          <span className="text-white text-sm font-semibold">{n.symbol}</span>
          {n.in_watchlist && <Star className="w-3 h-3 text-amber-400 fill-amber-400" />}
          {!n.read && <div className="w-1.5 h-1.5 bg-[#0052FF] rounded-full" />}
        </div>
        <p className="text-slate-300 text-xs">
          Verdict changed: <span className={verdictColor(n.old_verdict)}>{n.old_verdict}</span>
          {' → '}
          <span className={verdictColor(n.new_verdict)}>{n.new_verdict}</span>
        </p>
        {n.confidence > 0 && (
          <p className="text-slate-500 text-[10px] mt-0.5">Confidence: {n.confidence}%</p>
        )}
        <p className="text-slate-600 text-[10px] mt-0.5">
          {n.created_at ? new Date(n.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : ''}
        </p>
      </div>
    </div>
  </div>
);

export default NotificationItem;
