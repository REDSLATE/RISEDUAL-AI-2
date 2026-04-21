import React, { useState, useCallback } from 'react';
import { X, Calculator, Shield, Target, TrendingUp, DollarSign, AlertTriangle, BarChart3, ArrowRight, Layers } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { toast } from './ui/sonner';
import { authFetch } from '../contexts/AuthContext';
import PanelShell from './PanelShell';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api/risk-calc`;

const RRGauge = ({ ratio }) => {
  const clamped = Math.min(ratio, 5);
  const pct = (clamped / 5) * 100;
  const color = ratio >= 3 ? '#10B981' : ratio >= 2 ? '#3DE8D9' : ratio >= 1 ? '#F59E0B' : '#EF4444';
  return (
    <div className="flex flex-col items-center gap-1" data-testid="rr-gauge">
      <div className="relative w-32 h-16 overflow-hidden">
        <div className="absolute inset-0 rounded-t-full border-4 border-slate-700" />
        <div className="absolute bottom-0 left-1/2 w-1 h-14 origin-bottom" style={{ transform: `rotate(${-90 + pct * 1.8}deg)`, background: color }} />
        <div className="absolute bottom-0 left-1/2 -translate-x-1/2 w-3 h-3 rounded-full" style={{ background: color }} />
      </div>
      <span className="text-2xl font-black" style={{ color }}>{ratio > 0 ? `1:${ratio}` : 'N/A'}</span>
      <span className="text-slate-500 text-[9px]">Risk : Reward</span>
    </div>
  );
};

const StatBlock = ({ label, value, sub, color = 'text-white', icon: Icon }) => (
  <div className="bg-slate-800/60 rounded-lg p-2.5 text-center">
    <div className="flex items-center justify-center gap-1 mb-0.5">
      {Icon && <Icon className={`w-3 h-3 ${color}`} />}
      <span className="text-slate-500 text-[9px] uppercase">{label}</span>
    </div>
    <p className={`text-sm font-bold ${color}`}>{value}</p>
    {sub && <p className="text-slate-600 text-[8px]">{sub}</p>}
  </div>
);

const RiskCalculator = ({ onClose, onApplyToSmartOrder }) => {
  const [symbol, setSymbol] = useState('');
  const [side, setSide] = useState('buy');
  const [entry, setEntry] = useState('');
  const [sl, setSl] = useState('');
  const [tp, setTp] = useState('');
  const [method, setMethod] = useState('risk_pct');
  const [riskPct, setRiskPct] = useState('2');
  const [fixedDollar, setFixedDollar] = useState('');
  const [winRate, setWinRate] = useState('55');
  const [wlRatio, setWlRatio] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  const calculate = useCallback(async () => {
    if (!symbol || !sl || !tp) return toast.error('Symbol, SL, and TP required');
    setLoading(true);
    try {
      const body = {
        symbol: symbol.toUpperCase(), side,
        stop_loss_price: parseFloat(sl), take_profit_price: parseFloat(tp),
        sizing_method: method, risk_pct: parseFloat(riskPct) || 2,
      };
      if (entry) body.entry_price = parseFloat(entry);
      if (method === 'fixed_dollar' && fixedDollar) body.fixed_dollar_risk = parseFloat(fixedDollar);
      if (method === 'kelly') {
        if (winRate) body.win_rate = parseFloat(winRate);
        if (wlRatio) body.avg_win_loss_ratio = parseFloat(wlRatio);
      }
      const res = await authFetch(`${API}/calculate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const data = await res.json();
      if (res.ok) setResult(data);
      else toast.error(data.detail || 'Calculation failed');
    } catch { toast.error('Calculation error'); }
    finally { setLoading(false); }
  }, [symbol, side, entry, sl, tp, method, riskPct, fixedDollar, winRate, wlRatio]);

  const applyToOrder = () => {
    if (!result || !onApplyToSmartOrder) return;
    onApplyToSmartOrder({
      symbol: result.symbol, side: result.side, qty: result.position_size,
      entry_price: result.entry_price, stop_loss_price: result.stop_loss_price,
      take_profit_price: result.take_profit_price,
    });
    toast.success('Applied to Smart Order');
  };

  return (
    <PanelShell onClose={onClose} testId="risk-calculator" maxWidth="max-w-lg">
      <div className="w-full max-h-[90vh] overflow-y-auto bg-[#0B1426] border border-slate-600/30 rounded-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-400/20">
          <div className="flex items-center gap-2">
            <Calculator className="w-5 h-5 text-[#3DE8D9]" />
            <h2 className="text-white font-bold text-base">Risk Calculator</h2>
          </div>
          {onClose && <button onClick={onClose} className="text-slate-400 hover:text-white"><X className="w-5 h-5" /></button>}
        </div>

        <div className="p-5 space-y-4">
          {/* Symbol + Side */}
          <div className="grid grid-cols-3 gap-3">
            <div className="col-span-2">
              <Label className="text-slate-400 text-[10px] mb-1">Symbol</Label>
              <Input value={symbol} onChange={e => setSymbol(e.target.value.toUpperCase())} placeholder="SPY" className="bg-slate-800 border-slate-600 text-white h-9 text-sm" data-testid="risk-calc-symbol" />
            </div>
            <div>
              <Label className="text-slate-400 text-[10px] mb-1">Side</Label>
              <div className="flex gap-1">
                <button onClick={() => setSide('buy')} className={`flex-1 py-1.5 rounded-lg text-xs font-bold ${side === 'buy' ? 'bg-lime-500 text-white' : 'bg-slate-800 text-slate-400'}`} data-testid="risk-calc-side-buy">BUY</button>
                <button onClick={() => setSide('sell')} className={`flex-1 py-1.5 rounded-lg text-xs font-bold ${side === 'sell' ? 'bg-red-500 text-white' : 'bg-slate-800 text-slate-400'}`} data-testid="risk-calc-side-sell">SELL</button>
              </div>
            </div>
          </div>

          {/* Entry / SL / TP */}
          <div className="grid grid-cols-3 gap-3">
            <div>
              <Label className="text-slate-400 text-[10px] mb-1">Entry Price</Label>
              <Input type="number" value={entry} onChange={e => setEntry(e.target.value)} placeholder="Auto" className="bg-slate-800 border-slate-600 text-white h-9 text-sm" data-testid="risk-calc-entry" />
            </div>
            <div>
              <Label className="text-red-400 text-[10px] mb-1">Stop Loss</Label>
              <Input type="number" value={sl} onChange={e => setSl(e.target.value)} placeholder="$0.00" className="bg-slate-800 border-red-800/40 text-white h-9 text-sm" data-testid="risk-calc-sl" />
            </div>
            <div>
              <Label className="text-lime-400 text-[10px] mb-1">Take Profit</Label>
              <Input type="number" value={tp} onChange={e => setTp(e.target.value)} placeholder="$0.00" className="bg-slate-800 border-lime-800/40 text-white h-9 text-sm" data-testid="risk-calc-tp" />
            </div>
          </div>

          {/* Position Sizing Method */}
          <div className="bg-slate-800/50 rounded-xl p-3 border border-slate-600/20">
            <Label className="text-slate-400 text-[10px] mb-2 block">Position Sizing</Label>
            <div className="grid grid-cols-3 gap-2 mb-2">
              {[
                { id: 'risk_pct', label: '% Risk', icon: BarChart3 },
                { id: 'fixed_dollar', label: 'Fixed $', icon: DollarSign },
                { id: 'kelly', label: 'Kelly', icon: TrendingUp },
              ].map(m => (
                <button key={m.id} onClick={() => setMethod(m.id)}
                  className={`flex items-center justify-center gap-1 py-2 rounded-lg text-[10px] font-medium transition-colors ${
                    method === m.id ? 'bg-[#3DE8D9] text-white' : 'bg-slate-900 text-slate-400 hover:text-white'
                  }`} data-testid={`risk-calc-method-${m.id}`}>
                  <m.icon className="w-3 h-3" /> {m.label}
                </button>
              ))}
            </div>
            {method === 'risk_pct' && (
              <div>
                <Label className="text-slate-500 text-[9px]">Risk % of Account</Label>
                <div className="flex items-center gap-2">
                  <Input type="number" value={riskPct} onChange={e => setRiskPct(e.target.value)} className="bg-slate-900 border-slate-700 text-white h-8 text-xs flex-1" data-testid="risk-calc-risk-pct" />
                  <div className="flex gap-1">
                    {[1, 2, 3, 5].map(p => (
                      <button key={p} onClick={() => setRiskPct(String(p))}
                        className={`px-2 py-1 rounded text-[9px] ${parseFloat(riskPct) === p ? 'bg-[#3DE8D9] text-white' : 'bg-slate-800 text-slate-500'}`}>
                        {p}%
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            )}
            {method === 'fixed_dollar' && (
              <div>
                <Label className="text-slate-500 text-[9px]">Max Dollar Risk</Label>
                <Input type="number" value={fixedDollar} onChange={e => setFixedDollar(e.target.value)} placeholder="500" className="bg-slate-900 border-slate-700 text-white h-8 text-xs" />
              </div>
            )}
            {method === 'kelly' && (
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <Label className="text-slate-500 text-[9px]">Win Rate %</Label>
                  <Input type="number" value={winRate} onChange={e => setWinRate(e.target.value)} placeholder="55" className="bg-slate-900 border-slate-700 text-white h-8 text-xs" />
                </div>
                <div>
                  <Label className="text-slate-500 text-[9px]">Avg Win/Loss Ratio</Label>
                  <Input type="number" value={wlRatio} onChange={e => setWlRatio(e.target.value)} placeholder="Auto" className="bg-slate-900 border-slate-700 text-white h-8 text-xs" />
                </div>
              </div>
            )}
          </div>

          {/* Calculate Button */}
          <Button onClick={calculate} disabled={loading || !symbol || !sl || !tp}
            className="w-full bg-[#3DE8D9] text-white hover:bg-[#3DE8D9]/80 h-10 font-bold" data-testid="risk-calc-submit">
            <Calculator className="w-4 h-4 mr-2" /> {loading ? 'Calculating...' : 'Calculate Position'}
          </Button>

          {/* Results */}
          {result && (
            <div className="bg-[#111C30] rounded-xl p-4 border border-[#3DE8D9]/30 space-y-3" data-testid="risk-calc-result">
              {/* R:R Gauge + Core Stats */}
              <div className="flex items-start gap-4">
                <RRGauge ratio={result.risk_reward_ratio} />
                <div className="flex-1 grid grid-cols-2 gap-2">
                  <StatBlock label="Position Size" value={`${result.position_size} shares`} sub={`$${result.total_cost.toLocaleString()}`} icon={BarChart3} color="text-white" />
                  <StatBlock label="Account" value={`$${result.account_value.toLocaleString()}`} sub={`${result.risk_pct_of_account}% at risk`} icon={DollarSign} color="text-slate-300" />
                  <StatBlock label="Max Loss" value={`-$${result.max_loss.toLocaleString()}`} sub={`$${result.risk_per_share}/share`} icon={AlertTriangle} color="text-red-400" />
                  <StatBlock label="Max Profit" value={`+$${result.max_profit.toLocaleString()}`} sub={`$${result.reward_per_share}/share`} icon={Target} color="text-lime-400" />
                </div>
              </div>

              {/* Price Levels Visual */}
              <div className="bg-slate-900/50 rounded-lg p-3">
                <div className="flex items-center justify-between text-[10px] mb-2">
                  <span className="text-red-400 font-bold">SL: ${result.stop_loss_price}</span>
                  <ArrowRight className="w-3 h-3 text-slate-600" />
                  <span className="text-blue-400 font-bold">Entry: ${result.entry_price}</span>
                  <ArrowRight className="w-3 h-3 text-slate-600" />
                  <span className="text-lime-400 font-bold">TP: ${result.take_profit_price}</span>
                </div>
                <div className="relative h-3 bg-slate-800 rounded-full overflow-hidden">
                  {(() => {
                    const range = result.take_profit_price - result.stop_loss_price;
                    const entryPct = ((result.entry_price - result.stop_loss_price) / range) * 100;
                    return (
                      <>
                        <div className="absolute h-full bg-red-500/40 rounded-l-full" style={{ width: `${entryPct}%` }} />
                        <div className="absolute h-full bg-lime-500/40 rounded-r-full" style={{ left: `${entryPct}%`, width: `${100 - entryPct}%` }} />
                        <div className="absolute h-full w-0.5 bg-blue-400" style={{ left: `${entryPct}%` }} />
                      </>
                    );
                  })()}
                </div>
              </div>

              {/* Risk Circuit-Breaker Banner — fires when losing streak or
                  drawdown tripped the auto de-risk in the backend. */}
              {result.risk_adjustment?.risk_reduced && (
                <div
                  className="bg-amber-500/10 border border-amber-500/40 rounded-lg p-2 flex items-start gap-2"
                  data-testid="risk-reduced-banner"
                >
                  <Shield className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                  <div className="flex-1">
                    <div className="text-amber-300 text-[11px] font-semibold">
                      Risk auto-reduced · {result.risk_adjustment.applied_risk_pct}% applied ({result.risk_adjustment.requested_risk_pct}% requested)
                    </div>
                    <div className="text-amber-200/70 text-[10px] mt-0.5">
                      {result.risk_adjustment.reason}
                    </div>
                  </div>
                </div>
              )}

              {/* Trade-Guard Veto Banner — advisory R:R floor failure. */}
              {result.trade_guards?.veto && (
                <div
                  className="bg-red-500/10 border border-red-500/40 rounded-lg p-2 flex items-start gap-2"
                  data-testid="trade-guard-veto-banner"
                >
                  <AlertTriangle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
                  <div className="flex-1">
                    <div className="text-red-300 text-[11px] font-semibold">
                      Trade blocked by R:R guard (min {result.trade_guards.min_rr})
                    </div>
                    <div className="text-red-200/70 text-[10px] mt-0.5">
                      {result.trade_guards.veto_reason}
                    </div>
                  </div>
                </div>
              )}

              {/* Exploration-active pill — shows when ε-greedy overrode veto. */}
              {result.trade_guards?.exploration_active && (
                <div
                  className="bg-violet-500/10 border border-violet-500/40 rounded-lg p-2 flex items-start gap-2"
                  data-testid="exploration-active-banner"
                >
                  <Target className="w-4 h-4 text-violet-400 shrink-0 mt-0.5" />
                  <div className="flex-1">
                    <div className="text-violet-300 text-[11px] font-semibold">
                      ε-greedy exploration sample · veto overridden
                    </div>
                    <div className="text-violet-200/70 text-[10px] mt-0.5">
                      {result.trade_guards.exploration_reason}
                    </div>
                  </div>
                </div>
              )}

              {/* Affordability Warning */}
              {!result.can_afford && (
                <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-2 flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
                  <span className="text-red-300 text-[10px]">Position cost (${result.total_cost.toLocaleString()}) exceeds account value. Reduce position size or risk %.</span>
                </div>
              )}

              {/* Apply to Smart Order */}
              {onApplyToSmartOrder && (
                <Button onClick={applyToOrder} variant="outline" className="w-full bg-violet-900/20 text-violet-400 border-violet-800/50 hover:bg-violet-800/30 h-9 text-xs" data-testid="risk-calc-apply">
                  <Layers className="w-3.5 h-3.5 mr-1.5" /> Apply to Smart Order ({result.position_size} shares)
                </Button>
              )}
            </div>
          )}
        </div>
      </div>
    </PanelShell>
  );
};

export default RiskCalculator;
