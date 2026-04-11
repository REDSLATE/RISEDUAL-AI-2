import React, { useState, useEffect, useCallback } from 'react';
import { Grid3x3, TrendingUp, TrendingDown, RefreshCw, Brain, Sparkles } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { getApiBase } from '../utils/apiBase';

const BACKEND_URL = getApiBase();

const PERIODS = [
  { key: 'change_1d', label: '1D' },
  { key: 'change_1w', label: '1W' },
  { key: 'change_1m', label: '1M' },
  { key: 'change_3m', label: '3M' },
  { key: 'change_ytd', label: 'YTD' },
  { key: 'ai_sentiment', label: 'AI' },
];

const getHeatColor = (val) => {
  if (val >= 3) return 'bg-emerald-500/90 text-white';
  if (val >= 1.5) return 'bg-emerald-600/70 text-white';
  if (val >= 0.5) return 'bg-emerald-700/50 text-emerald-100';
  if (val >= 0) return 'bg-emerald-900/30 text-emerald-300';
  if (val >= -0.5) return 'bg-red-900/30 text-red-300';
  if (val >= -1.5) return 'bg-red-700/50 text-red-100';
  if (val >= -3) return 'bg-red-600/70 text-white';
  return 'bg-red-500/90 text-white';
};

const getSentimentColor = (val) => {
  if (val >= 75) return 'bg-emerald-500/90 text-white';
  if (val >= 62) return 'bg-emerald-600/70 text-white';
  if (val >= 55) return 'bg-emerald-700/50 text-emerald-100';
  if (val >= 45) return 'bg-slate-600/50 text-slate-200';
  if (val >= 38) return 'bg-orange-800/50 text-orange-200';
  if (val >= 25) return 'bg-red-700/50 text-red-100';
  return 'bg-red-500/90 text-white';
};

const getSentimentLabel = (val) => {
  if (val >= 75) return 'Strong Buy';
  if (val >= 62) return 'Bullish';
  if (val >= 55) return 'Lean Bull';
  if (val >= 45) return 'Neutral';
  if (val >= 38) return 'Cautious';
  if (val >= 25) return 'Bearish';
  return 'Strong Sell';
};

