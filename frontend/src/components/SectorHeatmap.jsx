import React, { useState, useEffect, useCallback } from 'react';
import { Grid3x3, TrendingUp, TrendingDown, BarChart3, RefreshCw } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

const PERIODS = [
  { key: 'change_1d', label: '1D' },
  { key: 'change_1w', label: '1W' },
  { key: 'change_1m', label: '1M' },
  { key: 'change_3m', label: '3M' },
  { key: 'change_ytd', label: 'YTD' },
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

const SectorHeatmap = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [period, setPeriod] = useState('change_1d');
  const [error, setError] = useState('');

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${BACKEND_URL}/api/sectors/heatmap`);
      if (!res.ok) throw new Error('Failed to fetch sector data');
      setData(await res.json());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 120000);
    return () => clearInterval(interval);
  }, [fetchData]);

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
          {[1,2,3,4,5].map(i => <div key={i} className="skeleton w-12 h-7 rounded-lg" />)}
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

  return (
    <div data-testid="sector-heatmap">
      <div className="flex items-center justify-between mb-5 flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-gradient-to-br from-orange-600 to-red-600 rounded-xl flex items-center justify-center">
            <Grid3x3 className="w-6 h-6 text-white" />
          </div>
          <div>
            <h2 className="text-white text-xl sm:text-2xl font-bold" style={{ fontFamily: 'Manrope, sans-serif' }}>Sector Rotation</h2>
            <p className="text-slate-400 text-xs">S&P 500 sector ETF performance heatmap</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {PERIODS.map(p => (
            <button
              key={p.key}
              onClick={() => setPeriod(p.key)}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                period === p.key
                  ? 'bg-[#0052FF] text-white'
                  : 'bg-slate-800/60 text-slate-400 hover:text-white border border-slate-700/50'
              }`}
              data-testid={`period-${p.key}`}
            >
              {p.label}
            </button>
          ))}
          <button onClick={fetchData} className="p-1.5 rounded-lg bg-slate-800/60 text-slate-400 hover:text-white border border-slate-700/50 transition-all ml-1" data-testid="refresh-sectors">
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {summary.best_sector && summary.worst_sector && (
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
      </div>
    </div>
  );
};

export default SectorHeatmap;
