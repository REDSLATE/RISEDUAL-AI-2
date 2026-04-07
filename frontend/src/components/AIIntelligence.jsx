import React, { useState } from 'react';
import { Brain, Target, BarChart3, Zap, TrendingUp, TrendingDown, Shield, AlertTriangle, ChevronRight, RefreshCw, Search, ArrowUp, ArrowDown, Minus } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { useAuth, authFetch } from '../contexts/AuthContext';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const TABS = [
  { id: 'score', label: 'AI Score', icon: Target, color: 'from-violet-600 to-purple-600' },
  { id: 'patterns', label: 'Patterns', icon: BarChart3, color: 'from-cyan-600 to-blue-600' },
  { id: 'brief', label: 'Quick Brief', icon: Zap, color: 'from-amber-600 to-orange-600' },
];

const AIIntelligence = ({ onSubscribe }) => {
  const { isPro } = useAuth();
  const [tab, setTab] = useState('score');
  const [symbol, setSymbol] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const [activeSymbol, setActiveSymbol] = useState('');

  const analyze = async () => {
    if (!symbol.trim()) return;
    setLoading(true);
    setError('');
    setResult(null);
    const endpoint = { score: 'score', patterns: 'patterns', brief: 'brief' }[tab];
    try {
      const res = await authFetch(`${API}/intelligence/${endpoint}/${symbol.trim().toUpperCase()}`);
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || 'Analysis failed');
      }
      const data = await res.json();
      setResult(data);
      setActiveSymbol(symbol.trim().toUpperCase());
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e) => { if (e.key === 'Enter') analyze(); };

  return (
    <div data-testid="ai-intelligence">
      <div className="flex items-center gap-3 mb-5">
        <div className="w-10 h-10 bg-gradient-to-br from-violet-600 to-blue-600 rounded-xl flex items-center justify-center">
          <Brain className="w-6 h-6 text-white" />
        </div>
        <div>
          <h2 className="text-white text-xl sm:text-2xl font-bold" style={{ fontFamily: 'Manrope, sans-serif' }}>AI Intelligence Hub</h2>
          <p className="text-slate-400 text-xs">AI-powered stock scoring, pattern detection, and instant briefs</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 mb-4">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => { setTab(t.id); setResult(null); setError(''); }}
            className={`flex items-center gap-1.5 px-4 py-2 rounded-xl text-sm font-medium transition-all ${tab === t.id ? `bg-gradient-to-r ${t.color} text-white shadow-lg` : 'bg-slate-800/60 text-slate-400 hover:text-white border border-slate-700/50'}`}
            data-testid={`tab-${t.id}`}
          >
            <t.icon className="w-4 h-4" />
            {t.label}
          </button>
        ))}
      </div>

      {/* Search Bar */}
      <div className="flex gap-2 mb-5">
        <div className="relative flex-1">
          <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-500 w-4 h-4" />
          <input
            value={symbol}
            onChange={e => setSymbol(e.target.value.toUpperCase())}
            onKeyDown={handleKeyDown}
            placeholder="Enter ticker (AAPL, TSLA, SPY...)"
            className="w-full bg-slate-800/80 border border-slate-700/60 rounded-xl pl-10 pr-4 py-3 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-violet-500/60"
            data-testid="intelligence-symbol-input"
          />
        </div>
        <Button
          onClick={analyze}
          disabled={loading || !symbol.trim()}
          className={`bg-gradient-to-r ${TABS.find(t => t.id === tab)?.color} text-white rounded-xl px-6`}
          data-testid="intelligence-analyze-btn"
        >
          {loading ? <RefreshCw className="w-4 h-4 animate-spin" /> : 'Analyze'}
        </Button>
      </div>

      {error && <div className="bg-red-900/30 border border-red-800/50 text-red-400 text-sm p-3 rounded-lg mb-4">{error}</div>}

      {loading && (
        <div className="text-center py-12">
          <div className="w-10 h-10 border-2 border-violet-500 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
          <p className="text-slate-400 text-sm">Analyzing {symbol.toUpperCase()}...</p>
          <p className="text-slate-600 text-xs mt-1">Crunching technicals + AI inference</p>
        </div>
      )}

      {!loading && result && tab === 'score' && <ScoreView data={result} />}
      {!loading && result && tab === 'patterns' && <PatternsView data={result} />}
      {!loading && result && tab === 'brief' && <BriefView data={result} />}

      {!loading && !result && !error && (
        <div className="text-center py-10">
          <Brain className="w-10 h-10 text-slate-700 mx-auto mb-3" />
          <p className="text-slate-500 text-sm">Enter a ticker symbol and click Analyze</p>
        </div>
      )}
    </div>
  );
};


