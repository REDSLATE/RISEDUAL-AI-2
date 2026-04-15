import React, { useState, useEffect, useCallback } from 'react';
import { Key, Plus, Trash2, RefreshCw, Eye, EyeOff, Shield, Lock, AlertTriangle, Check } from 'lucide-react';
import { Card } from '../ui/card';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { toast } from '../ui/sonner';
import { authFetch } from '../../contexts/AuthContext';
import logger from '../../utils/logger';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

const CATEGORIES = [
  { value: 'ai', label: 'AI Provider', color: 'text-[#3DE8D9] bg-[#3DE8D9]/10 border-[#3DE8D9]/20' },
  { value: 'market_data', label: 'Market Data', color: 'text-blue-400 bg-blue-500/10 border-blue-500/20' },
  { value: 'email', label: 'Email', color: 'text-purple-400 bg-purple-500/10 border-purple-500/20' },
  { value: 'search', label: 'Search', color: 'text-amber-400 bg-amber-500/10 border-amber-500/20' },
  { value: 'general', label: 'General', color: 'text-slate-400 bg-slate-500/10 border-slate-500/20' },
];

const COMMON_KEYS = [
  { name: 'OPENAI_API_KEY', category: 'ai', description: 'OpenAI GPT-4.1 backup', helpUrl: 'https://platform.openai.com/api-keys' },
  { name: 'ANTHROPIC_API_KEY', category: 'ai', description: 'Anthropic Claude Sonnet 4 backup', helpUrl: 'https://console.anthropic.com/settings/keys' },
  { name: 'OPENROUTER_API_KEY', category: 'ai', description: 'OpenRouter multi-model access', helpUrl: 'https://openrouter.ai/keys' },
  { name: 'TWELVEDATA_API_KEY', category: 'market_data', description: 'TwelveData market data backup', helpUrl: 'https://twelvedata.com/account/api-keys' },
  { name: 'FRED_API_KEYS', category: 'market_data', description: 'FRED macroeconomic data (comma-separated)', helpUrl: 'https://fred.stlouisfed.org/docs/api/api_key.html' },
  { name: 'SENDGRID_API_KEY', category: 'email', description: 'SendGrid email backup', helpUrl: 'https://app.sendgrid.com/settings/api_keys' },
  { name: 'TAVILY_API_KEY', category: 'search', description: 'Tavily advanced web search', helpUrl: 'https://app.tavily.com/home' },
];

const getCategoryConfig = (cat) => CATEGORIES.find(c => c.value === cat) || CATEGORIES[4];

