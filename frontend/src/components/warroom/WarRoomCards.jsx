import React from 'react';
import {
  Target, Users, Brain, Zap, Building2,
  DollarSign, BarChart3, Activity, ChevronRight,
  TrendingUp, TrendingDown
} from 'lucide-react';
import { Card } from '../ui/card';
import { Badge } from '../ui/badge';

// Helper: score → badge CSS class (green/amber/red threshold)
const scoreBadge = (val, high = 7, mid = 4) =>
  val >= high ? 'bg-emerald-900/40 text-emerald-400' :
  val >= mid ? 'bg-amber-900/40 text-amber-400' : 'bg-red-900/40 text-red-400';

const IMPACT_DOTS = { positive: 'bg-emerald-400', caution: 'bg-amber-400' };
const impactDot = (impact) => IMPACT_DOTS[impact] || 'bg-slate-500';

const SENTIMENT_BADGES = {
  bullish: 'bg-emerald-900/40 text-emerald-400',
  bearish: 'bg-red-900/40 text-red-400',
};
const sentimentBadge = (s) => SENTIMENT_BADGES[s] || 'bg-slate-700 text-slate-400';

const MiniBar = ({ value, max = 100, color = '#0052FF' }) => (
  <div className="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden">
    <div className="h-full rounded-full transition-all duration-700" style={{ width: `${Math.min((value / max) * 100, 100)}%`, backgroundColor: color }} />
  </div>
);

export const OverviewCard = ({ overview, symbol }) => (
  <Card className="bg-slate-800/40 border-slate-700/30 rounded-xl p-5 lg:col-span-2" data-testid="warroom-overview">
    <div className="flex items-center gap-2 mb-3">
      <Building2 className="w-4 h-4 text-[#0052FF]" />
      <h3 className="text-white font-semibold text-sm">{overview.name || symbol}</h3>
      <Badge className="bg-slate-700 text-slate-300 text-[10px]">{overview.sector}</Badge>
      <Badge className="bg-slate-700/60 text-slate-400 text-[10px]">{overview.industry}</Badge>
    </div>
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
      {[
        { label: 'Market Cap', value: `$${(parseFloat(overview.market_cap || 0) / 1e9).toFixed(0)}B`, icon: DollarSign },
        { label: 'P/E Ratio', value: overview.pe_ratio || 'N/A', icon: BarChart3 },
        { label: 'Beta', value: overview.beta || 'N/A', icon: Activity },
        { label: 'Analyst Target', value: overview.analyst_target ? `$${overview.analyst_target}` : 'N/A', icon: Target },
        { label: '52W High', value: overview['52_week_high'] ? `$${overview['52_week_high']}` : 'N/A', icon: TrendingUp },
        { label: '52W Low', value: overview['52_week_low'] ? `$${overview['52_week_low']}` : 'N/A', icon: TrendingDown },
        { label: 'Profit Margin', value: overview.profit_margin ? `${(parseFloat(overview.profit_margin) * 100).toFixed(1)}%` : 'N/A', icon: Zap },
        { label: 'Rev Growth', value: overview.revenue_growth ? `${(parseFloat(overview.revenue_growth) * 100).toFixed(1)}%` : 'N/A', icon: ChevronRight },
      ].map(({ label, value, icon: Icon }) => (
        <div key={label} className="bg-slate-900/40 rounded-lg p-2.5">
          <div className="flex items-center gap-1 mb-0.5">
            <Icon className="w-3 h-3 text-slate-500" />
            <span className="text-slate-500 text-[10px]">{label}</span>
          </div>
          <p className="text-white text-sm font-semibold">{value}</p>
        </div>
      ))}
    </div>
  </Card>
);

