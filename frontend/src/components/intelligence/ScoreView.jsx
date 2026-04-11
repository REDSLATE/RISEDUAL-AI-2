import React from 'react';
import { ArrowUp, ArrowDown, Minus } from 'lucide-react';
import { Card } from '../ui/card';
import { Badge } from '../ui/badge';

const ScoreView = ({ data }) => {
  const s = data.scores;
  const recColors = { buy: 'bg-green-500', hold: 'bg-amber-500', sell: 'bg-red-500' };

  return (
    <div className="space-y-4" data-testid="score-results">
      <Card className="bg-gradient-to-br from-violet-950/40 to-purple-950/40 border-violet-800/40 rounded-xl p-5">
        <div className="flex items-center justify-between flex-wrap gap-4">
          <div className="flex items-center gap-5">
            <div className="relative">
              <div className={`w-20 h-20 rounded-full flex items-center justify-center border-4 ${s.overall_score >= 7 ? 'border-emerald-500' : s.overall_score >= 4 ? 'border-amber-500' : 'border-red-500'}`}>
                <span className="text-white text-3xl font-black">{s.overall_score}</span>
              </div>
              <span className="absolute -bottom-1 left-1/2 -translate-x-1/2 text-[9px] text-slate-400">/10</span>
            </div>
            <div>
              <h3 className="text-white text-xl font-bold">{data.symbol}</h3>
              <p className="text-slate-300 text-sm">{s.summary}</p>
              <div className="flex items-center gap-2 mt-2">
                <Badge className={`${recColors[s.recommendation] || 'bg-slate-600'} text-white text-xs px-3 py-0.5 uppercase font-bold`} data-testid="score-recommendation">
                  {s.recommendation}
                </Badge>
                <Badge className="bg-slate-700/60 text-slate-300 border-slate-600 text-[10px]">
                  {s.confidence} confidence
                </Badge>
                <Badge className="bg-slate-700/60 text-slate-300 border-slate-600 text-[10px]">
                  Risk: {s.risk_level}
                </Badge>
              </div>
            </div>
          </div>
          {s.target_range && (
            <div className="text-right">
              <p className="text-slate-400 text-[10px] uppercase">Target Range</p>
              <p className="text-white text-lg font-bold">${s.target_range.low} — ${s.target_range.high}</p>
              <p className="text-slate-400 text-[10px]">{s.time_horizon}</p>
            </div>
          )}
        </div>
      </Card>

      <div className="grid grid-cols-3 gap-3">
        <ScoreBar label="Technical" score={s.technical_score} color="violet" />
        <ScoreBar label="Fundamental" score={s.fundamental_score} color="blue" />
        <ScoreBar label="Sentiment" score={s.sentiment_score} color="amber" />
      </div>

      {s.factors?.length > 0 && (
        <Card className="bg-slate-700/60 border-slate-400/30/40 rounded-xl p-4">
          <h4 className="text-white text-sm font-semibold mb-3">Key Factors</h4>
          <div className="space-y-2">
            {s.factors.map((f, i) => (
              <div key={`f-${i}`} className="flex items-start gap-2">
                <ImpactIcon impact={f.impact} />
                <div className="flex-1">
                  <p className="text-slate-200 text-sm">{f.factor}</p>
                  <Badge className="bg-slate-700/60 text-slate-400 text-[8px] mt-0.5">Weight: {f.weight}</Badge>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
};

const ScoreBar = ({ label, score, color }) => {
  const colors = {
    violet: { bg: 'bg-violet-500', track: 'bg-violet-900/30', text: 'text-violet-300' },
    blue: { bg: 'bg-blue-500', track: 'bg-blue-900/30', text: 'text-blue-400' },
    amber: { bg: 'bg-amber-500', track: 'bg-amber-900/30', text: 'text-amber-300' },
  };
  const c = colors[color];
  return (
    <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-slate-400 text-[10px] uppercase tracking-wider">{label}</span>
        <span className={`text-lg font-bold ${c.text}`}>{score}</span>
      </div>
      <div className={`w-full h-2 rounded-full ${c.track}`}>
        <div className={`h-2 rounded-full ${c.bg} transition-all`} style={{ width: `${score * 10}%` }} />
      </div>
    </Card>
  );
};

const ImpactIcon = ({ impact }) => {
  if (impact === 'positive') return <ArrowUp className="w-4 h-4 text-lime-400 mt-0.5 shrink-0" />;
  if (impact === 'negative') return <ArrowDown className="w-4 h-4 text-orange-400 mt-0.5 shrink-0" />;
  return <Minus className="w-4 h-4 text-amber-300 mt-0.5 shrink-0" />;
};

export default ScoreView;