const KeyVault = () => {
  const [keys, setKeys] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [newKey, setNewKey] = useState({ name: '', value: '', category: 'general', description: '' });
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(null);
  const [validating, setValidating] = useState(false);
  const [validateResult, setValidateResult] = useState(null);

  const fetchKeys = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authFetch(`${API}/vault/keys`);
      if (res.ok) {
        const data = await res.json();
        setKeys(data.keys || []);
      }
    } catch (e) {
      logger.error('Vault fetch error:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchKeys(); }, [fetchKeys]);

  const storeKey = async () => {
    if (!newKey.name || !newKey.value) {
      toast.error('Name and value are required');
      return;
    }
    setSaving(true);
    try {
      const res = await authFetch(`${API}/vault/keys`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newKey),
      });
      if (res.ok) {
        toast.success(`${newKey.name} stored and providers reloaded`);
        setNewKey({ name: '', value: '', category: 'general', description: '' });
        setShowAdd(false);
        fetchKeys();
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(err.detail || 'Failed to store key');
      }
    } catch (e) {
      toast.error('Failed to store key');
    } finally {
      setSaving(false);
    }
  };

  const deleteKey = async (name) => {
    setDeleting(name);
    try {
      const res = await authFetch(`${API}/vault/keys/${name}`, { method: 'DELETE' });
      if (res.ok) {
        toast.success(`${name} deleted`);
        fetchKeys();
      } else {
        toast.error('Failed to delete key');
      }
    } catch (e) {
      toast.error('Failed to delete key');
    } finally {
      setDeleting(null);
    }
  };

  const selectCommonKey = (common) => {
    setNewKey(prev => ({
      ...prev,
      name: common.name,
      category: common.category,
      description: common.description,
    }));
    setValidateResult(null);
  };

  const validateKey = async () => {
    if (!newKey.name || !newKey.value) return;
    setValidating(true);
    setValidateResult(null);
    try {
      const res = await authFetch(`${API}/vault/keys/validate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newKey),
      });
      const data = await res.json();
      setValidateResult(data);
      if (data.valid === true) toast.success(data.message || 'Key validated');
      else if (data.valid === false) toast.error(data.message || data.error || 'Validation failed');
      else toast.info(data.message || 'No validator — key will be stored as-is');
    } catch (e) {
      setValidateResult({ valid: false, error: 'Validation request failed' });
      toast.error('Validation request failed');
    } finally {
      setValidating(false);
    }
  };

  const getHelpUrl = () => {
    const common = COMMON_KEYS.find(k => k.name === newKey.name);
    return common?.helpUrl || null;
  };

  if (loading && keys.length === 0) {
    return (
      <div className="p-6 text-center text-slate-400">
        <RefreshCw className="w-5 h-5 animate-spin mx-auto mb-2" />
        Loading vault...
      </div>
    );
  }

  const storedNames = new Set(keys.map(k => k.name));
  const missingKeys = COMMON_KEYS.filter(k => !storedNames.has(k.name));

  return (
    <div data-testid="key-vault-tab" className="p-4 sm:p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Lock className="w-4 h-4 text-[#3DE8D9]" />
          <span className="text-white text-sm font-semibold">{keys.length} keys stored</span>
          <Badge variant="outline" className="text-[9px] bg-emerald-900/30 text-emerald-400 border-emerald-500/30">AES-256-GCM</Badge>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={fetchKeys} disabled={loading}
            className="bg-slate-800 border-slate-600 text-slate-300 hover:text-white text-xs h-7">
            <RefreshCw className={`w-3 h-3 mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </Button>
          <Button size="sm" onClick={() => setShowAdd(!showAdd)}
            className="bg-[#3DE8D9] hover:bg-[#3DE8D9]/80 text-black text-xs h-7 font-semibold">
            <Plus className="w-3 h-3 mr-1" /> Add Key
          </Button>
        </div>
      </div>

      {/* Missing keys alert */}
      {missingKeys.length > 0 && (
        <div className="bg-amber-500/[0.06] border border-amber-400/15 rounded-xl p-3" data-testid="missing-keys-alert">
          <div className="flex items-center gap-1.5 mb-2">
            <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />
            <span className="text-[11px] text-amber-300 font-semibold uppercase tracking-wider">Unconfigured Providers</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {missingKeys.map(k => (
              <button key={k.name} onClick={() => { selectCommonKey(k); setShowAdd(true); }}
                className="text-[10px] px-2 py-1 rounded-lg bg-slate-800/60 text-slate-300 border border-slate-700/40 hover:border-amber-400/40 hover:text-amber-300 transition-all cursor-pointer"
                data-testid={`missing-key-${k.name}`}>
                {k.name}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Add key form */}
      {showAdd && (
        <Card className="bg-slate-800/60 border-slate-700/50 p-4 space-y-3" data-testid="add-key-form">
          <div className="flex items-center gap-2 mb-1">
            <Key className="w-4 h-4 text-[#3DE8D9]" />
            <span className="text-white text-sm font-semibold">Store New Key</span>
          </div>

          {/* Quick select */}
          {!newKey.name && (
            <div className="space-y-1.5">
              <span className="text-[10px] text-slate-500 uppercase tracking-wider">Quick Select</span>
              <div className="flex flex-wrap gap-1.5">
                {COMMON_KEYS.filter(k => !storedNames.has(k.name)).map(k => {
                  const cat = getCategoryConfig(k.category);
                  return (
                    <button key={k.name} onClick={() => selectCommonKey(k)}
                      className={`text-[10px] px-2 py-1 rounded-lg border transition-all cursor-pointer ${cat.color}`}>
                      {k.name}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Name</label>
              <input value={newKey.name} onChange={e => setNewKey(p => ({ ...p, name: e.target.value }))}
                placeholder="OPENAI_API_KEY" className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-white placeholder-slate-500 focus:border-[#3DE8D9] focus:outline-none font-mono"
                data-testid="vault-key-name" />
            </div>
            <div>
              <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Category</label>
              <select value={newKey.category} onChange={e => setNewKey(p => ({ ...p, category: e.target.value }))}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-white focus:border-[#3DE8D9] focus:outline-none"
                data-testid="vault-key-category">
                {CATEGORIES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select>
            </div>
          </div>

          <div>
            <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">API Key Value</label>
            <input type="password" value={newKey.value} onChange={e => { setNewKey(p => ({ ...p, value: e.target.value })); setValidateResult(null); }}
              placeholder="sk-..." className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-white placeholder-slate-500 focus:border-[#3DE8D9] focus:outline-none font-mono"
              data-testid="vault-key-value" />
            {getHelpUrl() && (
              <a href={getHelpUrl()} target="_blank" rel="noopener noreferrer"
                className="text-[10px] text-[#3DE8D9]/70 hover:text-[#3DE8D9] mt-1 inline-flex items-center gap-1">
                Get your {newKey.name.replace(/_/g, ' ').replace('API KEY', '').replace('API KEYS', '').trim()} key &rarr;
              </a>
            )}
          </div>

          <div>
            <label className="text-[10px] text-slate-500 uppercase tracking-wider block mb-1">Description (optional)</label>
            <input value={newKey.description} onChange={e => setNewKey(p => ({ ...p, description: e.target.value }))}
              placeholder="OpenAI GPT-4.1 backup" className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-white placeholder-slate-500 focus:border-[#3DE8D9] focus:outline-none"
              data-testid="vault-key-desc" />
          </div>

          {/* Validation result */}
          {validateResult && (
            <div className={`flex items-center gap-2 text-xs px-3 py-2 rounded-lg ${
              validateResult.valid === true ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' :
              validateResult.valid === false ? 'bg-red-500/10 text-red-400 border border-red-500/20' :
              'bg-blue-500/10 text-blue-400 border border-blue-500/20'
            }`} data-testid="validate-result">
              {validateResult.valid === true ? <Check className="w-3.5 h-3.5" /> :
               validateResult.valid === false ? <AlertTriangle className="w-3.5 h-3.5" /> :
               <Shield className="w-3.5 h-3.5" />}
              <span>{validateResult.message || validateResult.error || 'Unknown result'}</span>
              {validateResult.status && <span className="font-mono text-[10px] opacity-70">HTTP {validateResult.status}</span>}
            </div>
          )}

          <div className="flex gap-2 pt-1">
            <Button size="sm" onClick={validateKey} disabled={validating || !newKey.name || !newKey.value}
              className="bg-slate-700 hover:bg-slate-600 text-white text-xs font-semibold border border-slate-600" data-testid="vault-validate-btn">
              {validating ? <RefreshCw className="w-3 h-3 animate-spin mr-1" /> : <Eye className="w-3 h-3 mr-1" />}
              {validating ? 'Testing...' : 'Validate'}
            </Button>
            <Button size="sm" onClick={storeKey} disabled={saving || !newKey.name || !newKey.value}
              className="bg-[#3DE8D9] hover:bg-[#3DE8D9]/80 text-black text-xs font-semibold" data-testid="vault-store-btn">
              {saving ? <RefreshCw className="w-3 h-3 animate-spin mr-1" /> : <Shield className="w-3 h-3 mr-1" />}
              {saving ? 'Encrypting...' : 'Encrypt & Store'}
            </Button>
            <Button size="sm" variant="outline" onClick={() => { setShowAdd(false); setNewKey({ name: '', value: '', category: 'general', description: '' }); setValidateResult(null); }}
              className="bg-slate-800 border-slate-600 text-slate-300 text-xs">
              Cancel
            </Button>
          </div>
        </Card>
      )}

      {/* Stored keys list */}
      {keys.length === 0 ? (
        <Card className="bg-slate-800/40 border-slate-700/50 p-8 text-center">
          <Lock className="w-8 h-8 text-slate-600 mx-auto mb-3" />
          <p className="text-slate-400 text-sm">No keys stored yet</p>
          <p className="text-slate-500 text-xs mt-1">Add API keys to activate provider backups</p>
        </Card>
      ) : (
        <div className="space-y-2">
          {keys.map(k => {
            const cat = getCategoryConfig(k.category);
            return (
              <div key={k.name} className="bg-slate-800/60 rounded-xl border border-slate-700/50 p-3 flex items-center justify-between hover:border-slate-600/50 transition-all"
                data-testid={`vault-key-${k.name}`}>
                <div className="flex items-center gap-3 min-w-0">
                  <div className="flex-shrink-0">
                    <Key className="w-4 h-4 text-slate-400" />
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-white text-sm font-mono font-medium truncate">{k.name}</span>
                      <Badge variant="outline" className={`text-[9px] px-1.5 py-0 ${cat.color}`}>{cat.label}</Badge>
                    </div>
                    <div className="flex items-center gap-3 mt-0.5">
                      <span className="text-slate-500 text-[11px] font-mono">{k.preview}</span>
                      {k.description && <span className="text-slate-500 text-[11px] truncate">{k.description}</span>}
                      {k.updated_at && <span className="text-slate-600 text-[10px]">Updated {new Date(k.updated_at).toLocaleDateString()}</span>}
                    </div>
                  </div>
                </div>
                <Button size="sm" variant="ghost" onClick={() => deleteKey(k.name)} disabled={deleting === k.name}
                  className="text-red-400 hover:text-red-300 hover:bg-red-500/10 h-7 w-7 p-0 flex-shrink-0"
                  data-testid={`vault-delete-${k.name}`}>
                  {deleting === k.name ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                </Button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default KeyVault;
