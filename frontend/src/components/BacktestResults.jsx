import React, { useMemo } from 'react';
import { TrendingUp, TrendingDown, BarChart3, Target, Calendar, Activity, Award, AlertTriangle, ChevronDown, ChevronUp } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, BarChart, Bar, Cell } from 'recharts';

const chartTooltipStyle = { backgroundColor: '#1E293B', border: '1px solid #334155', borderRadius: '8px', fontSize: '12px' };
const chartLabelStyle = { color: '#94A3B8' };
const axisTick = { fill: '#64748B', fontSize: 10 };

const BacktestResults = ({ result, onClose }) => {
  const m = result.metrics;

  const statCards = useMemo(() => [
    { label: 'Total Trades', value: m.total_trades, color: 'text-white', icon: Activity },
    { label: 'Win Rate', value: `${m.win_rate}%`, color: m.win_rate >= 50 ? 'text-emerald-400' : 'text-red-400', icon: Target },
    { label: 'Total P&L', value: `$${m.total_pnl.toFixed(2)}`, color: m.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400', icon: TrendingUp },
    { label: 'Sharpe Ratio', value: m.sharpe_ratio.toFixed(2), color: m.sharpe_ratio >= 1 ? 'text-emerald-400' : m.sharpe_ratio >= 0.5 ? 'text-amber-400' : 'text-red-400', icon: Award },
    { label: 'Max Drawdown', value: `$${m.max_drawdown.toFixed(2)}`, color: 'text-red-400', icon: TrendingDown },
    { label: 'Avg Holding', value: `${m.avg_holding_days}d`, color: 'text-slate-300', icon: Calendar },
  ], [m]);

  return (
    <div className="space-y-5" data-testid="backtest-results">
      {/* Header */}
      <Card className="bg-gradient-to-r from-cyan-950/40 to-blue-950/40 border-cyan-800/40 rounded-xl p-5">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <h3 className="text-white text-lg font-bold flex items-center gap-2">
              <BarChart3 className="w-5 h-5 text-cyan-400" />
              Backtest: {result.symbol}
            </h3>
            <p className="text-slate-400 text-xs mt-1">
              {result.strategy_name} | {result.date_range.start} to {result.date_range.end} ({result.data_points} days)
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Badge className={`text-xs font-bold px-3 py-1 ${m.total_pnl >= 0 ? 'bg-emerald-900/40 text-emerald-400 border-emerald-700/50' : 'bg-red-900/40 text-red-400 border-red-700/50'}`} data-testid="backtest-verdict">
              {m.total_pnl >= 0 ? 'PROFITABLE' : 'UNPROFITABLE'}
            </Badge>
          </div>
        </div>
      </Card>

      {/* Stats Grid */}
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
        {statCards.map(({ label, value, color, icon: Icon }) => (
          <Card key={label} className="bg-slate-800/60 border-slate-700/40 rounded-xl p-3 text-center">
            <Icon className={`w-4 h-4 mx-auto mb-1 ${color}`} />
            <p className={`text-lg font-bold ${color}`}>{value}</p>
            <p className="text-slate-500 text-[9px]">{label}</p>
          </Card>
        ))}
      </div>

      {/* P&L Curve */}
      {m.cumulative_pnl?.length > 0 && (
        <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-5">
          <h4 className="text-white text-sm font-semibold mb-3">Cumulative P&L</h4>
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={m.cumulative_pnl}>
              <defs>
                <linearGradient id="btPnlGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#06B6D4" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#06B6D4" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="date" tick={axisTick} tickLine={false} axisLine={false}
                tickFormatter={v => v ? new Date(v).toLocaleDateString('en-US', { month: 'short', year: '2-digit' }) : ''} />
              <YAxis tick={axisTick} tickLine={false} axisLine={false} tickFormatter={v => `$${v}`} />
              <Tooltip contentStyle={chartTooltipStyle} labelStyle={chartLabelStyle}
                formatter={(v) => [`$${v.toFixed(2)}`, 'Cumulative P&L']} />
              <Area type="monotone" dataKey="pnl" stroke="#06B6D4" fill="url(#btPnlGrad)" strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </Card>
      )}

      {/* Buy & Hold Comparison + Win/Loss */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-5">
          <h4 className="text-slate-400 text-xs font-medium uppercase mb-3">Strategy vs Buy & Hold</h4>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-slate-300 text-sm">Strategy P&L</span>
              <span className={`text-sm font-bold ${m.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                ${m.total_pnl.toFixed(2)}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-slate-300 text-sm">Buy & Hold P&L</span>
              <span className={`text-sm font-bold ${m.buy_hold_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                ${m.buy_hold_pnl.toFixed(2)} ({m.buy_hold_pct}%)
              </span>
            </div>
            <div className="border-t border-slate-700 pt-2">
              <span className="text-slate-500 text-xs">
                {m.total_pnl > m.buy_hold_pnl ? 'Strategy outperforms Buy & Hold' : 'Buy & Hold outperforms this strategy'}
              </span>
            </div>
          </div>
        </Card>

        <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-5">
          <h4 className="text-slate-400 text-xs font-medium uppercase mb-3">Win / Loss Breakdown</h4>
          <div className="flex items-center gap-4">
            <div className="flex-1">
              <div className="flex items-center justify-between mb-1">
                <span className="text-emerald-400 text-sm">Wins: {m.winning_trades}</span>
                <span className="text-emerald-400 text-xs">Avg +${m.avg_gain?.toFixed(2) || '0'}</span>
              </div>
              <div className="w-full bg-slate-700/50 rounded-full h-2">
                <div className="h-2 rounded-full bg-emerald-500" style={{ width: `${m.win_rate}%` }} />
              </div>
            </div>
            <div className="flex-1">
              <div className="flex items-center justify-between mb-1">
                <span className="text-red-400 text-sm">Losses: {m.losing_trades}</span>
                <span className="text-red-400 text-xs">Avg ${m.avg_loss?.toFixed(2) || '0'}</span>
              </div>
              <div className="w-full bg-slate-700/50 rounded-full h-2">
                <div className="h-2 rounded-full bg-red-500" style={{ width: `${100 - m.win_rate}%` }} />
              </div>
            </div>
          </div>
        </Card>
      </div>

      {/* Monthly Breakdown */}
      {m.monthly?.length > 0 && (
        <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-5">
          <h4 className="text-white text-sm font-semibold mb-3">Monthly Performance</h4>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={m.monthly}>
              <XAxis dataKey="month" tick={axisTick} tickLine={false} axisLine={false}
                tickFormatter={v => v ? v.slice(5) : ''} />
              <YAxis tick={axisTick} tickLine={false} axisLine={false} tickFormatter={v => `$${v}`} />
              <Tooltip contentStyle={chartTooltipStyle} labelStyle={chartLabelStyle}
                formatter={(v, name) => [`$${v.toFixed(2)}`, 'Monthly P&L']} />
              <Bar dataKey="pnl" radius={[4, 4, 0, 0]}>
                {m.monthly.map((entry, i) => (
                  <Cell key={`cell-${i}`} fill={entry.pnl >= 0 ? '#10B981' : '#EF4444'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>
      )}

      {/* Trade Log */}
      {result.trades?.length > 0 && (
        <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl p-5">
          <h4 className="text-white text-sm font-semibold mb-3">
            Trade Log ({result.total_trades_generated > 50 ? `Last 50 of ${result.total_trades_generated}` : result.trades.length} trades)
          </h4>
          <div className="overflow-x-auto max-h-[300px] overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-slate-800">
                <tr className="border-b border-slate-700">
                  <th className="text-left py-2 px-2 text-slate-400 font-medium">Entry</th>
                  <th className="text-left py-2 px-2 text-slate-400 font-medium">Exit</th>
                  <th className="text-right py-2 px-2 text-slate-400 font-medium">Entry $</th>
                  <th className="text-right py-2 px-2 text-slate-400 font-medium">Exit $</th>
                  <th className="text-right py-2 px-2 text-slate-400 font-medium">P&L</th>
                  <th className="text-right py-2 px-2 text-slate-400 font-medium">%</th>
                  <th className="text-left py-2 px-2 text-slate-400 font-medium">Reason</th>
                </tr>
              </thead>
              <tbody>
                {result.trades.map((t, i) => (
                  <tr key={`trade-${i}`} className="border-b border-slate-700/50 hover:bg-slate-700/30">
                    <td className="py-1.5 px-2 text-slate-300">{t.entry_date}</td>
                    <td className="py-1.5 px-2 text-slate-300">{t.exit_date}</td>
                    <td className="py-1.5 px-2 text-right text-white">${t.entry_price}</td>
                    <td className="py-1.5 px-2 text-right text-white">${t.exit_price}</td>
                    <td className={`py-1.5 px-2 text-right font-semibold ${t.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {t.pnl >= 0 ? '+' : ''}${t.pnl}
                    </td>
                    <td className={`py-1.5 px-2 text-right ${t.pnl_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct}%
                    </td>
                    <td className="py-1.5 px-2">
                      <ExitReasonBadge reason={t.exit_reason} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* Best / Worst Trade */}
      {m.best_trade && m.worst_trade && (
        <div className="grid grid-cols-2 gap-3">
          <Card className="bg-emerald-950/20 border-emerald-800/30 rounded-xl p-4">
            <p className="text-[10px] text-slate-500 mb-1">Best Trade</p>
            <p className="text-emerald-400 text-lg font-bold">+${m.best_trade.pnl} ({m.best_trade.pnl_pct}%)</p>
            <p className="text-slate-400 text-[10px]">{m.best_trade.entry_date} — {m.best_trade.holding_days}d hold</p>
          </Card>
          <Card className="bg-red-950/20 border-red-800/30 rounded-xl p-4">
            <p className="text-[10px] text-slate-500 mb-1">Worst Trade</p>
            <p className="text-red-400 text-lg font-bold">${m.worst_trade.pnl} ({m.worst_trade.pnl_pct}%)</p>
            <p className="text-slate-400 text-[10px]">{m.worst_trade.entry_date} — {m.worst_trade.holding_days}d hold</p>
          </Card>
        </div>
      )}

      {/* Disclaimer */}
      <div className="flex items-start gap-2 text-slate-500 text-[10px] bg-slate-800/30 rounded-lg p-3">
        <AlertTriangle className="w-4 h-4 shrink-0 text-amber-500" />
        <span>Past performance does not guarantee future results. This backtest uses simplified assumptions (single position, no slippage, no commissions). Use as directional guidance only.</span>
      </div>
    </div>
  );
};

const ExitReasonBadge = ({ reason }) => {
  const styles = {
    signal: 'bg-blue-900/30 text-blue-400 border-blue-700/40',
    stop_loss: 'bg-red-900/30 text-red-400 border-red-700/40',
    take_profit: 'bg-emerald-900/30 text-emerald-400 border-emerald-700/40',
    open: 'bg-amber-900/30 text-amber-400 border-amber-700/40',
  };
  const labels = { signal: 'Signal', stop_loss: 'Stop Loss', take_profit: 'Take Profit', open: 'Still Open' };
  return <Badge className={`text-[8px] px-1.5 py-0 border ${styles[reason] || 'bg-slate-700 text-slate-400'}`}>{labels[reason] || reason}</Badge>;
};

export default BacktestResults;
