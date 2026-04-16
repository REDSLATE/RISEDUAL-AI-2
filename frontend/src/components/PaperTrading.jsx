import React, { useState, useEffect, useCallback } from 'react';
import {
  X, DollarSign, TrendingUp, TrendingDown, RefreshCw,
  ArrowUpRight, ArrowDownRight, RotateCcw, History, Briefcase
} from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { useAuth, authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';
import logger from '../utils/logger';
import { toast } from 'sonner';

const API = `${getApiBase()}/api`;

import PanelShell from './PanelShell';

const PaperTrading = ({ onClose }) => {
  const { user } = useAuth();
  const [portfolio, setPortfolio] = useState(null);
  const [trades, setTrades] = useState([]);
  const [loading, setLoading] = useState(true);
  const [tradeLoading, setTradeLoading] = useState(false);
  const [tab, setTab] = useState('portfolio');
  const [symbol, setSymbol] = useState('');
  const [qty, setQty] = useState('');
  const [side, setSide] = useState('BUY');

  const fetchPortfolio = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/paper/portfolio`);
      if (res.ok) setPortfolio(await res.json());
    } catch (e) { logger.warn('Paper trading fetch failed:', e); }
  }, []);

  const fetchTrades = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/paper/trades?limit=30`);
      if (res.ok) setTrades(await res.json());
    } catch (e) { logger.warn('Paper trading fetch failed:', e); }
  }, []);

  useEffect(() => {
    Promise.all([fetchPortfolio(), fetchTrades()]).then(() => setLoading(false));
  }, [fetchPortfolio, fetchTrades]);

  const executeTrade = async () => {
    if (!symbol.trim() || !qty || parseFloat(qty) <= 0) return;
    setTradeLoading(true);
    try {
      const res = await authFetch(`${API}/paper/trade`, {
        method: 'POST',
        body: JSON.stringify({ symbol: symbol.toUpperCase(), side, qty: parseFloat(qty) }),
      });
      const data = await res.json();
      if (!res.ok) {
        toast.error(data.detail || 'Trade failed');
        return;
      }
      toast.success(`${side} ${qty} ${symbol.toUpperCase()} @ $${data.price}`);
      setSymbol('');
      setQty('');
      await Promise.all([fetchPortfolio(), fetchTrades()]);
    } catch (e) {
      toast.error(e.message);
    } finally {
      setTradeLoading(false);
    }
  };

  const resetPortfolio = async () => {
    if (!window.confirm('Reset your paper portfolio to $100,000? All positions and trade history will be cleared.')) return;
    try {
      const res = await authFetch(`${API}/paper/reset`, { method: 'POST' });
      if (res.ok) {
        toast.success('Portfolio reset to $100,000');
        await Promise.all([fetchPortfolio(), fetchTrades()]);
      }
    } catch (e) {
      toast.error('Reset failed');
    }
  };

  const pnlColor = (val) => val > 0 ? 'text-lime-400' : val < 0 ? 'text-orange-400' : 'text-slate-400';
  const pnlBg = (val) => val > 0 ? 'bg-green-600 border-lime-700/30' : val < 0 ? 'bg-orange-900 border-orange-700/30' : 'bg-slate-700/60 border-slate-400/30/40';

  return (
    <PanelShell onClose={onClose} testId="paper-trading-modal" maxWidth="max-w-3xl">
      <div className="bg-[#0F1A2E] border border-slate-400/25 rounded-2xl w-full max-h-[85vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-slate-400/25">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-gradient-to-br from-emerald-600 to-teal-500 rounded-xl flex items-center justify-center">
              <Briefcase className="w-5 h-5 text-white" />
            </div>
            <div>
              <h2 className="text-white text-lg font-bold" data-testid="paper-trading-title">Paper Trading</h2>
              <p className="text-slate-300 text-xs">Simulated portfolio with live market prices</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" className="text-slate-400 hover:text-white" onClick={resetPortfolio} data-testid="paper-reset-btn">
              <RotateCcw className="w-4 h-4 mr-1" /> Reset
            </Button>
            {onClose && <button onClick={onClose} className="text-slate-400 hover:text-white p-1" data-testid="paper-close-btn">
              <X className="w-5 h-5" />
            </button>}
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 px-5 pt-3">
          {[
            { key: 'portfolio', label: 'Portfolio', icon: Briefcase },
            { key: 'trade', label: 'Trade', icon: ArrowUpRight },
            { key: 'history', label: 'History', icon: History },
          ].map(t => (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium transition-all ${tab === t.key ? 'bg-teal-500/20 text-teal-400 border border-teal-500/30' : 'text-slate-400 hover:text-white hover:bg-slate-600/30/60'}`}
              data-testid={`paper-tab-${t.key}`}>
              <t.icon className="w-3.5 h-3.5" /> {t.label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {loading ? (
            <div className="text-center py-12">
              <RefreshCw className="w-6 h-6 text-teal-400 mx-auto animate-spin" />
              <p className="text-slate-300 text-sm mt-3">Loading portfolio...</p>
            </div>
          ) : tab === 'portfolio' ? (
            <>
              {/* Summary Cards */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <Card className="bg-slate-700/60 border-slate-400/30/30 rounded-xl p-4">
                  <p className="text-slate-400 text-[10px] uppercase tracking-wider">Equity</p>
                  <p className="text-white text-lg font-bold" data-testid="paper-equity">${portfolio?.equity?.toLocaleString()}</p>
                </Card>
                <Card className="bg-slate-700/60 border-slate-400/30/30 rounded-xl p-4">
                  <p className="text-slate-400 text-[10px] uppercase tracking-wider">Cash</p>
                  <p className="text-white text-lg font-bold" data-testid="paper-cash">${portfolio?.cash?.toLocaleString()}</p>
                </Card>
                <Card className={`border rounded-xl p-4 ${pnlBg(portfolio?.total_pnl)}`}>
                  <p className="text-slate-400 text-[10px] uppercase tracking-wider">Total P&L</p>
                  <p className={`text-lg font-bold ${pnlColor(portfolio?.total_pnl)}`} data-testid="paper-pnl">
                    ${portfolio?.total_pnl > 0 ? '+' : ''}{portfolio?.total_pnl?.toLocaleString()}
                  </p>
                </Card>
                <Card className={`border rounded-xl p-4 ${pnlBg(portfolio?.total_pnl_pct)}`}>
                  <p className="text-slate-400 text-[10px] uppercase tracking-wider">Return %</p>
                  <p className={`text-lg font-bold ${pnlColor(portfolio?.total_pnl_pct)}`} data-testid="paper-return">
                    {portfolio?.total_pnl_pct > 0 ? '+' : ''}{portfolio?.total_pnl_pct}%
                  </p>
                </Card>
              </div>

              {/* Positions */}
              <div>
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-white text-sm font-semibold">Positions ({portfolio?.position_count || 0})</h3>
                  <Button variant="ghost" size="sm" className="text-slate-400 hover:text-white h-7 text-xs" onClick={fetchPortfolio} data-testid="paper-refresh-btn">
                    <RefreshCw className="w-3 h-3 mr-1" /> Refresh Prices
                  </Button>
                </div>
                {portfolio?.positions?.length > 0 ? (
                  <div className="space-y-2">
                    {portfolio.positions.map(p => (
                      <Card key={p.symbol} className={`border rounded-xl p-4 ${pnlBg(p.unrealized_pnl)}`} data-testid={`position-${p.symbol}`}>
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-3">
                            <div className="w-8 h-8 bg-slate-700/60 rounded-lg flex items-center justify-center">
                              <span className="text-white text-xs font-bold">{p.symbol.slice(0, 2)}</span>
                            </div>
                            <div>
                              <span className="text-white text-sm font-semibold">{p.symbol}</span>
                              <p className="text-slate-400 text-[10px]">{p.qty} shares @ ${p.avg_cost.toFixed(2)}</p>
                            </div>
                          </div>
                          <div className="text-right">
                            <p className="text-white text-sm font-medium">${p.market_value.toLocaleString()}</p>
                            <p className={`text-xs font-medium ${pnlColor(p.unrealized_pnl)}`}>
                              {p.unrealized_pnl > 0 ? <TrendingUp className="w-3 h-3 inline mr-0.5" /> : p.unrealized_pnl < 0 ? <TrendingDown className="w-3 h-3 inline mr-0.5" /> : null}
                              ${p.unrealized_pnl > 0 ? '+' : ''}{p.unrealized_pnl.toLocaleString()} ({p.unrealized_pnl_pct > 0 ? '+' : ''}{p.unrealized_pnl_pct}%)
                            </p>
                          </div>
                        </div>
                      </Card>
                    ))}
                  </div>
                ) : (
                  <Card className="bg-slate-700/55 border-slate-400/30/30 rounded-xl p-8 text-center">
                    <DollarSign className="w-8 h-8 text-slate-400 mx-auto mb-2" />
                    <p className="text-slate-300 text-sm">No positions yet</p>
                    <p className="text-slate-300 text-xs mt-1">Use the Trade tab to buy your first paper stock</p>
                  </Card>
                )}
              </div>
            </>
          ) : tab === 'trade' ? (
            <div className="space-y-4">
              <Card className="bg-slate-700/60 border-slate-400/30/30 rounded-xl p-5">
                <h3 className="text-white text-sm font-semibold mb-4">Execute Paper Trade</h3>
                <div className="space-y-3">
                  {/* Side Toggle */}
                  <div className="flex gap-2">
                    <button onClick={() => setSide('BUY')}
                      className={`flex-1 py-2.5 rounded-lg text-sm font-semibold transition-all ${side === 'BUY' ? 'bg-green-500/20 text-lime-400 border border-emerald-500/40' : 'bg-slate-800 text-slate-400 border border-slate-400/25'}`}
                      data-testid="paper-buy-toggle">
                      <ArrowUpRight className="w-4 h-4 inline mr-1" /> BUY
                    </button>
                    <button onClick={() => setSide('SELL')}
                      className={`flex-1 py-2.5 rounded-lg text-sm font-semibold transition-all ${side === 'SELL' ? 'bg-red-500/20 text-orange-400 border border-red-500/40' : 'bg-slate-800 text-slate-400 border border-slate-400/25'}`}
                      data-testid="paper-sell-toggle">
                      <ArrowDownRight className="w-4 h-4 inline mr-1" /> SELL
                    </button>
                  </div>
                  {/* Symbol */}
                  <div>
                    <label className="text-slate-400 text-[10px] uppercase tracking-wider mb-1 block">Symbol</label>
                    <Input value={symbol} onChange={e => setSymbol(e.target.value.toUpperCase())}
                      placeholder="AAPL, NVDA, BTC..."
                      className="bg-slate-900 border-slate-600 text-white rounded-lg"
                      data-testid="paper-symbol-input" />
                  </div>
                  {/* Quantity */}
                  <div>
                    <label className="text-slate-400 text-[10px] uppercase tracking-wider mb-1 block">Quantity</label>
                    <Input type="number" value={qty} onChange={e => setQty(e.target.value)}
                      placeholder="Number of shares"
                      className="bg-slate-900 border-slate-600 text-white rounded-lg"
                      data-testid="paper-qty-input" />
                  </div>
                  {/* Cash Available */}
                  <div className="text-slate-300 text-xs">
                    Cash available: <span className="text-white font-medium">${portfolio?.cash?.toLocaleString()}</span>
                  </div>
                  {/* Execute */}
                  <Button onClick={executeTrade} disabled={tradeLoading || !symbol.trim() || !qty}
                    className={`w-full rounded-lg py-2.5 font-semibold ${side === 'BUY' ? 'bg-green-600 hover:bg-green-500' : 'bg-red-600 hover:bg-red-500'} text-white`}
                    data-testid="paper-execute-btn">
                    {tradeLoading ? <RefreshCw className="w-4 h-4 animate-spin mr-2" /> : null}
                    {side} {symbol || '...'} {qty ? `x ${qty}` : ''}
                  </Button>
                </div>
              </Card>

              {/* Quick positions for sell */}
              {side === 'SELL' && portfolio?.positions?.length > 0 && (
                <Card className="bg-slate-700/60 border-slate-400/30/30 rounded-xl p-4">
                  <p className="text-slate-300 text-xs font-medium mb-2">Quick Sell — Tap a position:</p>
                  <div className="flex flex-wrap gap-2">
                    {portfolio.positions.map(p => (
                      <button key={p.symbol} onClick={() => { setSymbol(p.symbol); setQty(String(p.qty)); }}
                        className="px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-400/25 text-white text-xs hover:border-red-500/40 transition-colors"
                        data-testid={`quick-sell-${p.symbol}`}>
                        {p.symbol} ({p.qty})
                      </button>
                    ))}
                  </div>
                </Card>
              )}
            </div>
          ) : (
            /* Trade History */
            <div className="space-y-2">
              {trades.length > 0 ? trades.map((t) => (
                <Card key={`${t.symbol}-${t.side}-${t.timestamp}`} className="bg-slate-700/55 border-slate-400/30/30 rounded-xl p-3 flex items-center justify-between" data-testid={`trade-${t.symbol}-${t.side}`}>
                  <div className="flex items-center gap-3">
                    <div className={`w-7 h-7 rounded-lg flex items-center justify-center ${t.side === 'BUY' ? 'bg-lime-600' : 'bg-orange-700'}`}>
                      {t.side === 'BUY' ? <ArrowUpRight className="w-4 h-4 text-lime-400" /> : <ArrowDownRight className="w-4 h-4 text-orange-400" />}
                    </div>
                    <div>
                      <span className={`text-xs font-semibold ${t.side === 'BUY' ? 'text-lime-400' : 'text-orange-400'}`}>{t.side}</span>
                      <span className="text-white text-sm font-medium ml-2">{t.symbol}</span>
                      <p className="text-slate-400 text-[10px]">{t.qty} shares @ ${t.price}</p>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="text-white text-sm font-medium">${t.total.toLocaleString()}</p>
                    <p className="text-slate-400 text-[10px]">{new Date(t.timestamp).toLocaleDateString()}</p>
                  </div>
                </Card>
              )) : (
                <Card className="bg-slate-700/55 border-slate-400/30/30 rounded-xl p-8 text-center">
                  <History className="w-8 h-8 text-slate-400 mx-auto mb-2" />
                  <p className="text-slate-300 text-sm">No trades yet</p>
                </Card>
              )}
            </div>
          )}
        </div>
      </div>
    </PanelShell>
  );
};

export default PaperTrading;
