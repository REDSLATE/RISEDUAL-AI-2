import React, { useState, useEffect, useCallback } from 'react';
import { Activity, TrendingUp, TrendingDown, Minus, RefreshCw, ChevronDown, ChevronUp, Radio } from 'lucide-react';
import { getApiBase } from '../utils/apiBase';
import OrderFlowHeatmap from './OrderFlowHeatmap';

const API = `${getApiBase()}/api`;

const CRYPTO_SET = new Set(['BTC', 'ETH', 'SOL', 'DOGE', 'ADA', 'XRP', 'AVAX', 'DOT', 'SHIB', 'LINK']);

const BIAS_STYLES = {
  INSTITUTIONAL_BID: { label: 'Institutional Buying', color: 'text-lime-400', bg: 'bg-green-500/10 border-emerald-500/20', icon: TrendingUp },
  INSTITUTIONAL_ASK: { label: 'Institutional Selling', color: 'text-orange-400', bg: 'bg-red-500/10 border-red-500/20', icon: TrendingDown },
  BALANCED: { label: 'Balanced Flow', color: 'text-slate-400', bg: 'bg-slate-500/10 border-slate-500/20', icon: Minus },
  NO_DATA: { label: 'No Data', color: 'text-slate-400', bg: 'bg-slate-700/60 border-slate-400/30', icon: Activity },
};

const WallBar = ({ wall, maxVol }) => {
  const pct = maxVol > 0 ? (wall.volume / maxVol) * 100 : 0;
  const isSupport = wall.type === 'support';
  const intensity = wall.intensity ?? 0;
  const intensityColor = intensity >= 85 ? 'text-yellow-300' : intensity >= 50 ? (isSupport ? 'text-lime-400' : 'text-orange-400') : 'text-slate-400';

  return (
    <div className="flex items-center gap-2 text-xs" data-testid={`wall-${wall.type}-${wall.price}`}>
      <span className="text-slate-400 w-16 text-right font-mono">${wall.price.toFixed(2)}</span>
      <div className="flex-1 h-4 bg-slate-900 rounded-sm overflow-hidden relative">
        <div
          className={`h-full rounded-sm ${isSupport ? 'bg-green-500/40' : 'bg-red-500/40'}`}
          style={{ width: `${Math.max(pct, 5)}%` }}
        />
        <span className="absolute inset-0 flex items-center justify-center text-[10px] text-slate-300 font-mono">
          {wall.volume >= 1e6 ? `${(wall.volume / 1e6).toFixed(1)}M` : wall.volume >= 1e3 ? `${(wall.volume / 1e3).toFixed(0)}K` : wall.volume.toFixed(0)}
        </span>
      </div>
      <span className={`w-8 text-right font-mono text-[10px] ${intensityColor}`} data-testid={`intensity-${wall.price}`}>
        {intensity}
      </span>
      <span className={`w-5 text-center text-[10px] px-1 py-0.5 rounded ${
        intensity >= 85
          ? 'bg-yellow-500/20 text-yellow-300 font-bold'
          : wall.strength === 'major'
            ? (isSupport ? 'bg-green-500/20 text-lime-400' : 'bg-red-500/20 text-orange-400')
            : 'bg-slate-800 text-slate-400'}`}>
        {intensity >= 85 ? 'W' : wall.strength === 'major' ? 'M' : 'm'}
      </span>
    </div>
  );
};

