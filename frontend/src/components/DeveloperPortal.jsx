import React, { useState, useEffect, useCallback } from 'react';
import { Key, Copy, Trash2, Plus, X, BarChart3, Shield, Zap, Check, Code } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { toast } from './ui/sonner';
import { authFetch, useAuth } from '../contexts/AuthContext';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

const DeveloperPortal = ({ onClose }) => {
  const { user } = useAuth();
  const [keys, setKeys] = useState([]);
  const [usage, setUsage] = useState(null);
  const [newLabel, setNewLabel] = useState('');
  const [newKey, setNewKey] = useState(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState('keys');

  const isPro = user?.subscription_status === 'pro';

  const fetchData = useCallback(async () => {
    try {
      const [keysRes, usageRes] = await Promise.all([
        authFetch(`${API}/developer/keys`),
        authFetch(`${API}/developer/usage`),
      ]);
      if (keysRes.ok) { const d = await keysRes.json(); setKeys(d.keys || []); }
      if (usageRes.ok) setUsage(await usageRes.json());
    } catch (e) {
      logger.warn('Developer portal fetch error:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const generateKey = async () => {
    try {
      const res = await authFetch(`${API}/developer/keys/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: newLabel || 'Default' }),
      });
      if (res.ok) {
        const data = await res.json();
        setNewKey(data.key);
        setNewLabel('');
        fetchData();
        toast.success('API key generated');
      } else {
        const d = await res.json();
        toast.error(d.detail || 'Failed to generate key');
      }
    } catch (e) {
      toast.error('Error generating key');
    }
  };

  const revokeKey = async (keyId) => {
    try {
      const res = await authFetch(`${API}/developer/keys/${keyId}`, { method: 'DELETE' });
      if (res.ok) {
        fetchData();
        toast.success('API key revoked');
      }
    } catch (e) {
      toast.error('Error revoking key');
    }
  };

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text);
    toast.success('Copied to clipboard');
  };

  const limit = isPro ? 5000 : 100;
  const used = usage?.today || 0;
  const pct = Math.min((used / limit) * 100, 100);

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-start justify-center overflow-y-auto p-4" data-testid="developer-portal">
      <div className="bg-slate-900 rounded-2xl max-w-3xl w-full my-4 border border-slate-400/25">
        {/* Header */}
        <div className="p-5 border-b border-slate-400/30 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 bg-violet-600 rounded-xl flex items-center justify-center">
              <Code className="w-5 h-5 text-white" />
            </div>
            <div>
              <h2 className="text-white text-lg font-bold">Developer API</h2>
              <p className="text-slate-400 text-[10px]">{isPro ? 'Pro' : 'Free'} tier &middot; {limit.toLocaleString()} calls/day</p>
            </div>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white text-xl px-2" data-testid="dev-portal-close">x</button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-slate-400/25 px-4">
          {[
            { id: 'keys', label: 'API Keys', icon: Key },
            { id: 'usage', label: 'Usage', icon: BarChart3 },
            { id: 'docs', label: 'Documentation', icon: Code },
          ].map(t => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`flex items-center gap-1.5 px-3 py-3 text-xs font-medium border-b-2 transition-all ${
                tab === t.id ? 'text-[#3DE8D9] border-[#3DE8D9]' : 'text-slate-400 border-transparent hover:text-slate-300'
              }`}
              data-testid={`dev-tab-${t.id}`}
            >
              <t.icon className="w-3.5 h-3.5" /> {t.label}
            </button>
          ))}
        </div>

        <div className="p-5">
          {tab === 'keys' && (
            <KeysTab
              keys={keys} newKey={newKey} newLabel={newLabel}
              setNewLabel={setNewLabel} setNewKey={setNewKey}
              generateKey={generateKey} revokeKey={revokeKey}
              copyToClipboard={copyToClipboard} loading={loading}
            />
          )}
          {tab === 'usage' && <UsageTab usage={usage} used={used} limit={limit} pct={pct} />}
          {tab === 'docs' && <DocsTab isPro={isPro} />}
        </div>
      </div>
    </div>
  );
};

const KeysTab = ({ keys, newKey, newLabel, setNewLabel, setNewKey, generateKey, revokeKey, copyToClipboard, loading }) => (
  <div>
    {/* New key just generated */}
    {newKey && (
      <div className="bg-lime-900/20 border border-lime-600/30 rounded-xl p-4 mb-4" data-testid="new-key-banner">
        <p className="text-lime-400 text-xs font-bold mb-2">New API Key Generated — Copy it now!</p>
        <div className="flex items-center gap-2">
          <code className="flex-1 bg-slate-800 rounded-lg px-3 py-2 text-[#3DE8D9] text-xs font-mono break-all" data-testid="new-key-value">{newKey}</code>
          <Button size="sm" onClick={() => copyToClipboard(newKey)} className="bg-slate-700 border-slate-600 text-white shrink-0">
            <Copy className="w-3.5 h-3.5" />
          </Button>
        </div>
        <p className="text-slate-400 text-[9px] mt-2">This key will not be shown again in full. Store it securely.</p>
        <button onClick={() => setNewKey(null)} className="text-slate-500 text-[10px] mt-1 hover:text-slate-300">Dismiss</button>
      </div>
    )}

    {/* Generate new key */}
    <div className="flex items-center gap-2 mb-5">
      <Input
        value={newLabel}
        onChange={(e) => setNewLabel(e.target.value)}
        placeholder="Key label (e.g. My App)"
        className="bg-slate-800 border-slate-600 text-white placeholder-slate-500 text-xs h-9"
        data-testid="key-label-input"
      />
      <Button size="sm" onClick={generateKey} className="bg-violet-600 hover:bg-violet-500 text-white h-9 shrink-0" data-testid="generate-key-btn">
        <Plus className="w-3.5 h-3.5 mr-1" /> Generate
      </Button>
    </div>

    {/* Keys list */}
    <div className="space-y-2" data-testid="keys-list">
      {loading ? (
        <div className="space-y-2">{[1,2].map(i => <div key={`skel-${i}`} className="h-14 bg-slate-800/50 rounded-xl animate-pulse" />)}</div>
      ) : keys.length === 0 ? (
        <div className="text-center py-8">
          <Key className="w-8 h-8 text-slate-600 mx-auto mb-2" />
          <p className="text-slate-400 text-xs">No API keys yet. Generate one to get started.</p>
        </div>
      ) : (
        keys.map(k => (
          <div key={k.key_id} className={`flex items-center justify-between bg-slate-800/50 rounded-xl px-4 py-3 border ${k.is_active ? 'border-slate-600/30' : 'border-red-800/30 opacity-60'}`}>
            <div className="flex items-center gap-3">
              <Key className={`w-4 h-4 ${k.is_active ? 'text-[#3DE8D9]' : 'text-red-400'}`} />
              <div>
                <p className="text-white text-xs font-medium">{k.label || 'Unnamed'}</p>
                <p className="text-slate-400 text-[10px] font-mono">{k.key_preview}</p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <div className="text-right">
                <p className="text-slate-300 text-[10px]">{(k.total_calls || 0).toLocaleString()} calls</p>
                <p className="text-slate-500 text-[9px]">{k.last_used ? `Last: ${k.last_used.split('T')[0]}` : 'Never used'}</p>
              </div>
              {k.is_active && (
                <button onClick={() => revokeKey(k.key_id)} className="text-red-400 hover:text-red-300 p-1" title="Revoke" data-testid={`revoke-key-${k.key_id}`}>
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              )}
              {!k.is_active && <span className="text-red-400 text-[9px] font-bold">REVOKED</span>}
            </div>
          </div>
        ))
      )}
    </div>
  </div>
);

const UsageTab = ({ usage, used, limit, pct }) => (
  <div>
    {/* Today's usage */}
    <div className="bg-slate-800/50 rounded-xl p-4 mb-5 border border-slate-600/20">
      <div className="flex items-center justify-between mb-2">
        <span className="text-slate-400 text-[10px]">Today's Usage</span>
        <span className="text-white text-sm font-bold">{used.toLocaleString()} / {limit.toLocaleString()}</span>
      </div>
      <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all duration-500 ${pct > 80 ? 'bg-red-500' : pct > 50 ? 'bg-amber-400' : 'bg-[#3DE8D9]'}`} style={{ width: `${pct}%` }} data-testid="usage-bar" />
      </div>
      <p className="text-slate-500 text-[9px] mt-1">{usage?.remaining || 0} remaining &middot; Resets at midnight UTC</p>
    </div>

    {/* 7-day history */}
    <h3 className="text-white text-xs font-semibold mb-3">Last 7 Days</h3>
    <div className="space-y-1.5" data-testid="usage-history">
      {(usage?.history || []).map(d => (
        <div key={d.date} className="flex items-center justify-between bg-slate-800/30 rounded-lg px-3 py-2">
          <span className="text-slate-300 text-[10px]">{d.date}</span>
          <div className="flex items-center gap-2">
            <div className="w-24 h-1.5 bg-slate-700 rounded-full overflow-hidden">
              <div className="h-full bg-[#3DE8D9] rounded-full" style={{ width: `${Math.min((d.count / (d.limit || limit)) * 100, 100)}%` }} />
            </div>
            <span className="text-slate-400 text-[10px] w-12 text-right">{d.count}</span>
          </div>
        </div>
      ))}
      {(!usage?.history || usage.history.length === 0) && (
        <p className="text-slate-500 text-[10px] text-center py-4">No usage data yet</p>
      )}
    </div>
  </div>
);

const DocsTab = ({ isPro }) => (
  <div className="space-y-4" data-testid="api-docs">
    {/* Base URL */}
    <div>
      <h3 className="text-white text-xs font-semibold mb-2">Base URL</h3>
      <code className="block bg-slate-800 rounded-lg px-3 py-2 text-[#3DE8D9] text-xs font-mono">{getApiBase()}/api/v1</code>
    </div>

    {/* Auth */}
    <div>
      <h3 className="text-white text-xs font-semibold mb-2">Authentication</h3>
      <p className="text-slate-400 text-[10px] mb-2">Include your API key in the <code className="text-[#3DE8D9]">X-API-Key</code> header:</p>
      <code className="block bg-slate-800 rounded-lg px-3 py-2 text-slate-300 text-[10px] font-mono whitespace-pre">{`curl -H "X-API-Key: rsd_live_your_key_here" \\
  ${getApiBase()}/api/v1/watchlist`}</code>
    </div>

    {/* Rate limits */}
    <div>
      <h3 className="text-white text-xs font-semibold mb-2">Rate Limits</h3>
      <div className="grid grid-cols-2 gap-2">
        <div className="bg-slate-800/50 rounded-lg p-3 border border-slate-600/20">
          <p className="text-slate-400 text-[9px]">Free Tier</p>
          <p className="text-white text-sm font-bold">100 <span className="text-slate-400 text-[9px] font-normal">calls/day</span></p>
        </div>
        <div className="bg-slate-800/50 rounded-lg p-3 border border-[#3DE8D9]/20">
          <p className="text-[#3DE8D9] text-[9px]">Pro Tier</p>
          <p className="text-white text-sm font-bold">5,000 <span className="text-slate-400 text-[9px] font-normal">calls/day</span></p>
        </div>
      </div>
    </div>

    {/* Endpoints */}
    <div>
      <h3 className="text-white text-xs font-semibold mb-2">Endpoints</h3>
      <div className="space-y-2">
        <Endpoint method="GET" path="/watchlist" desc="Your watchlist tickers with live prices" tier="all" />
        <Endpoint method="GET" path="/predictions" desc="Recent AI market predictions" tier="all" />
        <Endpoint method="GET" path="/predictions/{symbol}" desc="Predictions for a specific symbol" tier="all" />
        <Endpoint method="GET" path="/market/fear-greed" desc="Current Fear & Greed index" tier="all" />
        <Endpoint method="GET" path="/market/sectors" desc="Sector heatmap performance" tier="all" />
        <Endpoint method="GET" path="/signals/war-room/{symbol}" desc="AI War Room analysis" tier="pro" />
        <Endpoint method="GET" path="/signals/hypothesis/{symbol}" desc="AI Investment Hypothesis" tier="pro" />
      </div>
    </div>

    {/* Example */}
    <div>
      <h3 className="text-white text-xs font-semibold mb-2">Example Response</h3>
      <code className="block bg-slate-800 rounded-lg px-3 py-2 text-slate-300 text-[10px] font-mono whitespace-pre overflow-x-auto">{`{
  "tickers": ["AAPL", "TSLA", "NVDA"],
  "count": 3,
  "prices": [
    {"symbol": "AAPL", "price": 198.52, "change": 1.23, ...},
    ...
  ]
}`}</code>
    </div>
  </div>
);

const Endpoint = ({ method, path, desc, tier }) => (
  <div className="flex items-center gap-2 bg-slate-800/30 rounded-lg px-3 py-2">
    <span className="text-lime-400 text-[10px] font-bold font-mono w-8">{method}</span>
    <code className="text-[#3DE8D9] text-[10px] font-mono flex-1">/v1{path}</code>
    <span className="text-slate-400 text-[9px]">{desc}</span>
    {tier === 'pro' && <span className="text-[8px] font-bold uppercase px-1.5 py-0.5 rounded bg-violet-500/15 text-violet-400 shrink-0">PRO</span>}
  </div>
);

export default DeveloperPortal;