export const AIScoreCard = ({ aiScore }) => (
  <Card className="bg-slate-800/40 border-slate-700/30 rounded-xl p-5" data-testid="warroom-ai-score">
    <div className="flex items-center gap-2 mb-3">
      <Brain className="w-4 h-4 text-violet-400" />
      <h3 className="text-white font-semibold text-sm">AI Score</h3>
      <Badge className={`text-[10px] ${scoreBadge(aiScore.overall_score)}`}>
        {aiScore.overall_score}/10
      </Badge>
    </div>
    <div className="space-y-2.5">
      {[
        { label: 'Technical', score: aiScore.technical_score || 0, color: '#0052FF' },
        { label: 'Fundamental', score: aiScore.fundamental_score || 0, color: '#10B981' },
        { label: 'Sentiment', score: aiScore.sentiment_score || 0, color: '#F59E0B' },
      ].map(s => (
        <div key={s.label}>
          <div className="flex justify-between text-xs mb-1">
            <span className="text-slate-400">{s.label}</span>
            <span className="text-white font-medium">{s.score}/10</span>
          </div>
          <MiniBar value={s.score} max={10} color={s.color} />
        </div>
      ))}
    </div>
    {aiScore.factors && (
      <div className="mt-3 space-y-1">
        {aiScore.factors.slice(0, 3).map((f) => (
          <div key={f.factor?.substring(0, 30) || Math.random()} className="flex items-start gap-2 text-xs">
            <span className={`mt-0.5 w-1.5 h-1.5 rounded-full flex-shrink-0 ${impactDot(f.impact)}`} />
            <span className="text-slate-400">{f.factor?.substring(0, 100)}</span>
          </div>
        ))}
      </div>
    )}
  </Card>
);

export const BriefCard = ({ brief }) => (
  <Card className="bg-slate-800/40 border-slate-700/30 rounded-xl p-5" data-testid="warroom-brief">
    <div className="flex items-center gap-2 mb-3">
      <Zap className="w-4 h-4 text-amber-400" />
      <h3 className="text-white font-semibold text-sm">Intelligence Brief</h3>
    </div>
    {brief && (
      <div className="space-y-3">
        <p className="text-white font-medium text-sm">{brief.headline || 'No headline'}</p>
        <p className="text-slate-400 text-xs leading-relaxed">{brief.summary || brief.analysis || ''}</p>
        {brief.key_levels && (
          <div className="flex flex-wrap gap-2">
            {Object.entries(brief.key_levels).map(([k, v]) => (
              <Badge key={k} className="bg-slate-900/60 text-slate-300 text-[10px]">
                {k}: {typeof v === 'number' ? `$${v}` : v}
              </Badge>
            ))}
          </div>
        )}
      </div>
    )}
  </Card>
);

export const EarningsCard = ({ earnings }) => (
  <Card className="bg-slate-800/40 border-slate-700/30 rounded-xl p-5" data-testid="warroom-earnings">
    <div className="flex items-center justify-between mb-3">
      <div className="flex items-center gap-2">
        <Target className="w-4 h-4 text-emerald-400" />
        <h3 className="text-white font-semibold text-sm">Earnings Surprise Tracker</h3>
      </div>
      <div className="flex items-center gap-2">
        <Badge className={`text-[10px] ${scoreBadge(earnings.beat_rate, 75, 50)}`}>
          {earnings.beat_rate}% Beat Rate
        </Badge>
        {earnings.current_streak > 0 && (
          <Badge className="bg-emerald-900/30 text-emerald-400 text-[10px]">{earnings.current_streak}Q Streak</Badge>
        )}
      </div>
    </div>
    <div className="flex items-end gap-1.5 h-24 mb-2">
      {(earnings.quarters || []).slice(0, 8).reverse().map((q) => {
        const surprise = q.surprise_pct;
        const h = Math.min(Math.abs(surprise) * 3 + 10, 100);
        return (
          <div key={q.date} className="flex-1 flex flex-col items-center justify-end h-full">
            <div
              className={`w-full rounded-t transition-all ${q.beat ? 'bg-emerald-500/70' : 'bg-red-500/70'}`}
              style={{ height: `${h}%` }}
              title={`${q.date}: ${q.beat ? 'Beat' : 'Miss'} by ${surprise.toFixed(1)}%`}
            />
          </div>
        );
      })}
    </div>
    <div className="flex justify-between text-[9px] text-slate-500 px-1">
      {(earnings.quarters || []).slice(0, 8).reverse().map((q) => (
        <span key={`label-${q.date}`}>{q.date?.slice(2, 7)}</span>
      ))}
    </div>
    <div className="flex items-center gap-3 mt-2 text-[10px]">
      <span className="flex items-center gap-1"><span className="w-2 h-2 bg-emerald-500/70 rounded-sm" /> Beat</span>
      <span className="flex items-center gap-1"><span className="w-2 h-2 bg-red-500/70 rounded-sm" /> Miss</span>
    </div>
  </Card>
);

