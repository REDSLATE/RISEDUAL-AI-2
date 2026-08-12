import React, { useState, useEffect, useCallback } from 'react';
import { X, Bot, Plus, Trash2, Settings, Copy, Check, Grid3X3, Radio, Webhook, Power, RefreshCw } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Badge } from './ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { toast } from './ui/sonner';
import { authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';
import PanelShell from './PanelShell';
import TradingModeBanner from './TradingModeBanner';
import { useTradingMode } from '../hooks/useTradingMode';

const API = `${getApiBase()}/api/bots`;

const BOT_ICONS = { grid: Grid3X3, signal: Radio, webhook: Webhook };
const BOT_COLORS = { grid: 'text-[#3DE8D9]', signal: 'text-violet-400', webhook: 'text-amber-400' };

const Toggle = ({ enabled, onToggle, testId }) => (
  <button onClick={onToggle} className={`relative w-10 h-5 rounded-full transition-colors ${enabled ? 'bg-lime-500' : 'bg-slate-700'}`} data-testid={testId}>
    <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${enabled ? 'translate-x-5.5 left-auto right-0.5' : 'left-0.5'}`}
      style={{ transform: enabled ? 'translateX(20px)' : 'translateX(0)' }} />
  </button>
);

const BotCard = ({ bot, onToggle, onDelete, onCopyWebhook, onBrokerChange }) => {
  const Icon = BOT_ICONS[bot.type] || Bot;
  const color = BOT_COLORS[bot.type] || 'text-slate-400';
  const [copied, setCopied] = useState(false);
  const [switching, setSwitching] = useState(false);
  const currentBroker = (bot.broker || 'public').toLowerCase();

  const copyUrl = () => {
    if (!bot.webhook_secret) return;
    const url = `${getApiBase()}/api/bots/webhook/${bot.bot_id}/${bot.webhook_secret}`;
    navigator.clipboard.writeText(url).then(() => {
      setCopied(true);
      toast.success('Webhook URL copied');
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const switchBroker = async (b) => {
    if (b === currentBroker || switching) return;
    setSwitching(true);
    try {
      const ok = await onBrokerChange?.(bot.bot_id, b);
      if (ok) toast.success(`Broker switched to ${b === 'public' ? 'Public.com' : 'MooMoo'}`);
    } finally {
      setSwitching(false);
    }
  };

  return (
    <div className={`bg-slate-800/40 rounded-xl p-4 border ${bot.enabled ? 'border-lime-500/30' : 'border-slate-600/20'}`} data-testid={`bot-card-${bot.type}`}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Icon className={`w-5 h-5 ${color}`} />
          <div>
            <span className="text-white text-sm font-semibold">{bot.name}</span>
            <div className="flex items-center gap-1.5 mt-0.5">
              <Badge className={`text-[8px] ${bot.enabled ? 'bg-lime-500/15 text-lime-400' : 'bg-slate-700 text-slate-400'}`}>
                {bot.enabled ? 'ACTIVE' : 'OFF'}
              </Badge>
              <Badge className="text-[8px] bg-slate-700 text-slate-400">{bot.mode?.toUpperCase()}</Badge>
              <Badge className={`text-[8px] ${BOT_COLORS[bot.type]?.replace('text-', 'bg-').replace('400', '500/15')} ${BOT_COLORS[bot.type]}`}>
                {bot.type?.toUpperCase()}
              </Badge>
              <Badge
                className={`text-[8px] ${
                  currentBroker === 'moomoo'
                    ? 'bg-fuchsia-500/15 text-fuchsia-300'
                    : 'bg-cyan-500/15 text-cyan-300'
                }`}
                data-testid={`bot-broker-badge-${bot.bot_id}`}
              >
                {currentBroker === 'moomoo' ? 'MOOMOO' : 'PUBLIC'}
              </Badge>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Toggle enabled={bot.enabled} onToggle={() => onToggle(bot.bot_id, !bot.enabled)} testId={`bot-toggle-${bot.bot_id}`} />
          <button onClick={() => onDelete(bot.bot_id)} className="text-slate-500 hover:text-red-400"><Trash2 className="w-4 h-4" /></button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-2 mb-3">
        <div className="bg-slate-900/50 rounded-lg p-2 text-center">
          <p className="text-slate-500 text-[9px]">Trades</p>
          <p className="text-white text-sm font-bold">{bot.stats?.trades || 0}</p>
        </div>
        <div className="bg-slate-900/50 rounded-lg p-2 text-center">
          <p className="text-slate-500 text-[9px]">Signals</p>
          <p className="text-white text-sm font-bold">{bot.stats?.signals_received || 0}</p>
        </div>
        <div className="bg-slate-900/50 rounded-lg p-2 text-center">
          <p className="text-slate-500 text-[9px]">P&L</p>
          <p className={`text-sm font-bold ${(bot.stats?.pnl || 0) >= 0 ? 'text-lime-400' : 'text-red-400'}`}>
            ${(bot.stats?.pnl || 0).toFixed(2)}
          </p>
        </div>
      </div>

      {/* Config Summary */}
      <div className="text-[10px] text-slate-400 space-y-0.5">
        {bot.type === 'grid' && bot.config && (
          <>
            <p>{bot.config.symbol} | ${bot.config.lower_price?.toLocaleString()} — ${bot.config.upper_price?.toLocaleString()}</p>
            <p>{bot.config.grid_levels} levels | {bot.config.qty_per_grid} per grid</p>
          </>
        )}
        {bot.type === 'signal' && bot.config && (
          <>
            <p>Min AI confidence: {bot.config.min_confidence}% | Qty: {bot.config.qty}</p>
            <p>Auto SL: {bot.config.auto_sl_pct}% | Auto TP: {bot.config.auto_tp_pct}% | Smart Order: {bot.config.use_smart_order ? 'Yes' : 'No'}</p>
          </>
        )}
        {bot.type === 'webhook' && bot.config && (
          <>
            <p>Default qty: {bot.config.default_qty} | Max/day: {bot.config.max_trades_per_day} | Today: {bot.config.trades_today || 0}</p>
            <button onClick={copyUrl} className="flex items-center gap-1 text-amber-400 hover:text-white mt-1">
              {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
              {copied ? 'Copied!' : 'Copy Webhook URL'}
            </button>
          </>
        )}
      </div>
      {/* Broker selector (edit-time) */}
      <div className="mt-3 flex items-center gap-2" data-testid={`bot-broker-selector-${bot.bot_id}`}>
        <span className="text-[9px] uppercase tracking-wider text-slate-500">Broker</span>
        {['public', 'moomoo'].map(b => (
          <button
            key={b}
            type="button"
            disabled={switching}
            onClick={() => switchBroker(b)}
            data-testid={`bot-broker-switch-${bot.bot_id}-${b}`}
            className={`px-2 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider border transition ${
              currentBroker === b
                ? (b === 'moomoo'
                    ? 'bg-fuchsia-500/10 text-fuchsia-300 border-fuchsia-500/40'
                    : 'bg-cyan-500/10 text-cyan-300 border-cyan-500/40')
                : 'bg-slate-800/50 text-slate-500 border-slate-700 hover:text-white'
            } ${switching ? 'opacity-50 cursor-not-allowed' : ''}`}
          >
            {b === 'public' ? 'Public' : 'MooMoo'}
          </button>
        ))}
      </div>
      {bot.last_run && <p className="text-slate-600 text-[8px] mt-2">Last run: {new Date(bot.last_run).toLocaleString()}</p>}
    </div>
  );
};

const CreateBotForm = ({ onCreated }) => {
  const tradingMode = useTradingMode();
  const [type, setType] = useState('grid');
  const [name, setName] = useState('');
  const [broker, setBroker] = useState('public');
  // Bots inherit the user's current global trading mode at creation
  // time. The Paper/Live switch lives only in the navbar pill — having
  // a per-bot picker here was a duplicate that confused operators.
  const mode = tradingMode.mode || 'paper';
  const [creating, setCreating] = useState(false);

  // Grid config
  const [symbol, setSymbol] = useState('BTC');
  const [upperPrice, setUpperPrice] = useState('');
  const [lowerPrice, setLowerPrice] = useState('');
  const [gridLevels, setGridLevels] = useState('5');
  const [qtyPerGrid, setQtyPerGrid] = useState('0.01');

  // Signal config
  const [minConf, setMinConf] = useState('70');
  const [sigQty, setSigQty] = useState('5');
  const [slPct, setSlPct] = useState('3');
  const [tpPct, setTpPct] = useState('6');

  // Webhook config
  const [defQty, setDefQty] = useState('10');
  const [maxDay, setMaxDay] = useState('10');

  const handleCreate = async () => {
    setCreating(true);
    try {
      const config = type === 'grid' ? {
        symbol: symbol.toUpperCase(), upper_price: parseFloat(upperPrice), lower_price: parseFloat(lowerPrice),
        grid_levels: parseInt(gridLevels), qty_per_grid: parseFloat(qtyPerGrid),
      } : type === 'signal' ? {
        min_confidence: parseInt(minConf), qty: parseFloat(sigQty), auto_sl_pct: parseFloat(slPct),
        auto_tp_pct: parseFloat(tpPct), use_smart_order: true,
      } : {
        default_qty: parseFloat(defQty), max_trades_per_day: parseInt(maxDay),
      };

      const res = await authFetch(API, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type, name: name || `${type.charAt(0).toUpperCase() + type.slice(1)} Bot`, mode, broker, config }),
      });
      if (res.ok) { toast.success('Bot created (OFF by default)'); onCreated(); }
      else { const err = await res.json().catch(() => ({})); toast.error(err.detail || 'Create failed'); }
    } catch { toast.error('Create error'); }
    finally { setCreating(false); }
  };

  return (
    <div className="space-y-3" data-testid="create-bot-form">
      <div className="grid grid-cols-3 gap-2">
        {(['grid', 'signal', 'webhook']).map(t => {
          const Icon = BOT_ICONS[t];
          return (
            <button key={t} onClick={() => setType(t)}
              className={`flex items-center justify-center gap-1.5 py-2.5 rounded-xl text-xs font-semibold transition-all ${
                type === t ? `bg-slate-700 ${BOT_COLORS[t]} border border-current` : 'bg-slate-800/50 text-slate-400 hover:text-white'
              }`} data-testid={`create-bot-type-${t}`}>
              <Icon className="w-4 h-4" /> {t === 'grid' ? 'Grid Bot' : t === 'signal' ? 'Signal Bot' : 'Webhook'}
            </button>
          );
        })}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <Label className="text-slate-400 text-[10px]">Bot Name</Label>
          <Input value={name} onChange={e => setName(e.target.value)} placeholder={`My ${type} bot`} className="bg-slate-800 border-slate-600 text-white h-8 text-xs" data-testid="create-bot-name" />
        </div>
        <div>
          <Label className="text-slate-400 text-[10px]">Mode</Label>
          <div
            className={`h-8 px-2 rounded-md border flex items-center justify-between text-[10px] font-bold uppercase tracking-wider ${
              mode === 'live'
                ? 'bg-orange-500/10 text-orange-300 border-orange-500/30'
                : 'bg-[#3DE8D9]/10 text-[#3DE8D9] border-[#3DE8D9]/30'
            }`}
            title="Inherits the global trading mode from the navbar pill"
            data-testid="create-bot-mode-display"
            data-mode={mode}
          >
            <span>{mode}</span>
            <span className="text-slate-500 text-[8px] font-normal normal-case tracking-normal">navbar</span>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <Label className="text-slate-400 text-[10px]">Broker</Label>
          <div className="grid grid-cols-2 gap-1" data-testid="create-bot-broker">
            {['public', 'moomoo'].map(b => (
              <button
                key={b}
                type="button"
                onClick={() => setBroker(b)}
                data-testid={`create-bot-broker-${b}`}
                className={`h-8 rounded-md text-[10px] font-bold uppercase tracking-wider border transition ${
                  broker === b
                    ? 'bg-[#3DE8D9]/10 text-[#3DE8D9] border-[#3DE8D9]/40'
                    : 'bg-slate-800/50 text-slate-400 border-slate-700 hover:text-white'
                }`}
              >
                {b === 'public' ? 'Public.com' : 'MooMoo'}
              </button>
            ))}
          </div>
        </div>
        <div>
          <Label className="text-slate-400 text-[10px]">&nbsp;</Label>
          <div className="text-[9px] text-slate-500 leading-tight pt-2">
            {broker === 'moomoo'
              ? 'Routes via MooMoo OpenD (RTH · single position · $50 notional cap in V1). Requires MOOMOO_LIVE_ENABLED=1 in prod.'
              : 'Default equity broker. Fractional RTH orders.'}
          </div>
        </div>
      </div>

      {type === 'grid' && (
        <div className="bg-slate-800/30 rounded-xl p-3 space-y-2 border border-slate-600/15">
          <div className="grid grid-cols-2 gap-2">
            <div><Label className="text-slate-500 text-[9px]">Symbol</Label><Input value={symbol} onChange={e => setSymbol(e.target.value)} className="bg-slate-900 border-slate-700 text-white h-8 text-xs" /></div>
            <div><Label className="text-slate-500 text-[9px]">Grid Levels</Label><Input type="number" value={gridLevels} onChange={e => setGridLevels(e.target.value)} className="bg-slate-900 border-slate-700 text-white h-8 text-xs" /></div>
          </div>
          <div className="grid grid-cols-3 gap-2">
            <div><Label className="text-slate-500 text-[9px]">Lower Price</Label><Input type="number" value={lowerPrice} onChange={e => setLowerPrice(e.target.value)} className="bg-slate-900 border-slate-700 text-white h-8 text-xs" /></div>
            <div><Label className="text-slate-500 text-[9px]">Upper Price</Label><Input type="number" value={upperPrice} onChange={e => setUpperPrice(e.target.value)} className="bg-slate-900 border-slate-700 text-white h-8 text-xs" /></div>
            <div><Label className="text-slate-500 text-[9px]">Qty/Grid</Label><Input type="number" value={qtyPerGrid} onChange={e => setQtyPerGrid(e.target.value)} className="bg-slate-900 border-slate-700 text-white h-8 text-xs" /></div>
          </div>
        </div>
      )}

      {type === 'signal' && (
        <div className="bg-slate-800/30 rounded-xl p-3 space-y-2 border border-slate-600/15">
          <div className="grid grid-cols-2 gap-2">
            <div><Label className="text-slate-500 text-[9px]">Min AI Confidence %</Label><Input type="number" value={minConf} onChange={e => setMinConf(e.target.value)} className="bg-slate-900 border-slate-700 text-white h-8 text-xs" /></div>
            <div><Label className="text-slate-500 text-[9px]">Quantity</Label><Input type="number" value={sigQty} onChange={e => setSigQty(e.target.value)} className="bg-slate-900 border-slate-700 text-white h-8 text-xs" /></div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div><Label className="text-slate-500 text-[9px]">Auto SL %</Label><Input type="number" value={slPct} onChange={e => setSlPct(e.target.value)} className="bg-slate-900 border-slate-700 text-white h-8 text-xs" /></div>
            <div><Label className="text-slate-500 text-[9px]">Auto TP %</Label><Input type="number" value={tpPct} onChange={e => setTpPct(e.target.value)} className="bg-slate-900 border-slate-700 text-white h-8 text-xs" /></div>
          </div>
          <p className="text-slate-500 text-[9px]">Creates Smart Orders with trailing SL when AI confidence exceeds threshold</p>
        </div>
      )}

      {type === 'webhook' && (
        <div className="bg-slate-800/30 rounded-xl p-3 space-y-2 border border-slate-600/15">
          <div className="grid grid-cols-2 gap-2">
            <div><Label className="text-slate-500 text-[9px]">Default Quantity</Label><Input type="number" value={defQty} onChange={e => setDefQty(e.target.value)} className="bg-slate-900 border-slate-700 text-white h-8 text-xs" /></div>
            <div><Label className="text-slate-500 text-[9px]">Max Trades/Day</Label><Input type="number" value={maxDay} onChange={e => setMaxDay(e.target.value)} className="bg-slate-900 border-slate-700 text-white h-8 text-xs" /></div>
          </div>
          <p className="text-slate-500 text-[9px]">Receives POST webhooks from TradingView with {`{"action":"buy","symbol":"AAPL","qty":10}`}</p>
        </div>
      )}

      <Button onClick={handleCreate} disabled={creating} className="w-full bg-[#3DE8D9] text-white hover:bg-[#3DE8D9]/80 h-9 text-xs font-bold" data-testid="create-bot-submit">
        <Plus className="w-3.5 h-3.5 mr-1.5" /> {creating ? 'Creating...' : 'Create Bot (OFF by default)'}
      </Button>
    </div>
  );
};

const TradingBotPanel = ({ onClose }) => {
  const [bots, setBots] = useState([]);
  const [view, setView] = useState('list');
  const [loading, setLoading] = useState(true);

  const loadBots = useCallback(async () => {
    try {
      const res = await authFetch(API);
      if (res.ok) setBots(await res.json());
    } catch { /* */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { loadBots(); }, [loadBots]);

  const toggleBot = async (botId, enabled) => {
    try {
      const res = await authFetch(`${API}/${botId}/toggle`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      if (res.ok) { toast.success(enabled ? 'Bot activated' : 'Bot deactivated'); loadBots(); }
      else toast.error('Toggle failed');
    } catch { toast.error('Toggle error'); }
  };

  const deleteBot = async (botId) => {
    try {
      const res = await authFetch(`${API}/${botId}`, { method: 'DELETE' });
      if (res.ok) { toast.success('Bot deleted'); loadBots(); }
    } catch { toast.error('Delete error'); }
  };

  const changeBotBroker = async (botId, broker) => {
    try {
      const res = await authFetch(`${API}/${botId}/broker`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ broker }),
      });
      if (res.ok) { await loadBots(); return true; }
      const err = await res.json().catch(() => ({}));
      toast.error(err.detail || 'Broker switch failed');
      return false;
    } catch {
      toast.error('Broker switch error');
      return false;
    }
  };

  return (
    <PanelShell onClose={onClose} testId="trading-bot-panel" maxWidth="max-w-2xl">
      <div className="w-full max-h-[90vh] overflow-y-auto bg-[#0B1426] border border-slate-600/30 rounded-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-400/20">
          <div className="flex items-center gap-2">
            <Bot className="w-5 h-5 text-[#3DE8D9]" />
            <h2 className="text-white font-bold text-base">Trading Bots</h2>
            <span className="text-slate-400 text-[10px]">{bots.filter(b => b.enabled).length} active</span>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => setView('list')} className={`px-3 py-1 rounded-lg text-xs font-medium ${view === 'list' ? 'bg-[#3DE8D9] text-white' : 'text-slate-400'}`} data-testid="bot-tab-list">My Bots</button>
            <button onClick={() => setView('create')} className={`px-3 py-1 rounded-lg text-xs font-medium ${view === 'create' ? 'bg-[#3DE8D9] text-white' : 'text-slate-400'}`} data-testid="bot-tab-create">Create</button>
            <Button size="sm" variant="outline" onClick={loadBots} className="bg-slate-800 border-slate-400/30 text-slate-300 h-7 ml-1">
              <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
            </Button>
            {onClose && <button onClick={onClose} className="text-slate-400 hover:text-white ml-1"><X className="w-5 h-5" /></button>}
          </div>
        </div>

        <div className="p-5">
          {/* Bot scheduler reads each user's mode at execution time —
              this banner is informational so the user knows where
              their bots will route. */}
          <div className="mb-4">
            <TradingModeBanner expectedMode="live" compact />
          </div>
          {view === 'create' ? (
            <CreateBotForm onCreated={() => { loadBots(); setView('list'); }} />
          ) : (
            <div className="space-y-3" data-testid="bot-list">
              {bots.length === 0 ? (
                <div className="text-center py-12">
                  <Bot className="w-10 h-10 text-slate-600 mx-auto mb-3" />
                  <p className="text-slate-400 text-sm">No bots yet</p>
                  <Button onClick={() => setView('create')} className="mt-3 bg-[#3DE8D9] text-white text-xs">
                    <Plus className="w-3.5 h-3.5 mr-1" /> Create Your First Bot
                  </Button>
                </div>
              ) : (
                bots.map((b, i) => (
                  <BotCard key={b.bot_id || `bot-${i}`} bot={b} onToggle={toggleBot} onDelete={deleteBot} onBrokerChange={changeBotBroker} />
                ))
              )}
            </div>
          )}
        </div>
      </div>
    </PanelShell>
  );
};

export default TradingBotPanel;