const OrderFlowPanel = ({ symbol = 'SPY' }) => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(true);
  const [ticker, setTicker] = useState(symbol);
  const [tab, setTab] = useState('snapshot'); // 'snapshot' | 'live'

  const isCrypto = CRYPTO_SET.has(ticker.toUpperCase());

  const fetchFlow = useCallback(async (sym) => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/order-flow/${sym}`);
      if (res.ok) {
        const d = await res.json();
        if (!d.error) setData(d);
        else setData(null);
      }
    } catch {
      setData(null);
    }
    setLoading(false);
  }, []);

  useEffect(() => { fetchFlow(ticker); }, [ticker, fetchFlow]);

  const bias = data?.summary?.bias || 'NO_DATA';
  const biasStyle = BIAS_STYLES[bias] || BIAS_STYLES.NO_DATA;
  const BiasIcon = biasStyle.icon;

  const walls = data?.walls || [];
  const supportWalls = walls.filter(w => w.type === 'support');
  const resistanceWalls = walls.filter(w => w.type === 'resistance');
  const maxVol = walls.length > 0 ? Math.max(...walls.map(w => w.volume)) : 0;

  return (
    <div className="bg-[#111C30] border border-slate-800/60 rounded-xl overflow-hidden" data-testid="order-flow-panel">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-slate-600/30/20 transition-colors"
        data-testid="order-flow-toggle"
      >
        <div className="flex items-center gap-3">
          <Activity className="w-4 h-4 text-[#3DE8D9]" />
          <h3 className="text-white text-sm font-semibold tracking-wide">Order Flow</h3>
          {data && (
            <span className={`text-[10px] px-2 py-0.5 rounded-full border ${biasStyle.bg} ${biasStyle.color}`}>
              <BiasIcon className="w-3 h-3 inline mr-1" />
              {biasStyle.label}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {data && (
            <span className="flex items-center gap-1.5 text-slate-300 text-xs">
              {data.source === 'binance_l2' && (
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-yellow-500/10 text-yellow-300 border border-yellow-500/20" data-testid="source-binance">L2</span>
              )}
              {ticker} ${data.current_price}
            </span>
          )}
          {expanded ? <ChevronUp className="w-4 h-4 text-slate-400" /> : <ChevronDown className="w-4 h-4 text-slate-400" />}
        </div>
      </button>

      {expanded && (
        <div className="border-t border-slate-600/30/50">
          {/* Ticker selector */}
          <div className="px-4 py-2 flex items-center gap-2 border-b border-slate-600/30/30">
            <div className="flex gap-1.5 flex-wrap">
              {['SPY', 'AAPL', 'TSLA', 'NVDA', 'MSFT', 'BTC', 'ETH'].map(t => (
                <button key={t} onClick={() => setTicker(t)}
                  className={`text-[10px] px-2 py-1 rounded transition-colors ${ticker === t
                    ? 'bg-[#3DE8D9] text-white'
                    : 'bg-slate-700/60 text-slate-400 hover:text-white hover:bg-slate-700'}`}
                  data-testid={`flow-ticker-${t}`}
                >
                  {t}
                </button>
              ))}
            </div>
            {/* View tabs */}
            <div className="flex gap-1 ml-auto mr-2">
              <button onClick={() => setTab('snapshot')}
                className={`text-[10px] px-2 py-1 rounded transition-colors ${
                  tab === 'snapshot' ? 'bg-slate-700 text-white' : 'text-slate-400 hover:text-slate-300'}`}
                data-testid="tab-snapshot">
                Snapshot
              </button>
              {isCrypto && (
                <button onClick={() => setTab('live')}
                  className={`text-[10px] px-2 py-1 rounded transition-colors flex items-center gap-1 ${
                    tab === 'live' ? 'bg-[#3DE8D9] text-white' : 'text-slate-400 hover:text-slate-300'}`}
                  data-testid="tab-live">
                  <Radio className="w-3 h-3" />
                  Live
                </button>
              )}
            </div>
            {tab === 'snapshot' && (
              <button onClick={() => fetchFlow(ticker)} disabled={loading}
                className="text-slate-400 hover:text-white transition-colors"
                data-testid="flow-refresh">
                <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
              </button>
            )}
          </div>

          {/* Live Heatmap Tab */}
          {tab === 'live' && isCrypto ? (
            <div className="px-4 py-3">
              <OrderFlowHeatmap symbol={ticker} />
            </div>
          ) : (
          <>
          {loading ? (
            <div className="py-8 flex justify-center">
              <RefreshCw className="w-5 h-5 text-[#3DE8D9] animate-spin" />
            </div>
          ) : !data ? (
            <div className="py-8 text-center">
              <Activity className="w-6 h-6 text-slate-700 mx-auto mb-2" />
              <p className="text-slate-300 text-xs">No order flow data available</p>
            </div>
          ) : (
            <div className="px-4 py-3 space-y-4">
              {/* Summary stats */}
              <div className="grid grid-cols-3 gap-3">
                <div className="text-center">
                  <p className="text-slate-400 text-[10px] uppercase tracking-wider">
                    {data.source === 'binance_l2' ? 'Spread' : 'POC'}
                  </p>
                  <p className="text-white text-sm font-bold">
                    {data.source === 'binance_l2'
                      ? `${data.spread?.pct?.toFixed(3) ?? '—'}%`
                      : `$${data.point_of_control?.price?.toFixed(2) ?? '—'}`}
                  </p>
                </div>
                <div className="text-center">
                  <p className="text-slate-400 text-[10px] uppercase tracking-wider">Support</p>
                  <p className="text-lime-400 text-sm font-bold">{supportWalls.length}</p>
                </div>
                <div className="text-center">
                  <p className="text-slate-400 text-[10px] uppercase tracking-wider">Resistance</p>
                  <p className="text-orange-400 text-sm font-bold">{resistanceWalls.length}</p>
                </div>
              </div>

              {/* Volume bars */}
              {walls.length > 0 && (
                <div className="space-y-1.5" data-testid="wall-list">
                  <div className="flex items-center justify-between text-[10px] text-slate-400 px-0.5">
                    <span>Price Level</span>
                    <span className="flex gap-4"><span>Volume</span><span>Int</span></span>
                  </div>
                  {walls.slice(0, 8).map((w, i) => (
                    <WallBar key={i} wall={w} maxVol={maxVol} />
                  ))}
                </div>
              )}

              {/* Price range / depth info */}
              <div className="text-[10px] text-slate-400 flex justify-between">
                {data.source === 'binance_l2' ? (
                  <>
                    <span>Bid: ${data.spread?.best_bid?.toFixed(2) ?? '—'}</span>
                    <span>{data.depth_levels} depth levels · {data.total_bids}B/{data.total_asks}A</span>
                    <span>Ask: ${data.spread?.best_ask?.toFixed(2) ?? '—'}</span>
                  </>
                ) : (
                  <>
                    <span>L: ${data.price_range?.low?.toFixed(2) ?? '—'}</span>
                    <span>{data.total_bars ?? 0} bars ({data.period}/{data.interval})</span>
                    <span>H: ${data.price_range?.high?.toFixed(2) ?? '—'}</span>
                  </>
                )}
              </div>
            </div>
          )}
          </>
          )}
        </div>
      )}
    </div>
  );
};

export default OrderFlowPanel;
