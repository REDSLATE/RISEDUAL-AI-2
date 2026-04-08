import React, { useState } from 'react';
import {
  Search, Shield, TrendingUp, TrendingDown, BarChart3, Brain,
  Target, Users, Zap, Activity, ChevronRight, Building2,
  DollarSign, Loader2, AlertCircle, Lock
} from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { useAuth, authFetch } from '../contexts/AuthContext';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const VERDICT_COLORS = {
  'STRONG BUY': { bg: 'bg-emerald-900/40', text: 'text-emerald-400', border: 'border-emerald-700/50' },
  'BUY': { bg: 'bg-emerald-900/30', text: 'text-emerald-400', border: 'border-emerald-700/40' },
  'HOLD': { bg: 'bg-amber-900/30', text: 'text-amber-400', border: 'border-amber-700/40' },
  'SELL': { bg: 'bg-red-900/30', text: 'text-red-400', border: 'border-red-700/40' },
  'STRONG SELL': { bg: 'bg-red-900/40', text: 'text-red-400', border: 'border-red-700/50' },
};

const ScoreGauge = ({ score, label }) => {
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

const MiniBar = ({ value, max = 100, color = '#0052FF' }) => (
  <div className="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden">
    <div className="h-full rounded-full transition-all duration-700" style={{ width: `${Math.min((value / max) * 100, 100)}%`, backgroundColor: color }} />
  </div>
);

const AIWarRoom = ({ onSubscribe, onLogin }) => {
  const { user, isPro } = useAuth();
  const [symbol, setSymbol] = useState('');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const analyze = async (e) => {
    e?.preventDefault();
    if (!symbol.trim()) return;
    setLoading(true);
    setError('');
    setData(null);
    try {
      const res = await authFetch(`${API}/intelligence/war-room/${symbol.trim().toUpperCase()}`);
      if (res.status === 403) {
        setError('pro_required');
        return;
      }
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || 'Analysis failed');
      }
      setData(await res.json());
    } catch (err) {
      if (error !== 'pro_required') setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const v = data?.composite?.verdict || 'HOLD';
  const vc = VERDICT_COLORS[v] || VERDICT_COLORS['HOLD'];

  return (
    <div className="space-y-6" data-testid="ai-war-room">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-gradient-to-br from-red-600 to-amber-500 rounded-xl flex items-center justify-center">
            <Shield className="w-6 h-6 text-white" />
          </div>
          <div>
            <h2 className="text-white text-xl sm:text-2xl font-bold" style={{ fontFamily: 'Manrope, sans-serif' }}>
              AI War Room
            </h2>
            <p className="text-slate-400 text-xs sm:text-sm">Unified command center — AI Score + Earnings + Insider Intelligence</p>
          </div>
        </div>
        {isPro && <Badge className="bg-gradient-to-r from-red-600 to-amber-500 text-white border-0">PRO</Badge>}
      </div>

      {/* Search */}
      <form onSubmit={analyze} className="flex gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
          <Input
            placeholder="Enter ticker symbol (AAPL, TSLA, NVDA...)"
            value={symbol}
            onChange={e => setSymbol(e.target.value.toUpperCase())}
            className="pl-10 bg-slate-800 border-slate-600 text-white rounded-xl"
            data-testid="warroom-search"
          />
        </div>
        <Button type="submit" disabled={loading || !symbol.trim()}
          className="bg-gradient-to-r from-red-600 to-amber-500 hover:from-red-500 hover:to-amber-400 text-white rounded-xl px-6"
          data-testid="warroom-submit">
          {loading ? <><Loader2 className="w-4 h-4 mr-2 animate-spin" />Scanning...</> : <>Deploy Analysis</>}
        </Button>
      </form>

      {/* Pro Lock */}
      {error === 'pro_required' && (
        <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-8 text-center">
          <Lock className="w-10 h-10 text-amber-400 mx-auto mb-3" />
          <h3 className="text-white font-bold text-lg mb-2">War Room is Pro Only</h3>
          <p className="text-slate-400 text-sm mb-4">Get full access to AI-powered multi-signal analysis</p>
          <div className="flex gap-3 justify-center">
            {!user && <Button onClick={onLogin} className="bg-slate-700 hover:bg-slate-600 text-white rounded-xl">Log In</Button>}
            <Button onClick={onSubscribe} className="bg-gradient-to-r from-red-600 to-amber-500 text-white rounded-xl">
              <Zap className="w-4 h-4 mr-2" />Subscribe to Pro
            </Button>
          </div>
        </Card>
      )}

      {/* Loading */}
      {loading && (
        <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-8 text-center">
          <div className="space-y-3">
            <Shield className="w-10 h-10 text-amber-400 mx-auto animate-pulse" />
            <p className="text-white font-semibold">Deploying War Room for {symbol}</p>
            <p className="text-slate-400 text-sm">Scanning earnings, insider trades, technicals, and AI models...</p>
            <div className="flex justify-center gap-6 mt-4">
              {['AI Score', 'Earnings', 'Insiders', 'Technicals', 'Brief'].map((s, i) => (
                <div key={s} className="text-center" style={{ animationDelay: `${i * 0.2}s` }}>
                  <div className="w-2 h-2 bg-amber-400 rounded-full mx-auto mb-1 animate-pulse" style={{ animationDelay: `${i * 0.3}s` }} />
                  <span className="text-slate-500 text-[10px]">{s}</span>
                </div>
              ))}
            </div>
          </div>
        </Card>
      )}

      {/* Error */}
      {error && error !== 'pro_required' && (
        <div className="bg-red-900/30 border border-red-800/50 text-red-400 text-sm p-3 rounded-lg flex items-center gap-2">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />{error}
        </div>
      )}

      {/* Results */}
      {data && (
        <div className="space-y-4">
          {/* Top: Composite Signal + Overview */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {/* Composite Verdict */}
            <Card className={`${vc.bg} ${vc.border} border rounded-xl p-5 lg:col-span-1`} data-testid="warroom-verdict">
              <div className="text-center">
                <p className="text-slate-400 text-xs mb-2 uppercase tracking-wider">Composite Signal</p>
                <ScoreGauge score={data.composite.score} label="Conviction" />
                <p className={`text-2xl font-black mt-2 ${vc.text}`}>{v}</p>
                <div className="mt-3 space-y-1.5 text-left">
                  {[
                    { label: 'AI Score', value: data.composite.breakdown.ai_score_weight, color: '#0052FF' },
                    { label: 'Earnings', value: data.composite.breakdown.earnings_weight, color: '#10B981' },
                    { label: 'Insiders', value: data.composite.breakdown.insider_weight, color: '#F59E0B' },
                  ].map(b => (
                    <div key={b.label} className="flex items-center gap-2">
                      <span className="text-slate-500 text-[10px] w-14">{b.label}</span>
                      <MiniBar value={b.value} max={40} color={b.color} />
                      <span className="text-slate-400 text-[10px] w-6">{b.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            </Card>

            {/* Company Overview */}
            <Card className="bg-slate-800/40 border-slate-700/30 rounded-xl p-5 lg:col-span-2" data-testid="warroom-overview">
              <div className="flex items-center gap-2 mb-3">
                <Building2 className="w-4 h-4 text-[#0052FF]" />
                <h3 className="text-white font-semibold text-sm">{data.overview.name || data.symbol}</h3>
                <Badge className="bg-slate-700 text-slate-300 text-[10px]">{data.overview.sector}</Badge>
                <Badge className="bg-slate-700/60 text-slate-400 text-[10px]">{data.overview.industry}</Badge>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                {[
                  { label: 'Market Cap', value: `$${(parseFloat(data.overview.market_cap || 0) / 1e9).toFixed(0)}B`, icon: DollarSign },
                  { label: 'P/E Ratio', value: data.overview.pe_ratio || 'N/A', icon: BarChart3 },
                  { label: 'Beta', value: data.overview.beta || 'N/A', icon: Activity },
                  { label: 'Analyst Target', value: data.overview.analyst_target ? `$${data.overview.analyst_target}` : 'N/A', icon: Target },
                  { label: '52W High', value: data.overview['52_week_high'] ? `$${data.overview['52_week_high']}` : 'N/A', icon: TrendingUp },
                  { label: '52W Low', value: data.overview['52_week_low'] ? `$${data.overview['52_week_low']}` : 'N/A', icon: TrendingDown },
                  { label: 'Profit Margin', value: data.overview.profit_margin ? `${(parseFloat(data.overview.profit_margin) * 100).toFixed(1)}%` : 'N/A', icon: Zap },
                  { label: 'Rev Growth', value: data.overview.revenue_growth ? `${(parseFloat(data.overview.revenue_growth) * 100).toFixed(1)}%` : 'N/A', icon: ChevronRight },
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
          </div>

          {/* Middle: AI Score + Brief */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* AI Score */}
            <Card className="bg-slate-800/40 border-slate-700/30 rounded-xl p-5" data-testid="warroom-ai-score">
              <div className="flex items-center gap-2 mb-3">
                <Brain className="w-4 h-4 text-violet-400" />
                <h3 className="text-white font-semibold text-sm">AI Score</h3>
                <Badge className={`text-[10px] ${data.ai_score.overall_score >= 7 ? 'bg-emerald-900/40 text-emerald-400' : data.ai_score.overall_score >= 4 ? 'bg-amber-900/40 text-amber-400' : 'bg-red-900/40 text-red-400'}`}>
                  {data.ai_score.overall_score}/10
                </Badge>
              </div>
              <div className="space-y-2.5">
                {[
                  { label: 'Technical', score: data.ai_score.technical_score || 0, color: '#0052FF' },
                  { label: 'Fundamental', score: data.ai_score.fundamental_score || 0, color: '#10B981' },
                  { label: 'Sentiment', score: data.ai_score.sentiment_score || 0, color: '#F59E0B' },
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
              {data.ai_score.factors && (
                <div className="mt-3 space-y-1">
                  {data.ai_score.factors.slice(0, 3).map((f, i) => (
                    <div key={i} className="flex items-start gap-2 text-xs">
                      <span className={`mt-0.5 w-1.5 h-1.5 rounded-full flex-shrink-0 ${f.impact === 'positive' ? 'bg-emerald-400' : f.impact === 'caution' ? 'bg-amber-400' : 'bg-slate-500'}`} />
                      <span className="text-slate-400">{f.factor?.substring(0, 100)}</span>
                    </div>
                  ))}
                </div>
              )}
            </Card>

            {/* Quick Brief */}
            <Card className="bg-slate-800/40 border-slate-700/30 rounded-xl p-5" data-testid="warroom-brief">
              <div className="flex items-center gap-2 mb-3">
                <Zap className="w-4 h-4 text-amber-400" />
                <h3 className="text-white font-semibold text-sm">Intelligence Brief</h3>
              </div>
              {data.brief && (
                <div className="space-y-3">
                  <p className="text-white font-medium text-sm">{data.brief.headline || 'No headline'}</p>
                  <p className="text-slate-400 text-xs leading-relaxed">{data.brief.summary || data.brief.analysis || ''}</p>
                  {data.brief.key_levels && (
                    <div className="flex flex-wrap gap-2">
                      {Object.entries(data.brief.key_levels).map(([k, v]) => (
                        <Badge key={k} className="bg-slate-900/60 text-slate-300 text-[10px]">
                          {k}: {typeof v === 'number' ? `$${v}` : v}
                        </Badge>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </Card>
          </div>

          {/* Bottom: Earnings + Insiders */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Earnings Surprise Tracker */}
            <Card className="bg-slate-800/40 border-slate-700/30 rounded-xl p-5" data-testid="warroom-earnings">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Target className="w-4 h-4 text-emerald-400" />
                  <h3 className="text-white font-semibold text-sm">Earnings Surprise Tracker</h3>
                </div>
                <div className="flex items-center gap-2">
                  <Badge className={`text-[10px] ${data.earnings.beat_rate >= 75 ? 'bg-emerald-900/40 text-emerald-400' : data.earnings.beat_rate >= 50 ? 'bg-amber-900/40 text-amber-400' : 'bg-red-900/40 text-red-400'}`}>
                    {data.earnings.beat_rate}% Beat Rate
                  </Badge>
                  {data.earnings.current_streak > 0 && (
                    <Badge className="bg-emerald-900/30 text-emerald-400 text-[10px]">{data.earnings.current_streak}Q Streak</Badge>
                  )}
                </div>
              </div>
              {/* Earnings chart as bars */}
              <div className="flex items-end gap-1.5 h-24 mb-2">
                {(data.earnings.quarters || []).slice(0, 8).reverse().map((q, i) => {
                  const surprise = q.surprise_pct;
                  const h = Math.min(Math.abs(surprise) * 3 + 10, 100);
                  return (
                    <div key={i} className="flex-1 flex flex-col items-center justify-end h-full">
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
                {(data.earnings.quarters || []).slice(0, 8).reverse().map((q, i) => (
                  <span key={i}>{q.date?.slice(2, 7)}</span>
                ))}
              </div>
              <div className="flex items-center gap-3 mt-2 text-[10px]">
                <span className="flex items-center gap-1"><span className="w-2 h-2 bg-emerald-500/70 rounded-sm" /> Beat</span>
                <span className="flex items-center gap-1"><span className="w-2 h-2 bg-red-500/70 rounded-sm" /> Miss</span>
              </div>
            </Card>

            {/* Insider Trade Tracker */}
            <Card className="bg-slate-800/40 border-slate-700/30 rounded-xl p-5" data-testid="warroom-insiders">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Users className="w-4 h-4 text-blue-400" />
                  <h3 className="text-white font-semibold text-sm">Insider Trade Tracker</h3>
                </div>
                <Badge className={`text-[10px] ${data.insiders.net_sentiment === 'bullish' ? 'bg-emerald-900/40 text-emerald-400' : data.insiders.net_sentiment === 'bearish' ? 'bg-red-900/40 text-red-400' : 'bg-slate-700 text-slate-400'}`}>
                  {data.insiders.net_sentiment?.toUpperCase()}
                </Badge>
              </div>
              {/* Buy/Sell ratio bar */}
              <div className="mb-3">
                <div className="flex justify-between text-[10px] mb-1">
                  <span className="text-emerald-400">Buys ({data.insiders.buy_ratio}%)</span>
                  <span className="text-red-400">Sells ({100 - data.insiders.buy_ratio}%)</span>
                </div>
                <div className="w-full h-2.5 bg-red-900/40 rounded-full overflow-hidden">
                  <div className="h-full bg-emerald-500/70 rounded-full transition-all duration-700" style={{ width: `${data.insiders.buy_ratio}%` }} />
                </div>
              </div>
              {/* Recent trades */}
              <div className="space-y-1.5 max-h-36 overflow-y-auto">
                {(data.insiders.trades || []).slice(0, 6).map((t, i) => (
                  <div key={i} className="flex items-center justify-between text-xs bg-slate-900/40 rounded-lg px-2.5 py-1.5">
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
          </div>
        </div>
      )}
    </div>
  );
};

export default AIWarRoom;
