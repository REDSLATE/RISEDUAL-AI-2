import React, { useState, useEffect, useCallback } from 'react';
import { X, Plus, TrendingUp, TrendingDown, Target, BarChart3, BookOpen, Trash2, Paperclip, Lock, RefreshCw, ChevronDown, ChevronUp } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { toast } from './ui/sonner';
import { useAuth, authFetch } from '../contexts/AuthContext';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Area, AreaChart } from 'recharts';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

const TradingJournal = ({ onClose, onSubscribe }) => {
  const { user, isPro } = useAuth();
  const [tab, setTab] = useState('trades');
  const [trades, setTrades] = useState([]);
  const [analytics, setAnalytics] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [tradeLimit, setTradeLimit] = useState(-1);
  const [form, setForm] = useState({
    ticker: '', side: 'buy', entry_price: '', quantity: '', entry_date: new Date().toISOString().split('T')[0],
    exit_price: '', exit_date: '', notes: '',
  });
  const [saving, setSaving] = useState(false);
  const [closeForm, setCloseForm] = useState(null);

  // authFetch and API are module-level constants — stable across renders
  const fetchTrades = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authFetch(`${API}/journal/trades`);
      if (res.ok) {
        const data = await res.json();
        setTrades(data.trades);
        setTradeLimit(data.limit);
      }
    } catch (e) { logger.error(e); }
    finally { setLoading(false); }
  }, []);

  const fetchAnalytics = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/journal/analytics`);
      if (res.ok) setAnalytics(await res.json());
    } catch (e) { logger.error(e); }
  }, []);

  useEffect(() => { fetchTrades(); fetchAnalytics(); }, [fetchTrades, fetchAnalytics]);

  const createTrade = async () => {
    if (!form.ticker || !form.entry_price || !form.quantity) return;
    setSaving(true);
    try {
      const body = {
        ...form,
        entry_price: parseFloat(form.entry_price),
        quantity: parseFloat(form.quantity),
        exit_price: form.exit_price ? parseFloat(form.exit_price) : null,
        exit_date: form.exit_date || null,
      };
      const res = await authFetch(`${API}/journal/trade`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      if (res.ok) {
        setShowForm(false);
        setForm({ ticker: '', side: 'buy', entry_price: '', quantity: '', entry_date: new Date().toISOString().split('T')[0], exit_price: '', exit_date: '', notes: '' });
        fetchTrades();
        fetchAnalytics();
      } else {
        const d = await res.json();
        toast.error(d.detail || 'Failed to log trade');
      }
    } catch (e) { toast.error('Error logging trade'); }
    finally { setSaving(false); }
  };

  const closeTrade = async (tradeId) => {
    if (!closeForm?.exit_price) return;
    try {
      const res = await authFetch(`${API}/journal/trade/${tradeId}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ exit_price: parseFloat(closeForm.exit_price), exit_date: closeForm.exit_date || new Date().toISOString().split('T')[0] }),
      });
      if (res.ok) { setCloseForm(null); fetchTrades(); fetchAnalytics(); }
    } catch (e) { logger.error(e); }
  };

  const deleteTrade = async (id) => {
    try {
      await authFetch(`${API}/journal/trade/${id}`, { method: 'DELETE' });
      fetchTrades();
      fetchAnalytics();
    } catch (e) { logger.error(e); }
  };

  const attachHypothesis = async (tradeId) => {
    try {
      const res = await authFetch(`${API}/journal/trade/${tradeId}/attach-hypothesis`, { method: 'POST' });
      if (res.ok) fetchTrades();
      else { const d = await res.json(); alert(d.detail || 'No hypothesis found'); }
    } catch (e) { logger.error(e); }
  };

  const tabs = [
    { id: 'trades', label: 'My Trades', icon: BookOpen },
    { id: 'analytics', label: 'Analytics', icon: BarChart3 },
  ];

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-start justify-center overflow-y-auto p-4" data-testid="trading-journal">
      <div className="bg-slate-900 rounded-2xl max-w-4xl w-full my-4 border border-slate-400/25">
        {/* Header */}
        <div className="p-5 border-b border-slate-400/30 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-gradient-to-br from-[#3DE8D9] to-indigo-600 rounded-xl flex items-center justify-center">
              <BookOpen className="w-5 h-5 text-white" />
            </div>
            <div>
              <h2 className="text-white text-lg font-bold">Trading Journal</h2>
              <p className="text-slate-300 text-xs">{trades.length} trade{trades.length !== 1 ? 's' : ''} logged{tradeLimit > 0 ? ` (${tradeLimit - trades.length} remaining)` : ''}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button size="sm" className="bg-[#3DE8D9] text-white text-xs h-8" onClick={() => setShowForm(!showForm)} data-testid="new-trade-btn"
              disabled={tradeLimit > 0 && trades.length >= tradeLimit}>
              <Plus className="w-3.5 h-3.5 mr-1" /> Log Trade
            </Button>
            <button onClick={onClose} className="text-slate-400 hover:text-white px-2 text-xl">x</button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-slate-400/25 px-4">
          {tabs.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)} data-testid={`journal-tab-${t.id}`}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-all ${
                tab === t.id ? 'text-[#3DE8D9] border-[#3DE8D9]' : 'text-slate-400 border-transparent hover:text-slate-300'
              }`}>
              <t.icon className="w-4 h-4" /> {t.label}
            </button>
          ))}
        </div>

        <div className="p-4">
          {/* Trade limit warning */}
          {tradeLimit > 0 && trades.length >= tradeLimit && (
            <div className="flex items-center gap-2 bg-amber-900/20 border border-amber-800/40 rounded-xl px-4 py-2.5 mb-4" data-testid="trade-limit-warning">
              <Lock className="w-4 h-4 text-amber-300" />
              <p className="text-amber-300 text-xs flex-1">Free limit reached ({tradeLimit} trades). Upgrade to Pro for unlimited.</p>
              <Button size="sm" className="bg-[#3DE8D9] text-white text-xs h-7 px-3" onClick={onSubscribe}>Upgrade</Button>
            </div>
          )}

          {/* New Trade Form */}
          {showForm && (
            <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-4 mb-4 space-y-3" data-testid="trade-form">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div>
                  <label className="text-slate-400 text-[10px] block mb-1">Ticker</label>
                  <Input placeholder="AAPL" value={form.ticker} onChange={e => setForm({...form, ticker: e.target.value})}
                    className="bg-slate-900 border-slate-600 text-white text-sm h-9 rounded-xl" data-testid="trade-ticker" />
                </div>
                <div>
                  <label className="text-slate-400 text-[10px] block mb-1">Side</label>
                  <select value={form.side} onChange={e => setForm({...form, side: e.target.value})}
                    className="w-full bg-slate-900 border border-slate-600 text-white text-sm h-9 rounded-xl px-2" data-testid="trade-side">
                    <option value="buy">Buy (Long)</option>
                    <option value="sell">Sell (Short)</option>
                  </select>
                </div>
                <div>
                  <label className="text-slate-400 text-[10px] block mb-1">Entry Price</label>
                  <Input type="number" step="0.01" placeholder="150.00" value={form.entry_price} onChange={e => setForm({...form, entry_price: e.target.value})}
                    className="bg-slate-900 border-slate-600 text-white text-sm h-9 rounded-xl" data-testid="trade-entry" />
                </div>
                <div>
                  <label className="text-slate-400 text-[10px] block mb-1">Quantity</label>
                  <Input type="number" step="0.01" placeholder="10" value={form.quantity} onChange={e => setForm({...form, quantity: e.target.value})}
                    className="bg-slate-900 border-slate-600 text-white text-sm h-9 rounded-xl" data-testid="trade-qty" />
                </div>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div>
                  <label className="text-slate-400 text-[10px] block mb-1">Entry Date</label>
                  <Input type="date" value={form.entry_date} onChange={e => setForm({...form, entry_date: e.target.value})}
                    className="bg-slate-900 border-slate-600 text-white text-sm h-9 rounded-xl" />
                </div>
                <div>
                  <label className="text-slate-400 text-[10px] block mb-1">Exit Price (opt.)</label>
                  <Input type="number" step="0.01" placeholder="—" value={form.exit_price} onChange={e => setForm({...form, exit_price: e.target.value})}
                    className="bg-slate-900 border-slate-600 text-white text-sm h-9 rounded-xl" />
                </div>
                <div>
                  <label className="text-slate-400 text-[10px] block mb-1">Exit Date (opt.)</label>
                  <Input type="date" value={form.exit_date} onChange={e => setForm({...form, exit_date: e.target.value})}
                    className="bg-slate-900 border-slate-600 text-white text-sm h-9 rounded-xl" />
                </div>
                <div>
                  <label className="text-slate-400 text-[10px] block mb-1">Notes</label>
                  <Input placeholder="Why I took this trade..." value={form.notes} onChange={e => setForm({...form, notes: e.target.value})}
                    className="bg-slate-900 border-slate-600 text-white text-sm h-9 rounded-xl" />
                </div>
              </div>
              <div className="flex justify-end gap-2">
                <Button size="sm" variant="outline" className="text-xs h-8 bg-slate-700 text-slate-300 border-slate-600" onClick={() => setShowForm(false)}>Cancel</Button>
                <Button size="sm" className="text-xs h-8 bg-[#3DE8D9] text-white" onClick={createTrade} disabled={saving || !form.ticker || !form.entry_price || !form.quantity} data-testid="trade-submit">
                  {saving ? 'Saving...' : 'Log Trade'}
                </Button>
              </div>
            </Card>
          )}

          {tab === 'trades' ? (
            <TradesTab trades={trades} loading={loading} onDelete={deleteTrade} onClose={closeTrade} onAttach={attachHypothesis}
              closeForm={closeForm} setCloseForm={setCloseForm} />
          ) : (
            <AnalyticsTab analytics={analytics} />
          )}
        </div>
      </div>
    </div>
  );
};

const TradesTab = ({ trades, loading, onDelete, onClose, onAttach, closeForm, setCloseForm }) => {
  if (loading) return <div className="flex justify-center py-12"><RefreshCw className="w-6 h-6 text-[#3DE8D9] animate-spin" /></div>;
  if (trades.length === 0) return (
    <div className="text-center py-12">
      <BookOpen className="w-12 h-12 text-slate-400 mx-auto mb-3" />
      <p className="text-slate-300 text-sm">No trades logged yet</p>
      <p className="text-slate-300 text-xs mt-1">Click "Log Trade" to start tracking your performance</p>
    </div>
  );

  return (
    <div className="space-y-2 max-h-[400px] overflow-y-auto">
      {trades.map(t => (
        <div key={t.id} className="bg-slate-800/60 border border-slate-400/30/40 rounded-xl px-4 py-3" data-testid={`trade-${t.id}`}>
          <div className="flex items-center justify-between mb-1">
            <div className="flex items-center gap-2">
              {t.side === 'buy' ? <TrendingUp className="w-4 h-4 text-lime-400" /> : <TrendingDown className="w-4 h-4 text-orange-400" />}
              <span className="text-white font-semibold text-sm">{t.ticker}</span>
              <Badge className={`text-[9px] ${t.side === 'buy' ? 'bg-lime-700 text-lime-400 border-emerald-700/50' : 'bg-orange-800 text-orange-400 border-red-700/50'}`}>
                {t.side.toUpperCase()}
              </Badge>
              <Badge className={`text-[9px] ${t.status === 'closed' ? 'bg-slate-700 text-slate-400' : 'bg-[#3DE8D9]/20 text-[#3DE8D9] border-[#3DE8D9]/30'}`}>
                {t.status.toUpperCase()}
              </Badge>
            </div>
            <div className="flex items-center gap-1.5">
              {t.status === 'closed' && (
                <span className={`text-sm font-bold ${t.pnl >= 0 ? 'text-lime-400' : 'text-orange-400'}`}>
                  {t.pnl >= 0 ? '+' : ''}{t.pnl.toFixed(2)} ({t.pnl_percent >= 0 ? '+' : ''}{t.pnl_percent.toFixed(1)}%)
                </span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-4 text-[10px] text-slate-400">
            <span>Entry: ${t.entry_price?.toFixed(2)}</span>
            {t.exit_price && <span>Exit: ${t.exit_price.toFixed(2)}</span>}
            <span>Qty: {t.quantity}</span>
            <span>{t.entry_date}</span>
          </div>
          {t.notes && <p className="text-slate-300 text-xs mt-1 italic">"{t.notes}"</p>}
          {t.hypothesis && (
            <div className="mt-2 bg-slate-900/50 rounded-lg px-3 py-2 border border-slate-400/30/30">
              <p className="text-[10px] text-slate-400 mb-0.5">AI Hypothesis</p>
              <p className="text-xs text-white font-medium">{t.hypothesis.verdict} ({t.hypothesis.confidence}%)</p>
              {t.hypothesis.summary && <p className="text-[10px] text-slate-400 mt-0.5 line-clamp-2">{t.hypothesis.summary}</p>}
            </div>
          )}
          <div className="flex items-center gap-2 mt-2">
            {t.status === 'open' && (
              closeForm?.id === t.id ? (
                <div className="flex items-center gap-2">
                  <Input type="number" step="0.01" placeholder="Exit price" value={closeForm.exit_price || ''} onChange={e => setCloseForm({...closeForm, exit_price: e.target.value})}
                    className="bg-slate-900 border-slate-600 text-white text-xs h-7 w-28 rounded-lg" />
                  <Input type="date" value={closeForm.exit_date || ''} onChange={e => setCloseForm({...closeForm, exit_date: e.target.value})}
                    className="bg-slate-900 border-slate-600 text-white text-xs h-7 w-32 rounded-lg" />
                  <Button size="sm" className="text-[10px] h-7 px-2 bg-green-600 text-white" onClick={() => onClose(t.id)}>Close</Button>
                  <Button size="sm" variant="ghost" className="text-[10px] h-7 px-2 text-slate-400" onClick={() => setCloseForm(null)}>Cancel</Button>
                </div>
              ) : (
                <Button size="sm" variant="outline" className="text-[10px] h-6 px-2 bg-slate-700 text-slate-400 border-slate-600" onClick={() => setCloseForm({id: t.id, exit_price: '', exit_date: new Date().toISOString().split('T')[0]})}>
                  <Target className="w-3 h-3 mr-1" /> Close Trade
                </Button>
              )
            )}
            {!t.hypothesis && (
              <Button size="sm" variant="outline" className="text-[10px] h-6 px-2 bg-indigo-900/30 text-indigo-400 border-indigo-700/50" onClick={() => onAttach(t.id)}>
                <Paperclip className="w-3 h-3 mr-1" /> Attach AI Hypothesis
              </Button>
            )}
            <Button size="sm" variant="ghost" className="text-[10px] h-6 px-2 text-orange-400 hover:text-orange-300 ml-auto" onClick={() => onDelete(t.id)}>
              <Trash2 className="w-3 h-3" />
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
};

const AnalyticsTab = ({ analytics }) => {
  if (!analytics || analytics.closed_trades === 0) return (
    <div className="text-center py-12">
      <BarChart3 className="w-12 h-12 text-slate-400 mx-auto mb-3" />
      <p className="text-slate-300 text-sm">No closed trades yet</p>
      <p className="text-slate-300 text-xs mt-1">Close some trades to see your performance analytics</p>
    </div>
  );

  const a = analytics;
  const stats = [
    { label: 'Total P&L', value: `$${a.total_pnl.toFixed(2)}`, color: a.total_pnl >= 0 ? 'text-lime-400' : 'text-orange-400' },
    { label: 'Win Rate', value: `${a.win_rate}%`, color: a.win_rate >= 50 ? 'text-lime-400' : 'text-orange-400' },
    { label: 'Avg Gain', value: `$${a.avg_gain.toFixed(2)}`, color: 'text-lime-400' },
    { label: 'Avg Loss', value: `$${a.avg_loss.toFixed(2)}`, color: 'text-orange-400' },
    { label: 'Open', value: a.open_trades, color: 'text-[#3DE8D9]' },
    { label: 'Closed', value: a.closed_trades, color: 'text-white' },
  ];

  const chartTooltipStyle = { backgroundColor: '#1E293B', border: '1px solid #334155', borderRadius: '8px', fontSize: '12px' };
  const chartLabelStyle = { color: '#94A3B8' };
  const xAxisTick = { fill: '#64748B', fontSize: 10 };
  const yAxisTick = { fill: '#64748B', fontSize: 10 };

  return (
    <div className="space-y-4">
      {/* Stats Grid */}
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
        {stats.map(s => (
          <Card key={s.label} className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-3 text-center">
            <p className={`text-lg font-bold ${s.color}`}>{s.value}</p>
            <p className="text-slate-400 text-[10px]">{s.label}</p>
          </Card>
        ))}
      </div>

      {/* P&L Timeline Chart */}
      {a.pnl_timeline.length > 0 && (
        <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-4">
          <h4 className="text-white text-sm font-semibold mb-3">Cumulative P&L</h4>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={a.pnl_timeline}>
              <defs>
                <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#3DE8D9" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#3DE8D9" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="date" tick={xAxisTick} tickLine={false} axisLine={false}
                tickFormatter={v => v ? new Date(v).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : ''} />
              <YAxis tick={yAxisTick} tickLine={false} axisLine={false} tickFormatter={v => `$${v}`} />
              <Tooltip contentStyle={chartTooltipStyle}
                labelStyle={chartLabelStyle} formatter={(v) => [`$${v.toFixed(2)}`, 'Cumulative P&L']} />
              <Area type="monotone" dataKey="cumulative" stroke="#3DE8D9" fill="url(#pnlGrad)" strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </Card>
      )}

      {/* Best/Worst */}
      <div className="grid grid-cols-2 gap-3">
        {a.best_trade && (
          <Card className="bg-emerald-900/10 border-emerald-700/30 rounded-xl p-3">
            <p className="text-[10px] text-slate-400 mb-1">Best Trade</p>
            <p className="text-lime-400 text-lg font-bold">{a.best_trade.ticker}</p>
            <p className="text-lime-300 text-xs">+${a.best_trade.pnl.toFixed(2)} ({a.best_trade.pnl_percent > 0 ? '+' : ''}{a.best_trade.pnl_percent.toFixed(1)}%)</p>
          </Card>
        )}
        {a.worst_trade && (
          <Card className="bg-red-500/15 border-red-700/30 rounded-xl p-3">
            <p className="text-[10px] text-slate-400 mb-1">Worst Trade</p>
            <p className="text-orange-400 text-lg font-bold">{a.worst_trade.ticker}</p>
            <p className="text-orange-300 text-xs">${a.worst_trade.pnl.toFixed(2)} ({a.worst_trade.pnl_percent.toFixed(1)}%)</p>
          </Card>
        )}
      </div>

      {/* By Ticker */}
      {Object.keys(a.by_ticker).length > 0 && (
        <TickerPerformance byTicker={a.by_ticker} />
      )}
    </div>
  );
};

const TickerPerformance = ({ byTicker }) => {
  const sortedEntries = useMemo(() =>
    Object.entries(byTicker).sort((a, b) => b[1].pnl - a[1].pnl),
    [byTicker]
  );

  return (
    <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-4">
      <h4 className="text-white text-sm font-semibold mb-2">Performance by Ticker</h4>
      <div className="space-y-1.5">
        {sortedEntries.map(([ticker, data]) => (
          <div key={ticker} className="flex items-center justify-between bg-slate-900/50 rounded-lg px-3 py-2">
            <div className="flex items-center gap-2">
              <span className="text-white text-sm font-medium">{ticker}</span>
              <span className="text-slate-400 text-[10px]">{data.trades} trades ({data.wins}W)</span>
            </div>
            <span className={`text-sm font-bold ${data.pnl >= 0 ? 'text-lime-400' : 'text-orange-400'}`}>
              {data.pnl >= 0 ? '+' : ''}${data.pnl.toFixed(2)}
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
};

export default TradingJournal;