export const InsidersCard = ({ insiders }) => (
  <Card className="bg-slate-800/40 border-slate-700/30 rounded-xl p-5" data-testid="warroom-insiders">
    <div className="flex items-center justify-between mb-3">
      <div className="flex items-center gap-2">
        <Users className="w-4 h-4 text-blue-400" />
        <h3 className="text-white font-semibold text-sm">Insider Trade Tracker</h3>
      </div>
      <Badge className={`text-[10px] ${sentimentBadge(insiders.net_sentiment)}`}>
        {insiders.net_sentiment?.toUpperCase()}
      </Badge>
    </div>
    <div className="mb-3">
      <div className="flex justify-between text-[10px] mb-1">
        <span className="text-emerald-400">Buys ({insiders.buy_ratio}%)</span>
        <span className="text-red-400">Sells ({100 - insiders.buy_ratio}%)</span>
      </div>
      <div className="w-full h-2.5 bg-red-900/40 rounded-full overflow-hidden">
        <div className="h-full bg-emerald-500/70 rounded-full transition-all duration-700" style={{ width: `${insiders.buy_ratio}%` }} />
      </div>
    </div>
    <div className="space-y-1.5 max-h-36 overflow-y-auto">
      {(insiders.trades || []).slice(0, 6).map((t) => (
        <div key={`${t.name}-${t.date}`} className="flex items-center justify-between text-xs bg-slate-900/40 rounded-lg px-2.5 py-1.5">
          <div className="flex items-center gap-2">
            <span className={`w-1.5 h-1.5 rounded-full ${t.type === 'buy' ? 'bg-emerald-400' : 'bg-red-400'}`} />
            <span className="text-slate-300 truncate max-w-[120px]">{t.name}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-slate-500">{t.shares?.toLocaleString()} shares</span>
            <span className="text-slate-600 text-[10px]">{t.date?.slice(5)}</span>
          </div>
        </div>
      ))}
    </div>
  </Card>
);

export const ScoreGauge = ({ score, label }) => {
  const pct = Math.min(score, 100);
  const color = pct >= 70 ? '#10B981' : pct >= 45 ? '#F59E0B' : '#EF4444';
  const radius = 36;
  const circ = 2 * Math.PI * radius;
  const offset = circ - (pct / 100) * circ;
  return (
    <div className="flex flex-col items-center">
      <svg width="90" height="90" className="-rotate-90">
        <circle cx="45" cy="45" r={radius} fill="none" stroke="#1E293B" strokeWidth="6" />
        <circle cx="45" cy="45" r={radius} fill="none" stroke={color} strokeWidth="6"
          strokeDasharray={circ} strokeDashoffset={offset} strokeLinecap="round"
          className="transition-all duration-1000" />
      </svg>
      <span className="text-white text-xl font-bold -mt-14 mb-6" style={{ color }}>{pct}%</span>
      <span className="text-slate-400 text-[10px]">{label}</span>
    </div>
  );
};

export const CompositeBreakdownBar = ({ breakdown }) => (
  <div className="mt-3 space-y-1.5 text-left">
    {[
      { label: 'AI Score', value: breakdown.ai_score_weight, color: '#0052FF' },
      { label: 'Earnings', value: breakdown.earnings_weight, color: '#10B981' },
      { label: 'Insiders', value: breakdown.insider_weight, color: '#F59E0B' },
    ].map(b => (
      <div key={b.label} className="flex items-center gap-2">
        <span className="text-slate-500 text-[10px] w-14">{b.label}</span>
        <MiniBar value={b.value} max={40} color={b.color} />
        <span className="text-slate-400 text-[10px] w-6">{b.value}</span>
      </div>
    ))}
  </div>
);
