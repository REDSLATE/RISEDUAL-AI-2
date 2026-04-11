import React, { useState, useEffect, useCallback } from 'react';
import { Key, Shield, Eye, EyeOff, Save, Trash2, CheckCircle, AlertCircle, RefreshCw } from 'lucide-react';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { toast } from '../ui/sonner';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

const BROKER_LABELS = {
  alpaca: { name: 'Alpaca', color: 'text-amber-400', desc: 'Commission-free stock & crypto trading' },
};

const BrokerOAuthConfig = () => {
  const [brokers, setBrokers] = useState({});
  const [loading, setLoading] = useState(true);
  const [editingBroker, setEditingBroker] = useState(null);
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [showSecret, setShowSecret] = useState(false);
  const [saving, setSaving] = useState(false);

  const fetchConfig = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authFetch(`${API}/admin/broker-oauth`);
      if (res.ok) {
        const data = await res.json();
        setBrokers(data.brokers || {});
      }
    } catch {
      toast.error('Failed to load broker config');
    }
    setLoading(false);
  }, []);

  useEffect(() => { fetchConfig(); }, [fetchConfig]);

  const handleSave = async (brokerId) => {
    if (!clientId.trim() || !clientSecret.trim()) {
      toast.error('Both Client ID and Client Secret are required');
      return;
    }
    setSaving(true);
    try {
      const res = await authFetch(`${API}/admin/broker-oauth/${brokerId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_id: clientId, client_secret: clientSecret }),
      });
      if (res.ok) {
        toast.success(`${BROKER_LABELS[brokerId]?.name || brokerId} OAuth configured`);
        setEditingBroker(null);
        setClientId('');
        setClientSecret('');
        fetchConfig();
      } else {
        const d = await res.json();
        toast.error(d.detail || 'Save failed');
      }
    } catch {
      toast.error('Failed to save credentials');
    }
    setSaving(false);
  };

  const handleDelete = async (brokerId) => {
    try {
      const res = await authFetch(`${API}/admin/broker-oauth/${brokerId}`, { method: 'DELETE' });
      if (res.ok) {
        toast.success('OAuth config removed');
        fetchConfig();
      }
    } catch {
      toast.error('Delete failed');
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <RefreshCw className="w-5 h-5 text-slate-400 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-4" data-testid="broker-oauth-config">
      <div className="flex items-center gap-2 mb-2">
        <Key className="w-4 h-4 text-[#35D6C8]" />
        <h3 className="text-white text-sm font-semibold">Broker OAuth Credentials</h3>
      </div>
      <p className="text-slate-400 text-xs leading-relaxed">
        Configure OAuth Client ID and Secret for each supported broker. These are encrypted and stored securely.
        Once configured, users can connect their brokerage via one-click OAuth.
      </p>

      <div className="space-y-3">
        {Object.entries(brokers).map(([brokerId, config]) => {
          const label = BROKER_LABELS[brokerId] || { name: brokerId, color: 'text-slate-300', desc: '' };
          const isEditing = editingBroker === brokerId;

          return (
            <div key={brokerId} className={`bg-[#0B1120] border rounded-xl overflow-hidden ${config.configured ? 'border-emerald-500/30' : 'border-slate-800/60'}`}
              data-testid={`broker-config-${brokerId}`}>
              <div className="px-4 py-3 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <Shield className={`w-4 h-4 ${config.configured ? 'text-emerald-400' : 'text-slate-400'}`} />
                  <div>
                    <span className={`text-sm font-semibold ${label.color}`}>{label.name}</span>
                    <p className="text-slate-400 text-xs">{label.desc}</p>
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  {config.configured ? (
                    <>
                      <span className="text-[10px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 rounded-full px-2 py-0.5 flex items-center gap-1">
                        <CheckCircle className="w-3 h-3" /> Configured
                      </span>
                      <span className="text-slate-400 text-[10px] font-mono">{config.client_id_preview}</span>
                    </>
                  ) : (
                    <span className="text-[10px] bg-amber-500/10 text-amber-400 border border-amber-500/20 rounded-full px-2 py-0.5 flex items-center gap-1">
                      <AlertCircle className="w-3 h-3" /> Not Configured
                    </span>
                  )}
                  <Button size="sm" variant="outline" onClick={() => {
                    setEditingBroker(isEditing ? null : brokerId);
                    setClientId('');
                    setClientSecret('');
                    setShowSecret(false);
                  }}
                    className="text-xs bg-transparent border-slate-500/40 text-slate-300 hover:bg-slate-600/30 h-7"
                    data-testid={`broker-edit-${brokerId}`}>
                    {isEditing ? 'Cancel' : config.configured ? 'Update' : 'Configure'}
                  </Button>
                  {config.configured && (
                    <Button size="sm" variant="outline" onClick={() => handleDelete(brokerId)}
                      className="text-xs bg-transparent border-red-800/50 text-red-400 hover:bg-red-900/20 h-7"
                      data-testid={`broker-delete-${brokerId}`}>
                      <Trash2 className="w-3 h-3" />
                    </Button>
                  )}
                </div>
              </div>

              {isEditing && (
                <div className="px-4 pb-4 border-t border-slate-600/30/40 pt-3 space-y-3">
                  <div>
                    <label className="text-slate-400 text-xs mb-1 block">OAuth Client ID</label>
                    <Input value={clientId} onChange={e => setClientId(e.target.value)}
                      placeholder="Enter Client ID from Alpaca Developer Dashboard"
                      className="bg-slate-900 border-slate-500/40 text-white text-sm"
                      data-testid={`broker-client-id-${brokerId}`} />
                  </div>
                  <div>
                    <label className="text-slate-400 text-xs mb-1 block">OAuth Client Secret</label>
                    <div className="relative">
                      <Input type={showSecret ? 'text' : 'password'} value={clientSecret} onChange={e => setClientSecret(e.target.value)}
                        placeholder="Enter Client Secret"
                        className="bg-slate-900 border-slate-500/40 text-white text-sm pr-10"
                        data-testid={`broker-client-secret-${brokerId}`} />
                      <button onClick={() => setShowSecret(!showSecret)} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-300">
                        {showSecret ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                      </button>
                    </div>
                  </div>
                  <div className="flex items-center justify-between">
                    <p className="text-slate-400 text-[10px]">Credentials are encrypted with AES-256 before storage</p>
                    <Button onClick={() => handleSave(brokerId)} disabled={saving}
                      className="bg-[#35D6C8] hover:bg-[#35D6C8]/80 text-white text-xs h-8"
                      data-testid={`broker-save-${brokerId}`}>
                      <Save className="w-3 h-3 mr-1.5" />
                      {saving ? 'Saving...' : 'Save Credentials'}
                    </Button>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default BrokerOAuthConfig;
