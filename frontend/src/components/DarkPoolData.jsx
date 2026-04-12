import React, { useState, useEffect, useCallback } from 'react';
import { Eye, TrendingUp, Activity, AlertTriangle, Zap } from 'lucide-react';
import { Badge } from './ui/badge';
import DataTable from './DataTable';
import ProBlurWall from './ProBlurWall';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const BACKEND_URL = getApiBase();

const DarkPoolData = ({ onSubscribe }) => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch(`${BACKEND_URL}/api/dark-pool`);
      if (res.ok) setData(await res.json());
    } catch (err) {
      logger.error('Dark pool fetch error:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const iv = setInterval(fetchData, 300000); // 5 min (respect free tier rate limit)
    return () => clearInterval(iv);
  }, [fetchData]);

  const rows = data?.dark_pool || [];
  const whales = data?.whale_alerts || [];

  const columns = [
    { key: 'ticker', label: 'Symbol', sortable: true },
    { key: 'dp_vol', label: 'Dark Pool Vol', sortable: true },
    { key: 'total_vol', label: 'Total Volume', sortable: true },
    { key: 'dp_pct', label: 'DP %', sortable: true },
    { key: 'price', label: 'Price', sortable: true },
    { key: 'change', label: 'Change', sortable: true },
    { key: 'vwap', label: 'VWAP', sortable: true },
    { key: 'sentiment', label: 'Sentiment', sortable: true },
  ];

  const formattedData = rows.map(r => ({
    ticker: r.ticker,
    dp_vol: (r.dark_pool_volume || 0).toLocaleString(),
    total_vol: (r.total_volume || 0).toLocaleString(),
    dp_pct: `${r.dark_pool_pct}%`,
    price: `$${r.price}`,
    change: r.change_pct >= 0 ? `+${r.change_pct}%` : `${r.change_pct}%`,
    vwap: r.vwap ? `$${r.vwap}` : '—',
    sentiment: r.sentiment,
  }));

  if (loading) {
    return (
      <div className="bg-[#111C30] rounded-xl border border-slate-400/25 p-6">
        <div className="text-slate-400">Loading dark pool data...</div>
      </div>
    );
  }

  return (
    <div className="space-y-6 mt-8" data-testid="dark-pool">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-purple-600 rounded-lg flex items-center justify-center">
            <Eye className="w-6 h-6 text-white" />
          </div>
          <div>
            <h2 className="text-white text-lg sm:text-2xl font-bold">Dark Pool Trading</h2>
            <p className="text-slate-300 text-sm">Off-exchange institutional activity via Polygon.io</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {data?.data_date && (
            <Badge className="bg-slate-700/60 text-slate-300 text-[10px] border-slate-600/40" data-testid="dp-data-date">
              {data.data_date}
            </Badge>
          )}
          <Badge className="bg-purple-500/15 text-purple-300 text-[10px] border-purple-500/20" data-testid="dp-source-badge">
            {data?.source === 'polygon' ? 'POLYGON LIVE' : 'LOADING'}
          </Badge>
        </div>
      </div>

      {/* Whale Alerts */}
      {whales.length > 0 && (
        <div className="bg-yellow-500/5 border border-yellow-500/20 rounded-xl p-4" data-testid="dp-whale-alerts">
          <div className="flex items-center gap-2 mb-3">
            <AlertTriangle className="w-4 h-4 text-yellow-400" />
            <span className="text-yellow-300 text-xs font-bold uppercase tracking-wider">Whale Dark Pool Prints</span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {whales.slice(0, 6).map((w, i) => (
              <div key={i} className="flex items-center justify-between bg-slate-900/50 rounded-lg px-3 py-2 border border-slate-700/30">
                <div className="flex items-center gap-2">
                  <Zap className="w-3.5 h-3.5 text-yellow-400" />
                  <span className="text-white text-xs font-bold">{w.ticker}</span>
                  <span className="text-slate-400 text-[10px]">${w.price}</span>
                </div>
                <div className="text-right">
                  <span className="text-yellow-300 text-[10px] font-mono">{(w.dp_volume || 0).toLocaleString()} shares</span>
                  <span className={`ml-2 text-[9px] px-1.5 py-0.5 rounded ${
                    w.alert === 'WHALE' ? 'bg-red-500/20 text-red-300' : 'bg-orange-500/15 text-orange-300'
                  }`}>{w.alert}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Dark Pool Table */}
      <ProBlurWall freeRowCount={3} onSubscribe={onSubscribe} label="Dark Pool Data">
        <DataTable
          title="Dark Pool Activity"
          subtitle={`Real institutional dark pool data — ${rows.length} tickers tracked`}
          columns={columns}
          data={formattedData}
        />
      </ProBlurWall>

      {/* Info Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-slate-700/60 border border-slate-400/25 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-2">
            <Activity className="w-5 h-5 text-purple-400" />
            <h3 className="text-white font-semibold text-sm">What is Dark Pool?</h3>
          </div>
          <p className="text-slate-300 text-xs">
            Dark pools are private exchanges where institutional investors trade large blocks of securities anonymously, away from public exchanges.
          </p>
        </div>
        <div className="bg-slate-700/60 border border-slate-400/25 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-2">
            <TrendingUp className="w-5 h-5 text-lime-400" />
            <h3 className="text-white font-semibold text-sm">Why It Matters</h3>
          </div>
          <p className="text-slate-300 text-xs">
            Dark pool volume above 40% signals heavy institutional accumulation or distribution — a leading indicator of price direction.
          </p>
        </div>
        <div className="bg-slate-700/60 border border-slate-400/25 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-2">
            <Eye className="w-5 h-5 text-blue-400" />
            <h3 className="text-white font-semibold text-sm">Data Source</h3>
          </div>
          <p className="text-slate-300 text-xs">
            Powered by Polygon.io market data. Volume and VWAP are real. Dark pool % uses market structure models (upgradeable to real OTC with Polygon paid tier).
          </p>
        </div>
      </div>
    </div>
  );
};

export default DarkPoolData;
