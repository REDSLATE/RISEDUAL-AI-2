import React, { useState, useEffect, useCallback } from 'react';
import { Store, TrendingUp, TrendingDown, Target, Award, Copy, Eye, Clock, ArrowUpDown, X, Search, Lock, BarChart3, ChevronDown } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { toast } from './ui/sonner';
import { useAuth, authFetch } from '../contexts/AuthContext';
import logger from '../utils/logger';
import PanelShell from './PanelShell';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

const SORT_OPTIONS = [
  { value: 'newest', label: 'Newest' },
  { value: 'win_rate', label: 'Best Win Rate' },
  { value: 'pnl', label: 'Highest P&L' },
  { value: 'clones', label: 'Most Cloned' },
];

const StrategyMarketplace = ({ onClose, onSubscribe }) => {
  const { user, isPro } = useAuth();
  const [strategies, setStrategies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState('win_rate');
  const [total, setTotal] = useState(0);
  const [cloning, setCloning] = useState(null);
  const [expanded, setExpanded] = useState(null);
  const [searchTerm, setSearchTerm] = useState('');

  const fetchStrategies = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/marketplace/list?sort=${sort}&limit=50`);
      if (res.ok) {
        const data = await res.json();
        setStrategies(data.strategies);
        setTotal(data.total);
      }
    } catch (e) { logger.error(e); }
    setLoading(false);
  }, [sort]);

  useEffect(() => { fetchStrategies(); }, [fetchStrategies]);

  const cloneStrategy = async (strategyId) => {
    if (!isPro) { onSubscribe(); return; }
    setCloning(strategyId);
    try {
      const res = await authFetch(`${API}/marketplace/${strategyId}/clone`, { method: 'POST' });
      if (res.ok) {
        fetchStrategies();
      } else {
        const d = await res.json().catch(() => ({}));
        toast.error(d.detail || 'Clone failed');
      }
    } catch (e) { logger.error(e); }
    setCloning(null);
  };

  const filtered = searchTerm
    ? strategies.filter(s =>
        (s.strategy?.name || '').toLowerCase().includes(searchTerm.toLowerCase()) ||
        (s.description || '').toLowerCase().includes(searchTerm.toLowerCase()) ||
        (s.backtest?.symbol || '').toLowerCase().includes(searchTerm.toLowerCase())
      )
    : strategies;

  return (
    <PanelShell onClose={onClose} testId="strategy-marketplace" maxWidth="max-w-4xl">
      <div className="bg-slate-900 rounded-2xl w-full border border-slate-400/25">
        {/* Header */}
        <div className="p-6 border-b border-slate-400/30 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-gradient-to-br from-cyan-600 to-blue-600 rounded-xl flex items-center justify-center">
              <Store className="w-6 h-6 text-white" />
            </div>
            <div>
              <h2 className="text-white text-xl font-bold" style={{ fontFamily: 'Manrope, sans-serif' }}>Strategy Marketplace</h2>
              <p className="text-slate-300 text-xs">{total} strategies published by the community</p>
            </div>
          </div>
          {onClose && <button onClick={onClose} className="text-slate-400 hover:text-white" data-testid="marketplace-close">
            <X className="w-5 h-5" />
          </button>}
        </div>

        <div className="p-6 space-y-5">
          {/* Controls */}
          <div className="flex flex-col sm:flex-row gap-3">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
              <input
                value={searchTerm}
                onChange={e => setSearchTerm(e.target.value)}
                placeholder="Search strategies, tickers..."
                className="w-full bg-slate-800 border border-slate-400/30/60 rounded-xl pl-10 pr-4 py-2.5 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-cyan-500/60"
                data-testid="marketplace-search"
              />
            </div>
            <div className="flex items-center gap-2">
              <ArrowUpDown className="w-4 h-4 text-slate-400" />
              <select
                value={sort}
                onChange={e => setSort(e.target.value)}
                className="bg-slate-800 border border-slate-400/30/60 rounded-xl px-3 py-2.5 text-white text-sm focus:outline-none focus:border-cyan-500/60"
                data-testid="marketplace-sort"
              >
                {SORT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
          </div>

          {/* Strategy Grid */}
          {loading ? (
            <div className="text-center py-16">
              <div className="w-8 h-8 border-2 border-cyan-500 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
              <p className="text-slate-300 text-sm">Loading marketplace...</p>
            </div>
          ) : filtered.length === 0 ? (
            <div className="text-center py-16" data-testid="marketplace-empty">
              <div className="w-14 h-14 bg-slate-800/60 rounded-2xl flex items-center justify-center mx-auto mb-3 border border-slate-400/30/30">
                <Store className="w-7 h-7 text-slate-400" />
              </div>
              <p className="text-slate-300 text-sm font-medium mb-1">{searchTerm ? 'No matches found' : 'Marketplace is empty'}</p>
              <p className="text-slate-300 text-xs">{searchTerm ? 'Try a different search term.' : 'Be the first to publish a strategy!'}</p>
            </div>
          ) : (
            <div className="space-y-3" data-testid="marketplace-list">
              {filtered.map(s => (
                <StrategyCard
                  key={s.strategy_id}
                  item={s}
                  expanded={expanded === s.strategy_id}
                  onToggle={() => setExpanded(prev => prev === s.strategy_id ? null : s.strategy_id)}
                  onClone={() => cloneStrategy(s.strategy_id)}
                  cloning={cloning === s.strategy_id}
                  isPro={isPro}
                  isLoggedIn={!!user}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </PanelShell>
  );
};

const StrategyCard = ({ item, expanded, onToggle, onClone, cloning, isPro, isLoggedIn }) => {
  const m = item.backtest?.metrics || {};
  const profitable = (m.total_pnl || 0) >= 0;

  return (
    <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl overflow-hidden hover:border-cyan-700/40 transition-colors" data-testid={`marketplace-card-${item.strategy_id}`}>
      {/* Summary Row */}
      <button onClick={onToggle} className="w-full text-left p-4 flex items-center gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-white text-sm font-bold truncate">{item.strategy?.name || 'Unnamed Strategy'}</h3>
            <Badge className="bg-slate-700/60 text-slate-300 border-slate-600 text-[9px]">{item.backtest?.symbol}</Badge>
            <Badge className={`text-[9px] px-2 ${profitable ? 'bg-lime-700 text-lime-400 border-emerald-700/40' : 'bg-orange-800 text-orange-400 border-red-700/40'}`}>
              {profitable ? 'PROFITABLE' : 'LOSS'}
            </Badge>
          </div>
          <p className="text-slate-400 text-[11px] mt-1 truncate">{item.description}</p>
          <p className="text-slate-400 text-[10px] mt-0.5">by {item.author_name}</p>
        </div>

        {/* Quick Stats */}
        <div className="hidden sm:flex items-center gap-4 shrink-0">
          <QuickStat icon={Target} label="Win Rate" value={`${m.win_rate || 0}%`} color={m.win_rate >= 50 ? 'text-lime-400' : 'text-orange-400'} />
          <QuickStat icon={TrendingUp} label="P&L" value={`$${(m.total_pnl || 0).toFixed(0)}`} color={(m.total_pnl || 0) >= 0 ? 'text-lime-400' : 'text-orange-400'} />
          <QuickStat icon={Award} label="Sharpe" value={(m.sharpe_ratio || 0).toFixed(1)} color={m.sharpe_ratio >= 1 ? 'text-lime-400' : 'text-slate-300'} />
          <div className="flex items-center gap-3 text-slate-400 text-[10px]">
            <span className="flex items-center gap-1"><Eye className="w-3 h-3" />{item.views}</span>
            <span className="flex items-center gap-1"><Copy className="w-3 h-3" />{item.clones}</span>
          </div>
        </div>

        <ChevronDown className={`w-4 h-4 text-slate-400 shrink-0 transition-transform ${expanded ? 'rotate-180' : ''}`} />
      </button>

      {/* Expanded Detail */}
      {expanded && (
        <div className="px-4 pb-4 pt-0 border-t border-slate-400/25 space-y-4">
          {/* Mobile Stats */}
          <div className="sm:hidden grid grid-cols-3 gap-2 pt-3">
            <MobileStat label="Win Rate" value={`${m.win_rate || 0}%`} color={m.win_rate >= 50 ? 'text-lime-400' : 'text-orange-400'} />
            <MobileStat label="P&L" value={`$${(m.total_pnl || 0).toFixed(0)}`} color={(m.total_pnl || 0) >= 0 ? 'text-lime-400' : 'text-orange-400'} />
            <MobileStat label="Sharpe" value={(m.sharpe_ratio || 0).toFixed(1)} color="text-slate-300" />
          </div>

          {/* Full Metrics */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-3">
            <MetricBox label="Total Trades" value={m.total_trades || 0} />
            <MetricBox label="Max Drawdown" value={`$${(m.max_drawdown || 0).toFixed(0)}`} />
            <MetricBox label="Buy & Hold" value={`$${(m.buy_hold_pnl || 0).toFixed(0)}`} />
            <MetricBox label="Avg Holding" value={`${m.avg_holding_days || 0}d`} />
          </div>

          {/* Strategy Details */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {item.strategy?.entry_rules?.length > 0 && (
              <div className="bg-slate-800/50 rounded-lg p-3 border border-slate-400/30/30">
                <p className="text-lime-400 text-[10px] uppercase tracking-wider mb-1.5 font-medium">Entry Rules</p>
                {item.strategy.entry_rules.map((r, i) => (
                  <p key={`e-${i}`} className="text-slate-300 text-xs mb-0.5">- {r.condition}</p>
                ))}
              </div>
            )}
            {item.strategy?.exit_rules?.length > 0 && (
              <div className="bg-slate-800/50 rounded-lg p-3 border border-slate-400/30/30">
                <p className="text-orange-400 text-[10px] uppercase tracking-wider mb-1.5 font-medium">Exit Rules</p>
                {item.strategy.exit_rules.map((r, i) => (
                  <p key={`x-${i}`} className="text-slate-300 text-xs mb-0.5">- {r.condition}</p>
                ))}
              </div>
            )}
          </div>

          {/* Risk Management */}
          {item.strategy?.risk_management && (
            <div className="flex flex-wrap gap-2">
              {Object.entries(item.strategy.risk_management).map(([k, v]) => (
                <Badge key={k} className="bg-amber-900/20 text-amber-300 border-amber-800/30 text-[9px]">
                  {k.replace(/_/g, ' ')}: {String(v)}
                </Badge>
              ))}
            </div>
          )}

          {/* Clone Button */}
          <div className="flex items-center justify-between pt-2">
            <div className="flex items-center gap-2 text-slate-400 text-[10px]">
              <Clock className="w-3 h-3" />
              <span>Published {new Date(item.published_at).toLocaleDateString()}</span>
              <span>|</span>
              <span>{item.backtest?.years}yr backtest on {item.backtest?.symbol}</span>
            </div>
            <Button
              onClick={onClone}
              disabled={cloning}
              className={`text-xs h-8 rounded-lg px-4 ${isPro ? 'bg-cyan-600 hover:bg-cyan-500 text-white' : 'bg-slate-700 text-slate-400'}`}
              data-testid={`clone-btn-${item.strategy_id}`}
            >
              {!isPro ? (
                <><Lock className="w-3 h-3 mr-1" /> Pro Only</>
              ) : cloning ? (
                'Cloning...'
              ) : (
                <><Copy className="w-3.5 h-3.5 mr-1" /> Clone Strategy</>
              )}
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
};

const QuickStat = ({ icon: Icon, label, value, color }) => (
  <div className="text-center min-w-[60px]">
    <Icon className={`w-3.5 h-3.5 mx-auto mb-0.5 ${color}`} />
    <p className={`text-sm font-bold ${color}`}>{value}</p>
    <p className="text-slate-400 text-[8px]">{label}</p>
  </div>
);

const MobileStat = ({ label, value, color }) => (
  <div className="bg-slate-800/50 rounded-lg p-2 text-center border border-slate-400/30/30">
    <p className={`text-sm font-bold ${color}`}>{value}</p>
    <p className="text-slate-400 text-[9px]">{label}</p>
  </div>
);

const MetricBox = ({ label, value }) => (
  <div className="bg-slate-800/50 rounded-lg p-2.5 text-center border border-slate-400/30/30">
    <p className="text-white text-sm font-semibold">{value}</p>
    <p className="text-slate-400 text-[9px]">{label}</p>
  </div>
);

export default StrategyMarketplace;
