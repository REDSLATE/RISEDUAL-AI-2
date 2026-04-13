import React from 'react';
import { ShieldCheck, AlertTriangle, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { Badge } from '../ui/badge';

const VERDICT_CONFIG = {
  strong_buy: { color: 'text-lime-400', bg: 'bg-lime-500/10', icon: TrendingUp, label: 'STRONG BUY' },
  buy: { color: 'text-lime-300', bg: 'bg-lime-500/10', icon: TrendingUp, label: 'BUY' },
  hold: { color: 'text-amber-400', bg: 'bg-amber-500/10', icon: Minus, label: 'HOLD' },
  sell: { color: 'text-red-300', bg: 'bg-red-500/10', icon: TrendingDown, label: 'SELL' },
  strong_sell: { color: 'text-red-400', bg: 'bg-red-500/10', icon: TrendingDown, label: 'STRONG SELL' },
  avoid: { color: 'text-slate-400', bg: 'bg-slate-600/20', icon: AlertTriangle, label: 'AVOID' },
};

const RISK_COLORS = {
  low: 'bg-lime-500/15 text-lime-400',
  medium: 'bg-amber-500/15 text-amber-400',
  high: 'bg-red-500/15 text-red-400',
  extreme: 'bg-red-700/20 text-red-300',
};

const ConfidenceRing = ({ value }) => {
  const pct = Math.min(value || 0, 100);
  const color = pct >= 70 ? '#10B981' : pct >= 40 ? '#F59E0B' : '#EF4444';
  const circumference = 2 * Math.PI * 16;
  const offset = circumference - (pct / 100) * circumference;
  return (
    <div className="relative w-10 h-10 shrink-0" data-testid="confidence-ring">
      <svg className="w-10 h-10 -rotate-90" viewBox="0 0 36 36">
        <circle cx="18" cy="18" r="16" stroke="#1E293B" strokeWidth="3" fill="none" />
        <circle cx="18" cy="18" r="16" stroke={color} strokeWidth="3" fill="none"
          strokeDasharray={circumference} strokeDashoffset={offset} strokeLinecap="round" />
      </svg>
      <span className="absolute inset-0 flex items-center justify-center text-[9px] font-bold" style={{ color }}>
        {pct}
      </span>
    </div>
  );
};

const ValidatedMatchRow = ({ match }) => {
  const vc = VERDICT_CONFIG[match.ai_verdict] || VERDICT_CONFIG.hold;
  const VIcon = vc.icon;

  return (
    <div className="flex items-center gap-3 py-2.5 px-3 bg-slate-800/40 rounded-xl border border-slate-600/15" data-testid={`validated-${match.symbol}`}>
      <ConfidenceRing value={match.ai_confidence} />

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className="text-white text-sm font-bold">{match.symbol}</span>
          <span className="text-slate-300 text-xs">${match.price}</span>
          <Badge className={`text-[8px] px-1.5 ${vc.bg} ${vc.color}`}>
            <VIcon className="w-2.5 h-2.5 mr-0.5 inline" />{vc.label}
          </Badge>
          <Badge className={`text-[8px] px-1.5 ${RISK_COLORS[match.ai_risk] || RISK_COLORS.medium}`}>
            {(match.ai_risk || 'medium').toUpperCase()} RISK
          </Badge>
        </div>
        <p className="text-slate-400 text-[10px] leading-snug truncate">{match.ai_reasoning}</p>
        {match.ai_action && (
          <p className="text-[#3DE8D9] text-[9px] mt-0.5">{match.ai_action}</p>
        )}
      </div>

      <div className="text-right shrink-0">
        <p className="text-slate-500 text-[9px]">RSI {match.rsi || '?'}</p>
        <p className="text-slate-500 text-[9px]">Vol {match.vol_ratio || match.volume_ratio || '?'}x</p>
      </div>
    </div>
  );
};

const ValidationSummary = ({ summary }) => (
  <div className="flex items-center gap-3 bg-[#111C30] rounded-xl p-3 border border-[#3DE8D9]/20" data-testid="validation-summary">
    <ShieldCheck className="w-5 h-5 text-[#3DE8D9] shrink-0" />
    <div className="flex items-center gap-4 text-[10px]">
      <span className="text-white font-semibold">AI Validated</span>
      <span className="text-lime-400">{summary.strong_signals} strong</span>
      <span className="text-amber-400">{summary.moderate_signals} moderate</span>
      <span className="text-red-400">{summary.weak_signals} weak</span>
      <span className="text-slate-400">Avg: {summary.avg_confidence}%</span>
    </div>
  </div>
);

export { ValidatedMatchRow, ValidationSummary };
