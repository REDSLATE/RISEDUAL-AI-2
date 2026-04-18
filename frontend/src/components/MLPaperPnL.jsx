import React, { useState, useEffect, useCallback } from 'react';
import {
  TrendingUp, TrendingDown, DollarSign, BarChart3, RefreshCw,
  ArrowUpRight, ArrowDownRight, Target, Crosshair
} from 'lucide-react';
import { Card } from './ui/card';
import logger from '../utils/logger';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid
} from 'recharts';

const API = `${getApiBase()}/api`;

const MLPaperPnL = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/ml/paper-trades?limit=100`);
      if (res.ok) setData(await res.json());
    } catch (e) {
      logger.warn('ML paper trades fetch failed:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  if (loading) {
    return (
      <Card className="p-6 border border-slate-700/40 bg-slate-800/20" data-testid="ml-paper-pnl-loading">
        <div className="flex items-center justify-center py-8">
          <RefreshCw className="w-5 h-5 text-teal-400 animate-spin" />
          <span className="text-slate-400 text-sm ml-2">Loading ML paper trades...</span>
        </div>
      </Card>
    );
  }

  const summary = data?.summary || {};
  const trades = data?.trades || [];
  const cumPnl = data?.cumulative_pnl || [];
  const sizing = data?.position_sizing || {};
  const hasTrades = summary.total_trades > 0;

  const pnlColor = (v) => v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-slate-400';
  const pnlSign = (v) => v > 0 ? '+' : '';

  return (
    <div className="space-y-4" data-testid="ml-paper-pnl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Target className="w-4 h-4 text-teal-400" />
          <span className="text-white text-sm font-semibold">ML Paper Trading P&L</span>
          <Badge className="bg-teal-500/15 text-teal-400 border-0 text-[9px]">
            {summary.total_trades || 0} trades
          </Badge>
        </div>
        <Button size="sm" variant="outline" onClick={fetchData}
          className="bg-slate-800 border-slate-600 text-slate-300 text-xs h-7" data-testid="ml-paper-refresh">
          <RefreshCw className="w-3 h-3 mr-1" /> Refresh
        </Button>
      </div>

      {!hasTrades ? (
        <Card className="p-8 border border-slate-700/40 bg-slate-800/20 text-center">
          <Crosshair className="w-8 h-8 text-slate-600 mx-auto mb-3" />
          <p className="text-slate-400 text-sm font-medium">No ML paper trades yet</p>
          <p className="text-slate-500 text-xs mt-1">
            Paper trades will appear here once the signal model is trained and Tier 2 is unlocked
          </p>
        </Card>
      ) : (
        <>
          {/* Summary Cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            <Card className="p-3 border border-slate-700/40 bg-slate-800/30">
              <p className="text-slate-500 text-[9px] uppercase tracking-wider">Total P&L</p>
              <p className={`text-lg font-bold ${pnlColor(summary.total_pnl_usd)}`} data-testid="ml-total-pnl">
                {pnlSign(summary.total_pnl_usd)}${Math.abs(summary.total_pnl_usd || 0).toLocaleString()}
              </p>
            </Card>
            <Card className="p-3 border border-slate-700/40 bg-slate-800/30">
              <p className="text-slate-500 text-[9px] uppercase tracking-wider">Win Rate</p>
              <p className="text-white text-lg font-bold" data-testid="ml-win-rate">
                {((summary.win_rate || 0) * 100).toFixed(1)}%
              </p>
            </Card>
            <Card className="p-3 border border-slate-700/40 bg-slate-800/30">
              <p className="text-slate-500 text-[9px] uppercase tracking-wider">Best Trade</p>
              <p className="text-emerald-400 text-lg font-bold">
                +${(summary.max_win_usd || 0).toLocaleString()}
              </p>
            </Card>
            <Card className="p-3 border border-slate-700/40 bg-slate-800/30">
              <p className="text-slate-500 text-[9px] uppercase tracking-wider">Worst Trade</p>
              <p className="text-red-400 text-lg font-bold">
                ${(summary.max_loss_usd || 0).toLocaleString()}
              </p>
            </Card>
          </div>

          {/* Cumulative PnL Chart */}
          {cumPnl.length > 1 && (
            <Card className="p-4 border border-slate-700/40 bg-slate-800/20">
              <div className="flex items-center gap-2 mb-3">
                <BarChart3 className="w-4 h-4 text-teal-400" />
                <span className="text-white text-xs font-semibold">Cumulative P&L</span>
              </div>
              <div className="h-48">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={cumPnl}>
                    <defs>
                      <linearGradient id="pnlGradient" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#3DE8D9" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#3DE8D9" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis
                      dataKey="date"
                      tick={{ fill: '#64748b', fontSize: 9 }}
                      tickFormatter={(v) => new Date(v).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                    />
                    <YAxis
                      tick={{ fill: '#64748b', fontSize: 9 }}
                      tickFormatter={(v) => `$${v}`}
                    />
                    <Tooltip
                      contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: '8px' }}
                      labelStyle={{ color: '#94a3b8', fontSize: 10 }}
                      formatter={(value, name) => [
                        `$${value.toLocaleString()}`,
                        name === 'cumulative_pnl' ? 'Cumulative' : 'Trade'
                      ]}
                    />
                    <Area
                      type="monotone"
                      dataKey="cumulative_pnl"
                      stroke="#3DE8D9"
                      fill="url(#pnlGradient)"
                      strokeWidth={2}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </Card>
          )}

          {/* Position Sizing */}
          {sizing.avg_size_usd > 0 && (
            <Card className="p-3 border border-slate-700/40 bg-slate-800/20">
              <div className="flex items-center gap-2 mb-2">
                <DollarSign className="w-3 h-3 text-amber-400" />
                <span className="text-white text-xs font-semibold">Position Sizing (Half-Kelly)</span>
              </div>
              <div className="grid grid-cols-3 gap-3 text-center">
                <div>
                  <p className="text-white text-sm font-bold">${sizing.avg_size_usd?.toLocaleString()}</p>
                  <p className="text-slate-500 text-[9px]">Average</p>
                </div>
                <div>
                  <p className="text-emerald-400 text-sm font-bold">${sizing.max_size_usd?.toLocaleString()}</p>
                  <p className="text-slate-500 text-[9px]">Largest</p>
                </div>
                <div>
                  <p className="text-amber-400 text-sm font-bold">${sizing.min_size_usd?.toLocaleString()}</p>
                  <p className="text-slate-500 text-[9px]">Smallest</p>
                </div>
              </div>
            </Card>
          )}

          {/* Recent Trades Table */}
          <Card className="p-4 border border-slate-700/40 bg-slate-800/20">
            <div className="flex items-center gap-2 mb-3">
              <TrendingUp className="w-4 h-4 text-violet-400" />
              <span className="text-white text-xs font-semibold">Recent ML Trades</span>
              <Badge className="ml-auto bg-slate-700 text-slate-300 border-0 text-[9px]">
                {summary.open_trades} open / {summary.closed_trades} closed
              </Badge>
            </div>
            <div className="space-y-1.5 max-h-64 overflow-y-auto">
              {trades.slice(0, 20).map((t, i) => (
                <div key={t.trade_id || i}
                  className="flex items-center justify-between py-2 px-3 rounded-lg bg-slate-900/40 hover:bg-slate-900/60 transition-colors"
                  data-testid={`ml-trade-${i}`}>
                  <div className="flex items-center gap-2">
                    <div className={`w-6 h-6 rounded flex items-center justify-center ${
                      t.direction === 'up' ? 'bg-emerald-500/20' : 'bg-red-500/20'
                    }`}>
                      {t.direction === 'up'
                        ? <ArrowUpRight className="w-3 h-3 text-emerald-400" />
                        : <ArrowDownRight className="w-3 h-3 text-red-400" />}
                    </div>
                    <div>
                      <span className="text-white text-xs font-medium">{t.ticker || t.symbol}</span>
                      <span className={`ml-2 text-[10px] ${t.direction === 'up' ? 'text-emerald-400' : 'text-red-400'}`}>
                        {(t.direction || t.side || '').toUpperCase()}
                      </span>
                      {t.confidence && (
                        <span className="text-slate-500 text-[10px] ml-1">
                          {(t.confidence * 100).toFixed(0)}%
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="text-right">
                    {t.pnl_usd != null ? (
                      <p className={`text-xs font-medium ${pnlColor(t.pnl_usd)}`}>
                        {pnlSign(t.pnl_usd)}${Math.abs(t.pnl_usd).toFixed(2)}
                      </p>
                    ) : (
                      <Badge className={`text-[8px] border-0 ${
                        t.status === 'open' ? 'bg-amber-500/15 text-amber-400' : 'bg-slate-700 text-slate-400'
                      }`}>
                        {t.status || 'pending'}
                      </Badge>
                    )}
                    <p className="text-slate-600 text-[9px]">
                      {t.position_size_usd ? `$${t.position_size_usd.toLocaleString()}` : t.total ? `$${t.total.toLocaleString()}` : ''}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </>
      )}
    </div>
  );
};

export default MLPaperPnL;
