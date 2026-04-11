import React from 'react';
import { BarChart3 } from 'lucide-react';
import { Card } from '../ui/card';
import { Badge } from '../ui/badge';

const PatternsView = ({ data }) => {
  const a = data.analysis;
  const dirColors = { bullish: 'text-lime-400', bearish: 'text-orange-400', neutral: 'text-amber-300' };
  const statusStyles = {
    forming: 'bg-amber-900/30 text-amber-300 border-amber-700/40',
    confirmed: 'bg-lime-700 text-lime-400 border-emerald-700/40',
    breaking_out: 'bg-cyan-900/30 text-cyan-400 border-cyan-700/40',
    failed: 'bg-orange-800 text-orange-400 border-red-700/40',
  };

  return (
    <div className="space-y-4" data-testid="patterns-results">
      <Card className="bg-gradient-to-r from-cyan-950/40 to-blue-950/40 border-cyan-800/40 rounded-xl p-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <BarChart3 className="w-6 h-6 text-cyan-400" />
            <div>
              <h3 className="text-white text-lg font-bold">{data.symbol} — Pattern Analysis</h3>
              <p className="text-slate-300 text-xs">{a.patterns?.length || 0} patterns detected</p>
            </div>
          </div>
          <Badge className={`text-sm font-bold px-3 py-1 capitalize ${dirColors[a.overall_bias] || 'text-slate-300'} bg-slate-800/60 border-slate-400/30`} data-testid="pattern-bias">
            {a.overall_bias} Bias
          </Badge>
        </div>
        {a.volume_analysis && <p className="text-slate-300 text-xs mt-3">{a.volume_analysis}</p>}
      </Card>

      {a.key_levels && (
        <div className="grid grid-cols-2 gap-3">
          <Card className="bg-red-500/15 border-orange-700/25 rounded-xl p-4">
            <p className="text-orange-400 text-[10px] uppercase tracking-wider mb-2 font-medium">Resistance</p>
            <div className="flex gap-2 flex-wrap">
              {(a.key_levels.resistance || []).map((l, i) => (
                <Badge key={`r-${i}`} className="bg-orange-800 text-orange-300 border-red-700/40 text-sm font-mono">${l}</Badge>
              ))}
            </div>
          </Card>
          <Card className="bg-green-500/20 border-lime-700/25 rounded-xl p-4">
            <p className="text-lime-400 text-[10px] uppercase tracking-wider mb-2 font-medium">Support</p>
            <div className="flex gap-2 flex-wrap">
              {(a.key_levels.support || []).map((l, i) => (
                <Badge key={`s-${i}`} className="bg-lime-700 text-lime-300 border-emerald-700/40 text-sm font-mono">${l}</Badge>
              ))}
            </div>
          </Card>
        </div>
      )}

      {a.patterns?.length > 0 ? (
        <div className="space-y-3">
          {a.patterns.map((p, i) => (
            <Card key={`p-${i}`} className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-4" data-testid={`pattern-card-${i}`}>
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <span className={`text-sm font-bold ${dirColors[p.direction] || 'text-slate-300'}`}>{p.name}</span>
                  <Badge className={`text-[9px] border ${statusStyles[p.status] || 'bg-slate-700 text-slate-400'}`}>{p.status?.replace('_', ' ')}</Badge>
                  <Badge className="bg-slate-700/60 text-slate-400 text-[9px] capitalize">{p.type}</Badge>
                </div>
                <ConfidenceMeter confidence={p.confidence} />
              </div>
              <p className="text-slate-300 text-xs mb-2">{p.description}</p>
              <div className="flex gap-4 text-[10px]">
                {p.price_target && <span className="text-lime-400">Target: ${p.price_target}</span>}
                {p.stop_level && <span className="text-orange-400">Stop: ${p.stop_level}</span>}
                {p.timeframe && <span className="text-slate-400">Timeframe: {p.timeframe}</span>}
              </div>
            </Card>
          ))}
        </div>
      ) : (
        <div className="text-center py-8">
          <p className="text-slate-300 text-sm">No clear patterns detected at this time.</p>
        </div>
      )}
    </div>
  );
};

const ConfidenceMeter = ({ confidence }) => {
  const color = confidence >= 70 ? 'text-lime-400' : confidence >= 40 ? 'text-amber-300' : 'text-orange-400';
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1.5 bg-slate-700 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${confidence >= 70 ? 'bg-green-500' : confidence >= 40 ? 'bg-amber-500' : 'bg-red-500'}`} style={{ width: `${confidence}%` }} />
      </div>
      <span className={`text-[10px] font-bold ${color}`}>{confidence}%</span>
    </div>
  );
};

export default PatternsView;
