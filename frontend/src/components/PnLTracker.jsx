import React, { useState, useEffect, useCallback } from 'react';
import { DollarSign, TrendingUp, TrendingDown, Briefcase, PieChart, RefreshCw, AlertTriangle, ExternalLink } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { authFetch } from '../contexts/AuthContext';
import { useAuth } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

const PnLTracker = ({ onOpenBroker }) => {
  const { user } = useAuth();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const fetchPnL = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await authFetch(`${API}/broker/pnl-summary`);
      if (!res.ok) {
        if (res.status === 401) { setError('Login required'); return; }
        throw new Error('Failed to fetch P&L data');
      }
      setData(await res.json());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user) {
      fetchPnL();
      const interval = setInterval(fetchPnL, 30000);
      return () => clearInterval(interval);
    }
  }, [user, fetchPnL]);

  if (!user) {
    return (
      <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-8 text-center">
        <Briefcase className="w-10 h-10 text-slate-600 mx-auto mb-3" />
        <p className="text-slate-400 text-sm">Login to view your portfolio P&L</p>
      </Card>
    );
  }

  if (loading && !data) {
    return (
      <div className="flex items-center justify-center py-16">
        <RefreshCw className="w-5 h-5 text-[#35D6C8] animate-spin mr-3" />
        <span className="text-slate-400 text-sm">Loading portfolio...</span>
      </div>
    );
  }

  if (error) {
    return (
      <Card className="bg-red-900/20 border-red-800/40 rounded-xl p-4">
        <div className="flex items-center gap-2 text-red-400 text-sm">
          <AlertTriangle className="w-4 h-4" />
          {error}
        </div>
      </Card>
    );
  }

  const hasPositions = data?.positions_count > 0;
  const isUp = (data?.total_pl || 0) >= 0;

  return (
    <div data-testid="pnl-tracker">
      <div className="flex items-center justify-between mb-5 flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <div className={`w-10 h-10 bg-gradient-to-br ${isUp ? 'from-emerald-600 to-teal-600' : 'from-red-600 to-orange-600'} rounded-xl flex items-center justify-center`}>
            <DollarSign className="w-6 h-6 text-white" />
          </div>
          <div>
            <h2 className="text-white text-xl sm:text-2xl font-bold" style={{ fontFamily: 'Manrope, sans-serif' }}>P&L Tracker</h2>
            <p className="text-slate-400 text-xs">Real-time portfolio performance across brokers</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={fetchPnL} className="p-1.5 rounded-lg bg-slate-800/60 text-slate-400 hover:text-white border border-slate-700/50 transition-all" data-testid="refresh-pnl">
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {!hasPositions ? (
        <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-8 text-center">
          <Briefcase className="w-12 h-12 text-slate-600 mx-auto mb-4" />
          <p className="text-white text-lg font-semibold mb-2">No Positions Found</p>
          <p className="text-slate-400 text-sm mb-4">Connect a broker and open positions to track your P&L</p>
          {onOpenBroker && (
            <Button onClick={onOpenBroker} className="bg-[#35D6C8] hover:bg-[#67E3D3] text-white rounded-xl" data-testid="connect-broker-btn">
              <ExternalLink className="w-4 h-4 mr-2" /> Connect Broker
            </Button>
          )}
        </Card>
      ) : (
        <div className="space-y-4">
          {/* Summary Cards */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <SummaryCard
              label="Portfolio Value"
              value={`$${(data.total_value || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
              icon={<Briefcase className="w-4 h-4 text-[#35D6C8]" />}
              accent="blue"
            />
            <SummaryCard
              label="Unrealized P&L"
              value={`${isUp ? '+' : ''}$${(data.total_pl || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
              sub={`${isUp ? '+' : ''}${data.total_pl_pct || 0}%`}
              icon={isUp ? <TrendingUp className="w-4 h-4 text-emerald-400" /> : <TrendingDown className="w-4 h-4 text-red-400" />}
              accent={isUp ? 'green' : 'red'}
            />
            <SummaryCard
              label="Cost Basis"
              value={`$${(data.total_cost_basis || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
              icon={<DollarSign className="w-4 h-4 text-amber-400" />}
              accent="amber"
            />
            <SummaryCard
              label="Positions"
              value={data.positions_count || 0}
              sub={`${data.brokers?.length || 0} broker${(data.brokers?.length || 0) !== 1 ? 's' : ''}`}
              icon={<PieChart className="w-4 h-4 text-violet-400" />}
              accent="violet"
            />
          </div>

          {/* Broker Breakdown */}
          {data.brokers?.length > 0 && (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {data.brokers.map(b => (
                <Card key={b.broker_id} className="bg-slate-800/50 border-slate-700/40 rounded-xl p-4" data-testid={`broker-card-${b.broker_id}`}>
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <span className="text-white text-sm font-semibold capitalize">{b.broker_id}</span>
                      {b.paper && <Badge className="bg-amber-900/30 text-amber-400 text-[9px] border-amber-700/50">PAPER</Badge>}
                    </div>
                    {b.error ? (
                      <Badge className="bg-red-900/30 text-red-400 text-[9px]">Error</Badge>
                    ) : (
                      <span className="text-slate-500 text-[10px]">{b.positions_count} positions</span>
                    )}
                  </div>
                  {b.error ? (
                    <p className="text-red-400 text-xs">{b.error}</p>
                  ) : (
                    <div className="flex items-end justify-between">
                      <div>
                        <p className="text-white text-lg font-bold tabular-nums">${(b.portfolio_value || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}</p>
                        <p className="text-slate-500 text-[10px]">Cash: ${(b.cash || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}</p>
                      </div>
                      <span className={`text-sm font-bold ${(b.unrealized_pl || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {(b.unrealized_pl || 0) >= 0 ? '+' : ''}${(b.unrealized_pl || 0).toFixed(2)}
                      </span>
                    </div>
                  )}
                </Card>
              ))}
            </div>
          )}

          {/* Positions Table */}
          <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl overflow-hidden">
            <div className="p-4 border-b border-slate-700/40 flex items-center justify-between">
              <h3 className="text-white text-sm font-semibold">Open Positions</h3>
              <span className="text-slate-500 text-[10px]">{data.positions?.length || 0} positions</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm" data-testid="positions-table">
                <thead>
                  <tr className="border-b border-slate-700/30">
                    <th className="text-left text-slate-400 text-xs font-medium px-4 py-2.5">Symbol</th>
                    <th className="text-right text-slate-400 text-xs font-medium px-4 py-2.5">Qty</th>
                    <th className="text-right text-slate-400 text-xs font-medium px-4 py-2.5">Avg Entry</th>
                    <th className="text-right text-slate-400 text-xs font-medium px-4 py-2.5">Current</th>
                    <th className="text-right text-slate-400 text-xs font-medium px-4 py-2.5">Mkt Value</th>
                    <th className="text-right text-slate-400 text-xs font-medium px-4 py-2.5">P&L</th>
                    <th className="text-right text-slate-400 text-xs font-medium px-4 py-2.5">P&L %</th>
                    <th className="text-left text-slate-400 text-xs font-medium px-4 py-2.5">Broker</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.positions || []).map((p, i) => {
                    const posUp = p.unrealized_pl >= 0;
                    return (
                      <tr key={`${p.symbol}-${p.broker}-${i}`} className="border-b border-slate-800/40 hover:bg-slate-700/20 transition-colors" data-testid={`position-row-${p.symbol}`}>
                        <td className="px-4 py-2.5">
                          <span className="text-[#35D6C8] font-bold">{p.symbol}</span>
                          {p.side === 'short' && <Badge className="ml-1 text-[8px] bg-red-900/30 text-red-400">SHORT</Badge>}
                        </td>
                        <td className="px-4 py-2.5 text-right text-white tabular-nums">{p.qty}</td>
                        <td className="px-4 py-2.5 text-right text-slate-300 tabular-nums">${p.avg_entry.toFixed(2)}</td>
                        <td className="px-4 py-2.5 text-right text-white tabular-nums">${p.current_price.toFixed(2)}</td>
                        <td className="px-4 py-2.5 text-right text-slate-300 tabular-nums">${p.market_value.toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                        <td className={`px-4 py-2.5 text-right font-semibold tabular-nums ${posUp ? 'text-emerald-400' : 'text-red-400'}`}>
                          {posUp ? '+' : ''}${p.unrealized_pl.toFixed(2)}
                        </td>
                        <td className={`px-4 py-2.5 text-right font-semibold tabular-nums ${posUp ? 'text-emerald-400' : 'text-red-400'}`}>
                          {posUp ? '+' : ''}{p.unrealized_pl_pct.toFixed(2)}%
                        </td>
                        <td className="px-4 py-2.5">
                          <span className="text-slate-500 text-xs capitalize">{p.broker}</span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>

          {/* Sector Allocation */}
          {data.sector_allocation?.length > 0 && (
            <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-4">
              <h3 className="text-white text-sm font-semibold mb-3 flex items-center gap-2">
                <PieChart className="w-4 h-4 text-violet-400" /> Sector Allocation
              </h3>
              <div className="space-y-2">
                {data.sector_allocation.map(s => (
                  <div key={s.name} className="flex items-center gap-3" data-testid={`sector-alloc-${s.name}`}>
                    <span className="text-slate-300 text-xs w-28 truncate">{s.name}</span>
                    <div className="flex-1 bg-slate-700/30 rounded-full h-2">
                      <div
                        className="h-2 rounded-full bg-gradient-to-r from-[#35D6C8] to-violet-500"
                        style={{ width: `${Math.min(s.pct, 100)}%` }}
                      />
                    </div>
                    <span className="text-slate-400 text-[10px] w-10 text-right">{s.pct}%</span>
                    <span className={`text-[10px] font-semibold w-16 text-right ${s.pl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {s.pl >= 0 ? '+' : ''}${s.pl.toFixed(0)}
                    </span>
                  </div>
                ))}
              </div>
            </Card>
          )}
        </div>
      )}
    </div>
  );
};

const SummaryCard = ({ label, value, sub, icon, accent }) => {
  const accents = {
    blue: 'from-blue-950/30 to-blue-900/10 border-blue-800/30',
    green: 'from-emerald-950/30 to-emerald-900/10 border-emerald-800/30',
    red: 'from-red-950/30 to-red-900/10 border-red-800/30',
    amber: 'from-amber-950/30 to-amber-900/10 border-amber-800/30',
    violet: 'from-violet-950/30 to-violet-900/10 border-violet-800/30',
  };
  return (
    <Card className={`bg-gradient-to-br ${accents[accent] || ''} rounded-xl p-4`}>
      <div className="flex items-center gap-2 mb-1">{icon}<span className="text-slate-400 text-[10px] uppercase">{label}</span></div>
      <p className="text-white text-xl font-bold tabular-nums">{value}</p>
      {sub && <p className="text-slate-500 text-[10px] mt-0.5">{sub}</p>}
    </Card>
  );
};

export default PnLTracker;