// ═══════════════════════════════════
// AI SCORE VIEW
// ═══════════════════════════════════
const ScoreView = ({ data }) => {
  const s = data.scores;
  const recColors = { buy: 'bg-emerald-500', hold: 'bg-amber-500', sell: 'bg-red-500' };

  return (
    <div className="space-y-4" data-testid="score-results">
      {/* Main Score */}
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
              <p className="text-slate-500 text-[10px] uppercase">Target Range</p>
              <p className="text-white text-lg font-bold">${s.target_range.low} — ${s.target_range.high}</p>
              <p className="text-slate-500 text-[10px]">{s.time_horizon}</p>
            </div>
          )}
        </div>
      </Card>

      {/* Score Breakdown */}
      <div className="grid grid-cols-3 gap-3">
        <ScoreBar label="Technical" score={s.technical_score} color="violet" />
        <ScoreBar label="Fundamental" score={s.fundamental_score} color="blue" />
        <ScoreBar label="Sentiment" score={s.sentiment_score} color="amber" />
      </div>

      {/* Factors */}
      {s.factors?.length > 0 && (
        <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-4">
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
    violet: { bg: 'bg-violet-500', track: 'bg-violet-900/30', text: 'text-violet-400' },
    blue: { bg: 'bg-blue-500', track: 'bg-blue-900/30', text: 'text-blue-400' },
    amber: { bg: 'bg-amber-500', track: 'bg-amber-900/30', text: 'text-amber-400' },
  };
  const c = colors[color];
  return (
    <Card className="bg-slate-800/60 border-slate-700/40 rounded-xl p-3">
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
  if (impact === 'positive') return <ArrowUp className="w-4 h-4 text-emerald-400 mt-0.5 shrink-0" />;
  if (impact === 'negative') return <ArrowDown className="w-4 h-4 text-red-400 mt-0.5 shrink-0" />;
  return <Minus className="w-4 h-4 text-amber-400 mt-0.5 shrink-0" />;
};


// ═══════════════════════════════════
// PATTERN RECOGNITION VIEW
// ═══════════════════════════════════
const PatternsView = ({ data }) => {
  const a = data.analysis;
  const dirColors = { bullish: 'text-emerald-400', bearish: 'text-red-400', neutral: 'text-amber-400' };
  const statusStyles = {
    forming: 'bg-amber-900/30 text-amber-400 border-amber-700/40',
    confirmed: 'bg-emerald-900/30 text-emerald-400 border-emerald-700/40',
    breaking_out: 'bg-cyan-900/30 text-cyan-400 border-cyan-700/40',
    failed: 'bg-red-900/30 text-red-400 border-red-700/40',
  };

  return (
    <div className="space-y-4" data-testid="patterns-results">
      {/* Overall Bias */}
      <Card className="bg-gradient-to-r from-cyan-950/40 to-blue-950/40 border-cyan-800/40 rounded-xl p-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <BarChart3 className="w-6 h-6 text-cyan-400" />
            <div>
              <h3 className="text-white text-lg font-bold">{data.symbol} — Pattern Analysis</h3>
              <p className="text-slate-400 text-xs">{a.patterns?.length || 0} patterns detected</p>
            </div>
          </div>
          <Badge className={`text-sm font-bold px-3 py-1 capitalize ${dirColors[a.overall_bias] || 'text-slate-300'} bg-slate-800/60 border-slate-700`} data-testid="pattern-bias">
            {a.overall_bias} Bias
          </Badge>
        </div>
        {a.volume_analysis && <p className="text-slate-400 text-xs mt-3">{a.volume_analysis}</p>}
      </Card>

      {/* Key Levels */}
      {a.key_levels && (
        <div className="grid grid-cols-2 gap-3">
          <Card className="bg-red-950/15 border-red-800/25 rounded-xl p-4">
            <p className="text-red-400 text-[10px] uppercase tracking-wider mb-2 font-medium">Resistance</p>
            <div className="flex gap-2 flex-wrap">
              {(a.key_levels.resistance || []).map((l, i) => (
                <Badge key={`r-${i}`} className="bg-red-900/30 text-red-300 border-red-700/40 text-sm font-mono">${l}</Badge>
              ))}
            </div>
          </Card>
          <Card className="bg-emerald-950/15 border-emerald-800/25 rounded-xl p-4">
            <p className="text-emerald-400 text-[10px] uppercase tracking-wider mb-2 font-medium">Support</p>
            <div className="flex gap-2 flex-wrap">
              {(a.key_levels.support || []).map((l, i) => (
                <Badge key={`s-${i}`} className="bg-emerald-900/30 text-emerald-300 border-emerald-700/40 text-sm font-mono">${l}</Badge>
              ))}
            </div>
          </Card>
        </div>
      )}

      {/* Pattern Cards */}
      {a.patterns?.length > 0 ? (
        <div className="space-y-3">
          {a.patterns.map((p, i) => (
            <Card key={`p-${i}`} className="bg-slate-800/60 border-slate-700/40 rounded-xl p-4" data-testid={`pattern-card-${i}`}>
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
                {p.price_target && <span className="text-emerald-400">Target: ${p.price_target}</span>}
                {p.stop_level && <span className="text-red-400">Stop: ${p.stop_level}</span>}
                {p.timeframe && <span className="text-slate-500">Timeframe: {p.timeframe}</span>}
              </div>
            </Card>
          ))}
        </div>
      ) : (
        <div className="text-center py-8">
          <p className="text-slate-500 text-sm">No clear patterns detected at this time.</p>
        </div>
      )}
    </div>
  );
};

const ConfidenceMeter = ({ confidence }) => {
  const color = confidence >= 70 ? 'text-emerald-400' : confidence >= 40 ? 'text-amber-400' : 'text-red-400';
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1.5 bg-slate-700 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${confidence >= 70 ? 'bg-emerald-500' : confidence >= 40 ? 'bg-amber-500' : 'bg-red-500'}`} style={{ width: `${confidence}%` }} />
      </div>
      <span className={`text-[10px] font-bold ${color}`}>{confidence}%</span>
    </div>
  );
};


// ═══════════════════════════════════
// QUICK BRIEF VIEW
// ═══════════════════════════════════
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
      {/* Headline Card */}
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

      {/* Key Metrics */}
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

      {/* Performance */}
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

      {/* Catalysts & Risks */}
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

      {/* Action */}
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

export default AIIntelligence;
