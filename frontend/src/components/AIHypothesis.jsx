import React, { useState, useEffect } from 'react';
import { Search, TrendingUp, TrendingDown, Minus, Lock, Shield, BarChart3, Globe, Landmark, Zap, Sparkles } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { useAuth, authFetch } from '../contexts/AuthContext';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const AIHypothesis = ({ onSubscribe, onLogin }) => {
  const { user, isPro } = useAuth();
  const [symbol, setSymbol] = useState('');
  const [hypothesis, setHypothesis] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const search = async (e) => {
    e?.preventDefault();
    if (!symbol.trim()) return;
    setLoading(true);
    setError('');
    setHypothesis(null);
    try {
      const res = await authFetch(`${API}/hypothesis/${symbol.trim().toUpperCase()}`);
      if (!res.ok) throw new Error('Failed to generate hypothesis');
      setHypothesis(await res.json());
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const VerdictIcon = ({ verdict }) => {
    if (verdict === 'BUY') return <TrendingUp className="w-6 h-6 text-emerald-400" />;
    if (verdict === 'SELL') return <TrendingDown className="w-6 h-6 text-red-400" />;
    return <Minus className="w-6 h-6 text-amber-400" />;
  };

  const verdictColor = (v) => {
    if (v === 'BUY') return 'text-emerald-400 bg-emerald-900/30 border-emerald-700/50';
    if (v === 'SELL') return 'text-red-400 bg-red-900/30 border-red-700/50';
    return 'text-amber-400 bg-amber-900/30 border-amber-700/50';
  };

  return (
    <div className="space-y-6" data-testid="ai-hypothesis">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-gradient-to-br from-[#0052FF] to-cyan-500 rounded-xl flex items-center justify-center">
            <Sparkles className="w-6 h-6 text-white" />
          </div>
          <div>
            <h2 className="text-white text-xl sm:text-2xl font-bold" style={{fontFamily: 'Manrope, sans-serif'}}>AI Investment Hypothesis</h2>
            <p className="text-slate-400 text-xs sm:text-sm">Per-ticker analysis using all scraped macro data</p>
          </div>
        </div>
        {isPro && <Badge className="bg-gradient-to-r from-[#0052FF] to-cyan-500 text-white border-0">PRO</Badge>}
      </div>

      {/* Search */}
      <form onSubmit={search} className="flex gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
          <Input
            placeholder="Enter ticker (AAPL, BTC, TSLA...)"
            value={symbol}
            onChange={e => setSymbol(e.target.value.toUpperCase())}
            className="pl-10 bg-slate-800 border-slate-600 text-white rounded-xl"
            data-testid="hypothesis-search"
          />
        </div>
        <Button type="submit" disabled={loading || !symbol.trim()} className="bg-[#0052FF] hover:bg-[#2563EB] text-white rounded-xl px-6" data-testid="hypothesis-submit">
          {loading ? 'Analyzing...' : 'Analyze'}
        </Button>
      </form>

      {/* Loading */}
      {loading && (
        <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-8 text-center">
          <div className="animate-pulse space-y-3">
            <Sparkles className="w-8 h-8 text-[#0052FF] mx-auto animate-spin" />
            <p className="text-white font-medium">Generating AI Hypothesis for {symbol.toUpperCase()}...</p>
            <p className="text-slate-400 text-sm">Analyzing news, world events, congressional trades, and market data</p>
          </div>
        </Card>
      )}

      {error && <div className="bg-red-900/30 border border-red-800/50 text-red-400 text-sm p-3 rounded-lg">{error}</div>}

      {/* Locked State (Free User) */}
      {hypothesis && !hypothesis.is_pro && (
        <Card className="relative bg-slate-800/50 border-slate-700/40 rounded-xl overflow-hidden" data-testid="hypothesis-locked">
          {/* Teaser Stats */}
          <div className="p-6 space-y-4">
            <div className="flex items-center gap-2 text-white font-semibold text-lg">
              <Sparkles className="w-5 h-5 text-[#0052FF]" />
              AI Hypothesis Ready for {hypothesis.symbol}
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div className="bg-slate-900/60 rounded-lg p-3 text-center">
                <BarChart3 className="w-4 h-4 text-blue-400 mx-auto mb-1" />
                <p className="text-white text-lg font-bold">{hypothesis.teaser.data_sources_count}</p>
                <p className="text-slate-500 text-[10px]">Data Points</p>
              </div>
              <div className="bg-slate-900/60 rounded-lg p-3 text-center">
                <Globe className="w-4 h-4 text-emerald-400 mx-auto mb-1" />
                <p className="text-white text-lg font-bold">{hypothesis.teaser.world_events_count}</p>
                <p className="text-slate-500 text-[10px]">World Events</p>
              </div>
              <div className="bg-slate-900/60 rounded-lg p-3 text-center">
                <Landmark className="w-4 h-4 text-violet-400 mx-auto mb-1" />
                <p className="text-white text-lg font-bold">{hypothesis.teaser.congressional_trades_count}</p>
                <p className="text-slate-500 text-[10px]">Congress Trades</p>
              </div>
            </div>
          </div>

          {/* Blurred Preview */}
          <div className="relative px-6 pb-6">
            <div className="blur-md select-none pointer-events-none" aria-hidden="true">
              <div className="space-y-3">
                <div className="flex items-center gap-3">
                  <span className="text-3xl font-bold text-emerald-400">BUY</span>
                  <span className="text-slate-400">|</span>
                  <span className="text-white text-xl font-semibold">Confidence: 78%</span>
                </div>
                <p className="text-slate-300 text-sm">Based on analysis of 19 news articles, 30 world events, 12 congressional trades, and foreign market correlations, our AI recommends a strong position in this ticker. Key catalysts include sector momentum from technology adoption and favorable insider trading patterns...</p>
                <div className="flex gap-2">
                  <Badge className="bg-emerald-900/40 text-emerald-400">Price Target: $XXX</Badge>
                  <Badge className="bg-blue-900/40 text-blue-400">Upside: XX%</Badge>
                </div>
              </div>
            </div>

            {/* Overlay */}
            <div className="absolute inset-0 flex flex-col items-center justify-center bg-slate-900/50 backdrop-blur-sm rounded-b-xl">
              <Lock className="w-8 h-8 text-[#0052FF] mb-3" />
              <p className="text-white font-semibold text-lg mb-1">Unlock Full AI Hypothesis</p>
              <p className="text-slate-400 text-sm text-center max-w-xs mb-4">{hypothesis.teaser.summary}</p>
              <div className="flex gap-3">
                {!user ? (
                  <Button onClick={onLogin} className="bg-slate-700 hover:bg-slate-600 text-white rounded-xl" data-testid="hypothesis-login-btn">
                    Log In
                  </Button>
                ) : null}
                <Button onClick={onSubscribe} className="bg-[#0052FF] hover:bg-[#2563EB] text-white rounded-xl" data-testid="hypothesis-subscribe-btn">
                  <Zap className="w-4 h-4 mr-2" /> Subscribe to Pro
                </Button>
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* Full Hypothesis (Pro User) */}
      {hypothesis && hypothesis.is_pro && (
        <div className="space-y-5" data-testid="hypothesis-full">
          {/* Verdict Card */}
          <Card className={`border-2 rounded-xl p-6 ${verdictColor(hypothesis.verdict)}`}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <VerdictIcon verdict={hypothesis.verdict} />
                <div>
                  <div className="text-3xl font-black">{hypothesis.verdict}</div>
                  <div className="text-sm opacity-70">{hypothesis.symbol}</div>
                </div>
              </div>
              <div className="text-right">
                <div className="text-2xl font-bold">{hypothesis.confidence}%</div>
                <div className="text-sm opacity-70">Confidence</div>
              </div>
            </div>
            {hypothesis.summary && (
              <p className="mt-4 text-sm opacity-90">{hypothesis.summary}</p>
            )}
          </Card>

          {/* Price Targets */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-5">
              <h3 className="text-slate-400 text-xs font-medium uppercase mb-2">Short-Term Target (1-2 weeks)</h3>
              <p className="text-white text-lg font-bold">{hypothesis.price_target_short || 'N/A'}</p>
            </Card>
            <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-5">
              <h3 className="text-slate-400 text-xs font-medium uppercase mb-2">Medium-Term Target (1-3 months)</h3>
              <p className="text-white text-lg font-bold">{hypothesis.price_target_medium || 'N/A'}</p>
            </Card>
          </div>

          {/* Thesis */}
          {hypothesis.thesis && (
            <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-5">
              <h3 className="text-white font-semibold mb-3">Investment Thesis</h3>
              <p className="text-slate-300 text-sm leading-relaxed whitespace-pre-line">{hypothesis.thesis}</p>
            </Card>
          )}

          {/* Catalysts & Risks */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {hypothesis.catalysts?.length > 0 && (
              <Card className="bg-emerald-950/20 border-emerald-800/30 rounded-xl p-5">
                <h3 className="text-emerald-400 font-semibold mb-3 flex items-center gap-2">
                  <TrendingUp className="w-4 h-4" /> Key Catalysts
                </h3>
                <ul className="space-y-2">
                  {hypothesis.catalysts.map((c, i) => (
                    <li key={i} className="text-slate-300 text-sm flex items-start gap-2">
                      <span className="text-emerald-500 mt-1">+</span> {c}
                    </li>
                  ))}
                </ul>
              </Card>
            )}
            {hypothesis.risks?.length > 0 && (
              <Card className="bg-red-950/20 border-red-800/30 rounded-xl p-5">
                <h3 className="text-red-400 font-semibold mb-3 flex items-center gap-2">
                  <TrendingDown className="w-4 h-4" /> Key Risks
                </h3>
                <ul className="space-y-2">
                  {hypothesis.risks.map((r, i) => (
                    <li key={i} className="text-slate-300 text-sm flex items-start gap-2">
                      <span className="text-red-500 mt-1">-</span> {r}
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>

          {/* Congressional Activity & Sector Impact */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {hypothesis.congressional_activity && (
              <Card className="bg-violet-950/20 border-violet-800/30 rounded-xl p-5">
                <h3 className="text-violet-400 font-semibold mb-3 flex items-center gap-2">
                  <Landmark className="w-4 h-4" /> Congressional Activity
                </h3>
                <p className="text-slate-300 text-sm">{hypothesis.congressional_activity}</p>
              </Card>
            )}
            {hypothesis.sector_impact && (
              <Card className="bg-blue-950/20 border-blue-800/30 rounded-xl p-5">
                <h3 className="text-blue-400 font-semibold mb-3 flex items-center gap-2">
                  <Globe className="w-4 h-4" /> Sector & Macro Impact
                </h3>
                <p className="text-slate-300 text-sm">{hypothesis.sector_impact}</p>
              </Card>
            )}
          </div>

          {/* Technical Outlook */}
          {hypothesis.technical_outlook && (
            <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-5">
              <h3 className="text-white font-semibold mb-3 flex items-center gap-2">
                <BarChart3 className="w-4 h-4 text-[#0052FF]" /> Technical Outlook
              </h3>
              <p className="text-slate-300 text-sm">{hypothesis.technical_outlook}</p>
            </Card>
          )}
        </div>
      )}
    </div>
  );
};

export default AIHypothesis;
