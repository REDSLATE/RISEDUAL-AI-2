import React from 'react';
import { Zap, TrendingUp, AlertTriangle, Shield, ChevronRight } from 'lucide-react';
import { Card } from '../ui/card';
import { Badge } from '../ui/badge';

const BriefView = ({ data }) => {
  const b = data.brief;
  const verdictStyles = {
    buy: { bg: 'bg-emerald-500', border: 'border-emerald-700/50', text: 'text-emerald-400', glow: 'from-emerald-950/40 to-green-950/40' },
    hold: { bg: 'bg-amber-500', border: 'border-amber-700/50', text: 'text-amber-400', glow: 'from-amber-950/40 to-yellow-950/40' },
    sell: { bg: 'bg-red-500', border: 'border-red-700/50', text: 'text-red-400', glow: 'from-red-950/40 to-rose-950/40' },
  };
  const vs = verdictStyles[b.verdict] || verdictStyles.hold;

  return (
    <div className="space-y-4" data-testid="brief-results">
      <Card className={`bg-gradient-to-r ${vs.glow} ${vs.border} border rounded-xl p-5`}>
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1">
            <div className="flex items-center gap-2 mb-2">
              <Zap className={`w-5 h-5 ${vs.text}`} />
              <h3 className="text-white text-lg font-bold">{data.symbol}</h3>
              <Badge className={`${vs.bg} text-white text-xs font-bold px-3 py-0.5 uppercase`} data-testid="brief-verdict">
                {b.verdict}
              </Badge>
            </div>
            <p className="text-white text-base font-semibold mb-1">{b.headline}</p>
            <p className="text-slate-300 text-sm">{b.brief}</p>
          </div>
          <div className="text-right shrink-0">
            <div className="w-14 h-14 rounded-full border-4 border-slate-700 flex items-center justify-center">
              <span className={`text-lg font-black ${vs.text}`}>{b.confidence}</span>
            </div>
            <p className="text-slate-500 text-[8px] mt-0.5">Confidence</p>
          </div>
        </div>
      </Card>

      {b.key_metrics?.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          {b.key_metrics.map((m, i) => {
            const sentColor = m.sentiment === 'positive' ? 'text-emerald-400' : m.sentiment === 'negative' ? 'text-red-400' : 'text-amber-400';
            return (
              <Card key={`m-${i}`} className="bg-slate-800/60 border-slate-700/40 rounded-xl p-3 text-center">
                <p className={`text-base font-bold ${sentColor}`}>{m.value}</p>
                <p className="text-slate-500 text-[9px]">{m.label}</p>
              </Card>
            );
          })}
        </div>
      )}

      <div className="grid grid-cols-3 gap-2">
        {[
          { label: '1 Week', val: data.performance?.['1w'] },
          { label: '1 Month', val: data.performance?.['1m'] },
          { label: '3 Months', val: data.performance?.['3m'] },
        ].map(p => (
          <Card key={p.label} className="bg-slate-800/60 border-slate-700/40 rounded-xl p-3 text-center">
            <p className={`text-sm font-bold ${(p.val || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {(p.val || 0) >= 0 ? '+' : ''}{p.val || 0}%
            </p>
            <p className="text-slate-500 text-[9px]">{p.label}</p>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {b.catalysts?.length > 0 && (
          <Card className="bg-emerald-950/15 border-emerald-800/25 rounded-xl p-4">
            <div className="flex items-center gap-2 mb-2">
              <TrendingUp className="w-4 h-4 text-emerald-400" />
              <span className="text-emerald-400 text-xs font-semibold uppercase">Catalysts</span>
            </div>
            <ul className="space-y-1">
              {b.catalysts.map((c, i) => (
                <li key={`c-${i}`} className="text-slate-300 text-xs flex items-start gap-1.5">
                  <ChevronRight className="w-3 h-3 text-emerald-500 mt-0.5 shrink-0" />{c}
                </li>
              ))}
            </ul>
          </Card>
        )}
        {b.risks?.length > 0 && (
          <Card className="bg-red-950/15 border-red-800/25 rounded-xl p-4">
            <div className="flex items-center gap-2 mb-2">
              <AlertTriangle className="w-4 h-4 text-red-400" />
              <span className="text-red-400 text-xs font-semibold uppercase">Risks</span>
            </div>
            <ul className="space-y-1">
              {b.risks.map((r, i) => (
                <li key={`r-${i}`} className="text-slate-300 text-xs flex items-start gap-1.5">
                  <ChevronRight className="w-3 h-3 text-red-500 mt-0.5 shrink-0" />{r}
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>

      {b.action && (
        <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-1">
            <Shield className="w-4 h-4 text-[#0052FF]" />
            <span className="text-[#0052FF] text-xs font-semibold uppercase">Suggested Action</span>
          </div>
          <p className="text-slate-200 text-sm">{b.action}</p>
        </Card>
      )}
    </div>
  );
};

export default BriefView;