const SectorHeatmap = () => {
  const [data, setData] = useState(null);
  const [sentiment, setSentiment] = useState(null);
  const [history, setHistory] = useState(null);
  const [loading, setLoading] = useState(true);
  const [sentimentLoading, setSentimentLoading] = useState(false);
  const [period, setPeriod] = useState('change_1d');
  const [error, setError] = useState('');

  const fetchData = useCallback(async (force = false) => {
    setLoading(true);
    setError('');
    try {
      const url = force
        ? `${BACKEND_URL}/api/sectors/heatmap?force=true`
        : `${BACKEND_URL}/api/sectors/heatmap`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`Server error (${res.status})`);
      const json = await res.json();
      if (!json.sectors?.length) throw new Error('No sector data returned');
      setData(json);
    } catch (e) {
      setError(e.message || 'Failed to fetch sector data');
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchSentiment = useCallback(async (force = false) => {
    setSentimentLoading(true);
    try {
      const url = force
        ? `${BACKEND_URL}/api/sectors/sentiment?force=true`
        : `${BACKEND_URL}/api/sectors/sentiment`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`Sentiment error (${res.status})`);
      const json = await res.json();
      setSentiment(json);
      // Also fetch history for trend sparklines
      try {
        const hRes = await fetch(`${BACKEND_URL}/api/sectors/sentiment/history?limit=20`);
        if (hRes.ok) setHistory(await hRes.json());
      } catch { /* history is optional */ }
    } catch (e) {
      console.error('Sentiment fetch failed:', e);
    } finally {
      setSentimentLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 120000);
    return () => clearInterval(interval);
  }, [fetchData]);

  // Load sentiment when AI tab is first selected
  useEffect(() => {
    if (period === 'ai_sentiment' && !sentiment && !sentimentLoading) {
      fetchSentiment();
    }
  }, [period, sentiment, sentimentLoading, fetchSentiment]);

  const isAI = period === 'ai_sentiment';

  if (loading && !data) {
    return (
      <Card className="bg-slate-800/40 border-slate-700/30 rounded-2xl p-5" data-testid="sector-heatmap-skeleton">
        <div className="flex items-center gap-3 mb-5">
          <div className="skeleton w-10 h-10 rounded-xl" />
          <div>
            <div className="skeleton w-48 h-5 mb-2" />
            <div className="skeleton w-32 h-3" />
          </div>
        </div>
        <div className="flex gap-2 mb-5">
          {[1,2,3,4,5,6].map(i => <div key={i} className="skeleton w-12 h-7 rounded-lg" />)}
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          {[1,2,3,4,5,6,7,8].map(i => (
            <div key={i} className="skeleton h-24 rounded-xl" />
          ))}
        </div>
      </Card>
    );
  }

  if (error) {
    return (
      <div className="bg-red-900/20 border border-red-800/40 rounded-xl p-4 text-red-400 text-sm">{error}</div>
    );
  }

  const sectors = data?.sectors || [];
  const summary = data?.market_summary || {};
  const sentimentData = sentiment?.sectors || {};
  const sectorTrends = history?.sector_trends || {};

  // Mini SVG sparkline for sentiment trend
  const Sparkline = ({ points, width = 48, height = 16 }) => {
    if (!points || points.length < 2) return null;
    const vals = points.map(p => p.heatmap_value);
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const range = max - min || 1;
    const step = width / (vals.length - 1);
    const pathD = vals.map((v, i) => {
      const x = i * step;
      const y = height - ((v - min) / range) * height;
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
    const trending = vals[vals.length - 1] >= vals[0];
    return (
      <svg width={width} height={height} className="opacity-70">
        <path d={pathD} fill="none" stroke={trending ? '#34d399' : '#f87171'} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  };

  return (
    <div data-testid="sector-heatmap">
      <div className="flex items-center justify-between mb-5 flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-gradient-to-br from-orange-600 to-red-600 rounded-xl flex items-center justify-center">
            <Grid3x3 className="w-6 h-6 text-white" />
          </div>
          <div>
            <h2 className="text-white text-xl sm:text-2xl font-bold" style={{ fontFamily: 'Manrope, sans-serif' }}>Sector Rotation</h2>
            <p className="text-slate-400 text-xs">
              {isAI ? 'Multi-agent AI sentiment analysis' : 'S&P 500 sector ETF performance heatmap'}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {PERIODS.map(p => (
            <button
              key={p.key}
              onClick={() => setPeriod(p.key)}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all flex items-center gap-1 ${
                period === p.key
                  ? p.key === 'ai_sentiment'
                    ? 'bg-purple-600 text-white ring-1 ring-purple-400/50'
                    : 'bg-[#35D6C8] text-white'
                  : 'bg-slate-800/60 text-slate-400 hover:text-white border border-slate-700/50'
              }`}
              data-testid={`period-${p.key}`}
            >
              {p.key === 'ai_sentiment' && <Brain className="w-3 h-3" />}
              {p.label}
            </button>
          ))}
          <button
            onClick={() => isAI ? fetchSentiment(true) : fetchData(true)}
            className="p-1.5 rounded-lg bg-slate-800/60 text-slate-400 hover:text-white border border-slate-700/50 transition-all ml-1"
            title={isAI ? 'Regenerate AI sentiment' : 'Force refresh (bypass cache)'}
            data-testid="refresh-sectors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${(loading || sentimentLoading) ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* AI Sentiment Header Bar */}
      {isAI && sentiment && (
        <div className="mb-4 p-3 rounded-xl bg-purple-950/20 border border-purple-800/30">
          <div className="flex items-center gap-2 mb-1.5">
            <Sparkles className="w-4 h-4 text-purple-400" />
            <span className="text-purple-300 text-xs font-semibold uppercase tracking-wide">Multi-Agent Consensus</span>
            <Badge className="bg-purple-900/50 text-purple-300 border-purple-700/50 text-[10px]">
              {sentiment.agents_used || 4} agents
            </Badge>
            <Badge className={`text-[10px] border-0 ${sentiment.risk_regime === 'risk-on' ? 'bg-emerald-900/50 text-emerald-300' : sentiment.risk_regime === 'risk-off' ? 'bg-red-900/50 text-red-300' : 'bg-slate-700/50 text-slate-300'}`}>
              {sentiment.risk_regime || 'mixed'}
            </Badge>
            {history && history.snapshots_count > 1 && (
              <Badge className="bg-slate-700/50 text-slate-400 text-[10px]">
                {history.snapshots_count} snapshots
              </Badge>
            )}
          </div>
          {sentiment.rotation_call && (
            <p className="text-slate-300 text-xs leading-relaxed">{sentiment.rotation_call}</p>
          )}
        </div>
      )}

      {isAI && sentimentLoading && !sentiment && (
        <div className="mb-4 p-6 rounded-xl bg-purple-950/20 border border-purple-800/30 flex flex-col items-center gap-3">
          <div className="relative">
            <Brain className="w-8 h-8 text-purple-400 animate-pulse" />
            <Sparkles className="w-4 h-4 text-purple-300 absolute -top-1 -right-1 animate-bounce" />
          </div>
          <p className="text-purple-300 text-sm font-medium">AI agents analyzing sectors...</p>
          <p className="text-slate-500 text-xs">3 analysts + 1 strategist running in parallel</p>
        </div>
      )}

      {!isAI && summary.best_sector && summary.worst_sector && (
        <div className="grid grid-cols-2 gap-3 mb-4">
          <Card className="bg-emerald-950/20 border-emerald-800/30 rounded-xl p-3 flex items-center gap-3">
            <TrendingUp className="w-5 h-5 text-emerald-400" />
            <div>
              <p className="text-slate-400 text-[10px] uppercase">Best Sector</p>
              <p className="text-white text-sm font-bold">{summary.best_sector.name} <span className="text-emerald-400">+{summary.best_sector.change}%</span></p>
            </div>
          </Card>
          <Card className="bg-red-950/20 border-red-800/30 rounded-xl p-3 flex items-center gap-3">
            <TrendingDown className="w-5 h-5 text-red-400" />
            <div>
              <p className="text-slate-400 text-[10px] uppercase">Worst Sector</p>
              <p className="text-white text-sm font-bold">{summary.worst_sector.name} <span className="text-red-400">{summary.worst_sector.change}%</span></p>
            </div>
          </Card>
        </div>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2" data-testid="heatmap-grid">
        {sectors.map(s => {
          if (isAI) {
            const ai = sentimentData[s.symbol] || {};
            const heatVal = ai.heatmap_value ?? 50;
            const label = ai.label || getSentimentLabel(heatVal);
            const reasoning = ai.reasoning || '';
            return (
              <div
                key={s.symbol}
                className={`${getSentimentColor(heatVal)} rounded-xl p-4 transition-all hover:scale-[1.02] cursor-default relative overflow-hidden`}
                style={{ minHeight: `${Math.max(80, s.weight * 4)}px` }}
                data-testid={`sector-tile-${s.symbol}`}
                title={reasoning}
              >
                <div className="absolute top-0 right-0 opacity-10 text-6xl font-black leading-none select-none pointer-events-none" style={{ marginTop: '-8px', marginRight: '-4px' }}>
                  {s.symbol}
                </div>
                <div className="relative z-10">
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-bold text-sm">{s.symbol}</span>
                    <Badge className="bg-purple-900/40 border-purple-700/40 text-[9px] font-medium">{label}</Badge>
                  </div>
                  <p className="text-xs opacity-80 mb-2">{s.name}</p>
                  <div className="flex items-end justify-between">
                    <span className="text-2xl font-black tabular-nums">{heatVal.toFixed(0)}</span>
                    <div className="flex flex-col items-end gap-0.5">
                      {sectorTrends[s.symbol] && sectorTrends[s.symbol].length >= 2 && (
                        <Sparkline points={sectorTrends[s.symbol]} />
                      )}
                      <span className="text-[10px] opacity-60">/ 100</span>
                    </div>
                  </div>
                  {reasoning && (
                    <p className="text-[10px] opacity-60 mt-1 line-clamp-2">{reasoning}</p>
                  )}
                </div>
              </div>
            );
          }

          const val = s[period] || 0;
          const isUp = val >= 0;
          return (
            <div
              key={s.symbol}
              className={`${getHeatColor(val)} rounded-xl p-4 transition-all hover:scale-[1.02] cursor-default relative overflow-hidden`}
              style={{ minHeight: `${Math.max(80, s.weight * 4)}px` }}
              data-testid={`sector-tile-${s.symbol}`}
            >
              <div className="absolute top-0 right-0 opacity-10 text-6xl font-black leading-none select-none pointer-events-none" style={{ marginTop: '-8px', marginRight: '-4px' }}>
                {s.symbol}
              </div>
              <div className="relative z-10">
                <div className="flex items-center justify-between mb-1">
                  <span className="font-bold text-sm">{s.symbol}</span>
                  <Badge className="bg-black/20 border-0 text-[9px] font-medium">{s.weight}%</Badge>
                </div>
                <p className="text-xs opacity-80 mb-2">{s.name}</p>
                <div className="flex items-end justify-between">
                  <span className="text-2xl font-black tabular-nums">
                    {isUp ? '+' : ''}{val.toFixed(2)}%
                  </span>
                  {isUp ? <TrendingUp className="w-4 h-4 opacity-60" /> : <TrendingDown className="w-4 h-4 opacity-60" />}
                </div>
                <p className="text-[10px] opacity-60 mt-1">${s.price?.toFixed(2)}</p>
              </div>
            </div>
          );
        })}
      </div>

      <div className="flex items-center justify-between mt-4 text-[10px] text-slate-600">
        {isAI ? (
          <>
            <div className="flex items-center gap-2">
              <div className="flex items-center gap-1">
                <div className="w-3 h-3 rounded bg-red-500/90" /><span>0-25</span>
              </div>
              <div className="flex items-center gap-1">
                <div className="w-3 h-3 rounded bg-orange-800/50" /><span>25-45</span>
              </div>
              <div className="flex items-center gap-1">
                <div className="w-3 h-3 rounded bg-slate-600/50" /><span>45-55</span>
              </div>
              <div className="flex items-center gap-1">
                <div className="w-3 h-3 rounded bg-emerald-600/70" /><span>55-75</span>
              </div>
              <div className="flex items-center gap-1">
                <div className="w-3 h-3 rounded bg-emerald-500/90" /><span>75-100</span>
              </div>
            </div>
            <span className="flex items-center gap-1"><Brain className="w-3 h-3" /> AI Sentiment Score (0-100)</span>
          </>
        ) : (
          <>
            <div className="flex items-center gap-2">
              <div className="flex items-center gap-1">
                <div className="w-3 h-3 rounded bg-red-500/90" /><span>-3%+</span>
              </div>
              <div className="flex items-center gap-1">
                <div className="w-3 h-3 rounded bg-red-900/30" /><span>-0.5%</span>
              </div>
              <div className="flex items-center gap-1">
                <div className="w-3 h-3 rounded bg-emerald-900/30" /><span>+0.5%</span>
              </div>
              <div className="flex items-center gap-1">
                <div className="w-3 h-3 rounded bg-emerald-500/90" /><span>+3%+</span>
              </div>
            </div>
            <span>Tile size ~ S&P 500 sector weight</span>
          </>
        )}
      </div>
    </div>
  );
};

export default SectorHeatmap;
