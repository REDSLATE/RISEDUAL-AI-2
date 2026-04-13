import React, { useState, useCallback, useEffect } from 'react';
import { X, Layers, Shield, Target, TrendingUp, Zap, BarChart3, Play, Eye, Trash2, ChevronDown, ChevronUp, Plus, Minus } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Badge } from './ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { toast } from './ui/sonner';
import { useAuth, authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api/smart-orders`;

const ModeTag = ({ mode }) => {
  const colors = { paper: 'bg-lime-500/15 text-lime-400', live: 'bg-red-500/15 text-red-400', simulate: 'bg-blue-500/15 text-blue-400' };
  return <Badge className={`text-[9px] ${colors[mode] || 'bg-slate-700 text-slate-400'}`}>{mode.toUpperCase()}</Badge>;
};

const StatusTag = ({ status }) => {
  const colors = {
    pending: 'bg-amber-500/15 text-amber-400', filled: 'bg-lime-500/15 text-lime-400',
    partially_filled: 'bg-blue-500/15 text-blue-400', stopped: 'bg-red-500/15 text-red-400',
    cancelled: 'bg-slate-600 text-slate-400', completed: 'bg-[#3DE8D9]/15 text-[#3DE8D9]',
  };
  return <Badge className={`text-[9px] ${colors[status] || 'bg-slate-700 text-slate-400'}`}>{status?.replace('_', ' ').toUpperCase()}</Badge>;
};

const SmartOrderPanel = ({ onClose }) => {
  const { user, isPro } = useAuth();
  const [orders, setOrders] = useState([]);
  const [view, setView] = useState('create');
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState(null);
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Form state
  const [symbol, setSymbol] = useState('');
  const [side, setSide] = useState('buy');
  const [qty, setQty] = useState('');
  const [mode, setMode] = useState('paper');
  const [orderType, setOrderType] = useState('market');
  const [entryPrice, setEntryPrice] = useState('');

  // SL/TP
  const [slEnabled, setSlEnabled] = useState(false);
  const [slPrice, setSlPrice] = useState('');
  const [slTrailing, setSlTrailing] = useState(false);
  const [slTrailingPct, setSlTrailingPct] = useState('2');
  const [slEmergency, setSlEmergency] = useState('');

  const [tpLevels, setTpLevels] = useState([{ price: '', pct: '100', trailing: false, trailingPct: '1' }]);
  const [beEnabled, setBeEnabled] = useState(false);
  const [beTriggerIdx, setBeTriggerIdx] = useState('0');

  // Ladder
  const [ladderEnabled, setLadderEnabled] = useState(false);
  const [ladderLevels, setLadderLevels] = useState('3');
  const [ladderLow, setLadderLow] = useState('');
  const [ladderHigh, setLadderHigh] = useState('');
  const [ladderDist, setLadderDist] = useState('equal');

  const loadOrders = useCallback(async () => {
    try {
      const res = await authFetch(`${API}?limit=30`);
      if (res.ok) setOrders(await res.json());
    } catch { /* */ }
  }, []);

  useEffect(() => { loadOrders(); }, [loadOrders]);

  const buildPayload = (isSimulate = false) => {
    const payload = {
      symbol: symbol.toUpperCase(),
      side,
      qty: parseFloat(qty),
      mode: isSimulate ? 'simulate' : mode,
      order_type: ladderEnabled ? 'ladder' : orderType,
    };
    if (orderType === 'limit' && entryPrice) payload.entry_price = parseFloat(entryPrice);
    if (slEnabled && slPrice) {
      payload.stop_loss = { price: parseFloat(slPrice), trailing: slTrailing, trailing_pct: parseFloat(slTrailingPct) || 2 };
      if (slEmergency) payload.stop_loss.emergency_price = parseFloat(slEmergency);
    }
    const validTps = tpLevels.filter(tp => tp.price);
    if (validTps.length > 0) {
      payload.take_profits = validTps.map(tp => ({
        price: parseFloat(tp.price), pct_of_qty: parseFloat(tp.pct) || 25,
        trailing: tp.trailing, trailing_pct: parseFloat(tp.trailingPct) || 1,
      }));
    }
    if (beEnabled) payload.break_even = { enabled: true, trigger_tp_index: parseInt(beTriggerIdx) || 0 };
    if (ladderEnabled && ladderLow && ladderHigh) {
      payload.ladder = { levels: parseInt(ladderLevels) || 3, range_low: parseFloat(ladderLow), range_high: parseFloat(ladderHigh), distribution: ladderDist };
    }
    return payload;
  };

  const handleSimulate = async () => {
    if (!symbol || !qty) return toast.error('Symbol and quantity required');
    setLoading(true);
    try {
      const res = await authFetch(API, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(buildPayload(true)) });
      const data = await res.json();
      if (res.ok) { setPreview(data); toast.success('Simulation complete'); }
      else toast.error(data.detail || 'Simulation failed');
    } catch { toast.error('Simulation error'); }
    finally { setLoading(false); }
  };

  const handleSubmit = async () => {
    if (!symbol || !qty) return toast.error('Symbol and quantity required');
    if (mode === 'live' && user?.role !== 'owner') return toast.error('Live trading restricted');
    setLoading(true);
    try {
      const res = await authFetch(API, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(buildPayload()) });
      const data = await res.json();
      if (res.ok) { toast.success(`Order placed: ${data.status}`); setPreview(null); loadOrders(); setView('orders'); }
      else toast.error(data.detail || 'Order failed');
    } catch { toast.error('Order failed'); }
    finally { setLoading(false); }
  };

  const cancelOrder = async (orderId) => {
    try {
      const res = await authFetch(`${API}/${orderId}`, { method: 'DELETE' });
      if (res.ok) { toast.success('Order cancelled'); loadOrders(); }
      else toast.error('Cancel failed');
    } catch { toast.error('Cancel error'); }
  };

  const addTpLevel = () => {
    if (tpLevels.length >= 5) return;
    const remaining = 100 - tpLevels.reduce((s, tp) => s + (parseFloat(tp.pct) || 0), 0);
    setTpLevels([...tpLevels, { price: '', pct: String(Math.max(remaining, 10)), trailing: false, trailingPct: '1' }]);
  };
  const removeTpLevel = (i) => setTpLevels(tpLevels.filter((_, idx) => idx !== i));
  const updateTp = (i, field, val) => setTpLevels(tpLevels.map((tp, idx) => idx === i ? { ...tp, [field]: val } : tp));

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm" data-testid="smart-order-panel">
      <div className="w-full max-w-2xl max-h-[90vh] overflow-y-auto bg-[#0B1426] border border-slate-600/30 rounded-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-400/20">
          <div className="flex items-center gap-2">
            <Layers className="w-5 h-5 text-[#3DE8D9]" />
            <h2 className="text-white font-bold text-base">Smart Orders</h2>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => setView('create')} className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${view === 'create' ? 'bg-[#3DE8D9] text-white' : 'text-slate-400 hover:text-white'}`} data-testid="smart-order-tab-create">Create</button>
            <button onClick={() => { setView('orders'); loadOrders(); }} className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${view === 'orders' ? 'bg-[#3DE8D9] text-white' : 'text-slate-400 hover:text-white'}`} data-testid="smart-order-tab-orders">Orders ({orders.length})</button>
            <button onClick={onClose} className="text-slate-400 hover:text-white ml-2"><X className="w-5 h-5" /></button>
          </div>
        </div>

        {view === 'create' ? (
          <div className="p-5 space-y-4">
            {/* Row 1: Symbol, Side, Qty, Mode */}
            <div className="grid grid-cols-4 gap-3">
              <div>
                <Label className="text-slate-400 text-[10px] mb-1">Symbol</Label>
                <Input value={symbol} onChange={e => setSymbol(e.target.value.toUpperCase())} placeholder="AAPL" className="bg-slate-800 border-slate-600 text-white h-9 text-sm" data-testid="smart-order-symbol" />
              </div>
              <div>
                <Label className="text-slate-400 text-[10px] mb-1">Side</Label>
                <div className="flex gap-1">
                  <button onClick={() => setSide('buy')} className={`flex-1 py-1.5 rounded-lg text-xs font-bold ${side === 'buy' ? 'bg-lime-500 text-white' : 'bg-slate-800 text-slate-400'}`} data-testid="smart-order-side-buy">BUY</button>
                  <button onClick={() => setSide('sell')} className={`flex-1 py-1.5 rounded-lg text-xs font-bold ${side === 'sell' ? 'bg-red-500 text-white' : 'bg-slate-800 text-slate-400'}`} data-testid="smart-order-side-sell">SELL</button>
                </div>
              </div>
              <div>
                <Label className="text-slate-400 text-[10px] mb-1">Quantity</Label>
                <Input type="number" value={qty} onChange={e => setQty(e.target.value)} placeholder="10" className="bg-slate-800 border-slate-600 text-white h-9 text-sm" data-testid="smart-order-qty" />
              </div>
              <div>
                <Label className="text-slate-400 text-[10px] mb-1">Mode</Label>
                <Select value={mode} onValueChange={setMode}>
                  <SelectTrigger className="bg-slate-800 border-slate-600 text-white h-9 text-xs"><SelectValue /></SelectTrigger>
                  <SelectContent><SelectItem value="paper">Paper</SelectItem><SelectItem value="live">Live</SelectItem><SelectItem value="simulate">Preview</SelectItem></SelectContent>
                </Select>
              </div>
            </div>

            {/* Row 2: Order Type, Entry Price */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="text-slate-400 text-[10px] mb-1">Entry Type</Label>
                <Select value={orderType} onValueChange={setOrderType}>
                  <SelectTrigger className="bg-slate-800 border-slate-600 text-white h-9 text-xs"><SelectValue /></SelectTrigger>
                  <SelectContent><SelectItem value="market">Market</SelectItem><SelectItem value="limit">Limit</SelectItem></SelectContent>
                </Select>
              </div>
              {orderType === 'limit' && (
                <div>
                  <Label className="text-slate-400 text-[10px] mb-1">Limit Price</Label>
                  <Input type="number" value={entryPrice} onChange={e => setEntryPrice(e.target.value)} placeholder="0.00" className="bg-slate-800 border-slate-600 text-white h-9 text-sm" />
                </div>
              )}
            </div>

            {/* Stop Loss */}
            <div className="bg-slate-800/50 rounded-xl p-3 border border-slate-600/20">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <Shield className="w-4 h-4 text-red-400" />
                  <span className="text-white text-xs font-semibold">Stop Loss</span>
                </div>
                <button onClick={() => setSlEnabled(!slEnabled)} className={`w-8 h-4 rounded-full transition-colors ${slEnabled ? 'bg-red-500' : 'bg-slate-700'}`} data-testid="smart-order-sl-toggle">
                  <div className={`w-3.5 h-3.5 rounded-full bg-white transition-transform ${slEnabled ? 'translate-x-4' : 'translate-x-0.5'}`} />
                </button>
              </div>
              {slEnabled && (
                <div className="grid grid-cols-3 gap-2">
                  <div>
                    <Label className="text-slate-500 text-[9px]">SL Price</Label>
                    <Input type="number" value={slPrice} onChange={e => setSlPrice(e.target.value)} placeholder="0.00" className="bg-slate-900 border-slate-700 text-white h-8 text-xs" data-testid="smart-order-sl-price" />
                  </div>
                  <div>
                    <Label className="text-slate-500 text-[9px]">Trailing %</Label>
                    <div className="flex items-center gap-1">
                      <button onClick={() => setSlTrailing(!slTrailing)} className={`w-6 h-3.5 rounded-full ${slTrailing ? 'bg-[#3DE8D9]' : 'bg-slate-700'}`}>
                        <div className={`w-2.5 h-2.5 rounded-full bg-white transition-transform ${slTrailing ? 'translate-x-3' : 'translate-x-0.5'}`} />
                      </button>
                      {slTrailing && <Input type="number" value={slTrailingPct} onChange={e => setSlTrailingPct(e.target.value)} className="bg-slate-900 border-slate-700 text-white h-8 text-xs w-16" />}
                    </div>
                  </div>
                  <div>
                    <Label className="text-slate-500 text-[9px]">Emergency SL</Label>
                    <Input type="number" value={slEmergency} onChange={e => setSlEmergency(e.target.value)} placeholder="Optional" className="bg-slate-900 border-slate-700 text-white h-8 text-xs" />
                  </div>
                </div>
              )}
            </div>

            {/* Take Profits */}
            <div className="bg-slate-800/50 rounded-xl p-3 border border-slate-600/20">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <Target className="w-4 h-4 text-lime-400" />
                  <span className="text-white text-xs font-semibold">Take Profits</span>
                  <span className="text-slate-500 text-[9px]">(up to 5)</span>
                </div>
                <button onClick={addTpLevel} className="text-[#3DE8D9] text-[10px] flex items-center gap-0.5 hover:text-white" disabled={tpLevels.length >= 5}>
                  <Plus className="w-3 h-3" /> Add Level
                </button>
              </div>
              <div className="space-y-2">
                {tpLevels.map((tp, i) => (
                  <div key={`tp-${i}`} className="grid grid-cols-12 gap-1.5 items-end">
                    <div className="col-span-1 flex items-center justify-center">
                      <span className="text-[#3DE8D9] text-[10px] font-bold">TP{i + 1}</span>
                    </div>
                    <div className="col-span-4">
                      <Label className="text-slate-500 text-[9px]">Price</Label>
                      <Input type="number" value={tp.price} onChange={e => updateTp(i, 'price', e.target.value)} placeholder="0.00" className="bg-slate-900 border-slate-700 text-white h-8 text-xs" data-testid={`smart-order-tp-${i}-price`} />
                    </div>
                    <div className="col-span-3">
                      <Label className="text-slate-500 text-[9px]">% of Position</Label>
                      <Input type="number" value={tp.pct} onChange={e => updateTp(i, 'pct', e.target.value)} className="bg-slate-900 border-slate-700 text-white h-8 text-xs" />
                    </div>
                    <div className="col-span-3">
                      <Label className="text-slate-500 text-[9px]">Trailing</Label>
                      <div className="flex items-center gap-1 h-8">
                        <button onClick={() => updateTp(i, 'trailing', !tp.trailing)} className={`w-6 h-3.5 rounded-full ${tp.trailing ? 'bg-[#3DE8D9]' : 'bg-slate-700'}`}>
                          <div className={`w-2.5 h-2.5 rounded-full bg-white transition-transform ${tp.trailing ? 'translate-x-3' : 'translate-x-0.5'}`} />
                        </button>
                        {tp.trailing && <Input type="number" value={tp.trailingPct} onChange={e => updateTp(i, 'trailingPct', e.target.value)} className="bg-slate-900 border-slate-700 text-white h-8 text-xs w-12" />}
                      </div>
                    </div>
                    <div className="col-span-1 flex items-center justify-center">
                      {tpLevels.length > 1 && <button onClick={() => removeTpLevel(i)} className="text-slate-500 hover:text-red-400"><Minus className="w-3.5 h-3.5" /></button>}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Advanced: Ladder + Break-Even */}
            <button onClick={() => setShowAdvanced(!showAdvanced)} className="flex items-center gap-1 text-slate-400 text-xs hover:text-white">
              {showAdvanced ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />} Advanced Options
            </button>

            {showAdvanced && (
              <div className="space-y-3">
                {/* Ladder */}
                <div className="bg-slate-800/50 rounded-xl p-3 border border-slate-600/20">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <BarChart3 className="w-4 h-4 text-violet-400" />
                      <span className="text-white text-xs font-semibold">Ladder Entry</span>
                    </div>
                    <button onClick={() => setLadderEnabled(!ladderEnabled)} className={`w-8 h-4 rounded-full ${ladderEnabled ? 'bg-violet-500' : 'bg-slate-700'}`} data-testid="smart-order-ladder-toggle">
                      <div className={`w-3.5 h-3.5 rounded-full bg-white transition-transform ${ladderEnabled ? 'translate-x-4' : 'translate-x-0.5'}`} />
                    </button>
                  </div>
                  {ladderEnabled && (
                    <div className="grid grid-cols-4 gap-2">
                      <div>
                        <Label className="text-slate-500 text-[9px]">Levels</Label>
                        <Input type="number" value={ladderLevels} onChange={e => setLadderLevels(e.target.value)} min="2" max="10" className="bg-slate-900 border-slate-700 text-white h-8 text-xs" />
                      </div>
                      <div>
                        <Label className="text-slate-500 text-[9px]">Range Low</Label>
                        <Input type="number" value={ladderLow} onChange={e => setLadderLow(e.target.value)} className="bg-slate-900 border-slate-700 text-white h-8 text-xs" />
                      </div>
                      <div>
                        <Label className="text-slate-500 text-[9px]">Range High</Label>
                        <Input type="number" value={ladderHigh} onChange={e => setLadderHigh(e.target.value)} className="bg-slate-900 border-slate-700 text-white h-8 text-xs" />
                      </div>
                      <div>
                        <Label className="text-slate-500 text-[9px]">Distribution</Label>
                        <Select value={ladderDist} onValueChange={setLadderDist}>
                          <SelectTrigger className="bg-slate-900 border-slate-700 text-white h-8 text-[10px]"><SelectValue /></SelectTrigger>
                          <SelectContent><SelectItem value="equal">Equal</SelectItem><SelectItem value="weighted_bottom">Weight Bottom</SelectItem><SelectItem value="weighted_top">Weight Top</SelectItem></SelectContent>
                        </Select>
                      </div>
                    </div>
                  )}
                </div>

                {/* Break-Even */}
                <div className="bg-slate-800/50 rounded-xl p-3 border border-slate-600/20">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <TrendingUp className="w-4 h-4 text-[#3DE8D9]" />
                      <span className="text-white text-xs font-semibold">Break-Even Protection</span>
                    </div>
                    <div className="flex items-center gap-2">
                      {beEnabled && (
                        <div className="flex items-center gap-1">
                          <span className="text-slate-500 text-[9px]">After TP</span>
                          <Input type="number" value={beTriggerIdx} onChange={e => setBeTriggerIdx(e.target.value)} min="0" max="4" className="bg-slate-900 border-slate-700 text-white h-6 text-[10px] w-10" />
                        </div>
                      )}
                      <button onClick={() => setBeEnabled(!beEnabled)} className={`w-8 h-4 rounded-full ${beEnabled ? 'bg-[#3DE8D9]' : 'bg-slate-700'}`} data-testid="smart-order-be-toggle">
                        <div className={`w-3.5 h-3.5 rounded-full bg-white transition-transform ${beEnabled ? 'translate-x-4' : 'translate-x-0.5'}`} />
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Preview Box */}
            {preview && (
              <div className="bg-[#111C30] rounded-xl p-4 border border-[#3DE8D9]/30" data-testid="smart-order-preview">
                <div className="flex items-center gap-2 mb-3">
                  <Eye className="w-4 h-4 text-[#3DE8D9]" />
                  <span className="text-white text-xs font-semibold">Order Preview</span>
                </div>
                <div className="grid grid-cols-3 gap-3 text-center">
                  <div>
                    <p className="text-slate-400 text-[9px]">Entry Price</p>
                    <p className="text-white text-sm font-bold">${preview.avg_entry_price}</p>
                  </div>
                  <div>
                    <p className="text-slate-400 text-[9px]">Projected Risk</p>
                    <p className="text-red-400 text-sm font-bold">${preview.projected_risk}</p>
                  </div>
                  <div>
                    <p className="text-slate-400 text-[9px]">Projected Reward</p>
                    <p className="text-lime-400 text-sm font-bold">${preview.projected_reward}</p>
                  </div>
                </div>
                <div className="flex items-center justify-center mt-2">
                  <span className="text-slate-400 text-[10px]">Risk:Reward</span>
                  <span className={`ml-2 text-lg font-black ${preview.risk_reward_ratio >= 2 ? 'text-lime-400' : preview.risk_reward_ratio >= 1 ? 'text-amber-400' : 'text-red-400'}`}>
                    {preview.risk_reward_ratio > 0 ? `1:${preview.risk_reward_ratio}` : 'N/A'}
                  </span>
                </div>
                {preview.legs?.length > 1 && (
                  <div className="mt-3 border-t border-slate-700 pt-2">
                    <p className="text-slate-400 text-[9px] mb-1">Ladder Legs ({preview.leg_count})</p>
                    {preview.legs.map((leg, i) => (
                      <div key={`preview-leg-${i}`} className="flex justify-between text-[10px]">
                        <span className="text-slate-300">${leg.price.toFixed(2)}</span>
                        <span className="text-slate-400">{leg.qty.toFixed(2)} shares</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Action Buttons */}
            <div className="flex gap-2">
              <Button onClick={handleSimulate} disabled={loading || !symbol || !qty} variant="outline"
                className="flex-1 bg-blue-900/20 text-blue-400 border-blue-800/50 hover:bg-blue-800/30 h-10" data-testid="smart-order-simulate">
                <Eye className="w-4 h-4 mr-1.5" /> Preview
              </Button>
              <Button onClick={handleSubmit} disabled={loading || !symbol || !qty}
                className={`flex-1 h-10 font-bold ${side === 'buy' ? 'bg-lime-600 hover:bg-lime-500 text-white' : 'bg-red-600 hover:bg-red-500 text-white'}`} data-testid="smart-order-submit">
                <Play className="w-4 h-4 mr-1.5" /> {side === 'buy' ? 'Place Buy' : 'Place Sell'}
              </Button>
            </div>
          </div>
        ) : (
          /* Orders List */
          <div className="p-4 space-y-2" data-testid="smart-orders-list">
            {orders.length === 0 ? (
              <p className="text-slate-400 text-sm text-center py-8">No smart orders yet</p>
            ) : (
              orders.map((o, i) => (
                <div key={o.order_id || `order-${i}`} className="bg-slate-800/40 rounded-xl p-3 border border-slate-600/20">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-white font-bold text-sm">{o.symbol}</span>
                      <Badge className={`text-[9px] ${o.side === 'buy' ? 'bg-lime-500/15 text-lime-400' : 'bg-red-500/15 text-red-400'}`}>{o.side?.toUpperCase()}</Badge>
                      <ModeTag mode={o.mode} />
                      <StatusTag status={o.status} />
                    </div>
                    <div className="flex items-center gap-2">
                      {o.realized_pnl !== 0 && (
                        <span className={`text-xs font-bold ${o.realized_pnl > 0 ? 'text-lime-400' : 'text-red-400'}`}>
                          {o.realized_pnl > 0 ? '+' : ''}${o.realized_pnl?.toFixed(2)}
                        </span>
                      )}
                      {['pending', 'partially_filled', 'filled'].includes(o.status) && (
                        <button onClick={() => cancelOrder(o.order_id)} className="text-slate-500 hover:text-red-400">
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-4 mt-1.5 text-[10px] text-slate-400">
                    <span>Qty: {o.total_qty}</span>
                    {o.avg_fill_price && <span>Avg: ${o.avg_fill_price?.toFixed(2)}</span>}
                    {o.current_price && <span>Now: ${o.current_price?.toFixed(2)}</span>}
                    {o.stop_loss && <span className="text-red-400">SL: ${o.stop_loss.price?.toFixed(2)}{o.stop_loss.trailing ? ' (trail)' : ''}{o.stop_loss.moved_to_break_even ? ' [BE]' : ''}</span>}
                    {o.take_profits?.length > 0 && <span className="text-lime-400">TPs: {o.take_profits.filter(tp => tp.triggered).length}/{o.take_profits.length}</span>}
                    {o.legs?.length > 1 && <span className="text-violet-400">Ladder: {o.legs.filter(l => l.filled).length}/{o.legs.length}</span>}
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default SmartOrderPanel;
