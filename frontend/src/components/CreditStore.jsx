import React, { useState, useEffect, useCallback } from 'react';
import { Coins, Zap, Crown, Package, ArrowRight, Check, History, X, ShoppingCart } from 'lucide-react';
import { Button } from './ui/button';
import { toast } from './ui/sonner';
import { authFetch, useAuth } from '../contexts/AuthContext';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

const CreditStore = ({ onClose, onSubscribe }) => {
  const { user } = useAuth();
  const [balance, setBalance] = useState(null);
  const [packs, setPacks] = useState([]);
  const [history, setHistory] = useState([]);
  const [costs, setCosts] = useState(null);
  const [tab, setTab] = useState('buy');
  const [purchasing, setPurchasing] = useState(null);

  const isPro = user?.subscription_status === 'pro';

  const fetchData = useCallback(async () => {
    try {
      const [bRes, pRes, cRes] = await Promise.all([
        authFetch(`${API}/credits/balance`),
        authFetch(`${API}/credits/packs`),
        authFetch(`${API}/credits/costs`),
      ]);
      if (bRes.ok) setBalance(await bRes.json());
      if (pRes.ok) { const d = await pRes.json(); setPacks(d.packs || []); }
      if (cRes.ok) setCosts(await cRes.json());
    } catch (e) {
      logger.warn('Credit store fetch error:', e);
    }
  }, []);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/credits/history?limit=20`);
      if (res.ok) { const d = await res.json(); setHistory(d.transactions || []); }
    } catch (e) {
      logger.warn('Credit history fetch error:', e);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const purchasePack = async (packId) => {
    setPurchasing(packId);
    try {
      const res = await authFetch(`${API}/credits/purchase`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pack_id: packId }),
      });
      if (res.ok) {
        const data = await res.json();
        toast.success(`Added ${data.credits_added} credits! Balance: ${data.new_balance}`);
        fetchData();
      } else {
        const d = await res.json();
        toast.error(d.detail || 'Purchase failed');
      }
    } catch (e) {
      toast.error('Purchase error');
    } finally {
      setPurchasing(null);
    }
  };

  const credits = balance?.credits || 0;

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-start justify-center overflow-y-auto p-4" data-testid="credit-store">
      <div className="bg-slate-900 rounded-2xl max-w-2xl w-full my-4 border border-slate-400/25">
        {/* Header */}
        <div className="p-5 border-b border-slate-400/30">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 bg-amber-500/20 rounded-xl flex items-center justify-center">
                <Coins className="w-5 h-5 text-amber-400" />
              </div>
              <div>
                <h2 className="text-white text-lg font-bold">AI Credits</h2>
                <p className="text-slate-400 text-[10px]">{isPro ? 'Pro Member' : 'Free Tier'}</p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <div className="bg-slate-800 rounded-xl px-4 py-2 border border-slate-600/30" data-testid="credit-balance-display">
                <p className="text-[9px] text-slate-400">Balance</p>
                <p className="text-xl font-bold text-amber-400">{credits.toLocaleString()}</p>
              </div>
              <button onClick={onClose} className="text-slate-400 hover:text-white" data-testid="credit-store-close">
                <X className="w-5 h-5" />
              </button>
            </div>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-slate-400/25 px-4">
          {[
            { id: 'buy', label: 'Buy Credits', icon: ShoppingCart },
            { id: 'costs', label: 'Credit Costs', icon: Zap },
            { id: 'history', label: 'History', icon: History },
          ].map(t => (
            <button key={t.id} onClick={() => { setTab(t.id); if (t.id === 'history' && history.length === 0) fetchHistory(); }}
              className={`flex items-center gap-1.5 px-3 py-3 text-xs font-medium border-b-2 transition-all ${
                tab === t.id ? 'text-amber-400 border-amber-400' : 'text-slate-400 border-transparent hover:text-slate-300'
              }`}
              data-testid={`credit-tab-${t.id}`}
            >
              <t.icon className="w-3.5 h-3.5" /> {t.label}
            </button>
          ))}
        </div>

        <div className="p-5">
          {tab === 'buy' && (
            <div>
              {/* Pro upsell */}
              {!isPro && (
                <div className="bg-gradient-to-r from-violet-900/30 to-purple-900/20 border border-violet-500/30 rounded-xl p-4 mb-5" data-testid="pro-upsell">
                  <div className="flex items-center gap-2 mb-2">
                    <Crown className="w-4 h-4 text-violet-400" />
                    <span className="text-violet-300 text-xs font-bold">Best Value: Go Pro</span>
                  </div>
                  <p className="text-slate-300 text-[10px] mb-3">Get 5,000 credits/month + unlimited AI Chat & War Room for $55/month</p>
                  <Button size="sm" onClick={() => { onClose(); onSubscribe?.(); }} className="bg-violet-600 hover:bg-violet-500 text-white h-8 text-xs" data-testid="pro-upsell-btn">
                    <Crown className="w-3.5 h-3.5 mr-1" /> Upgrade to Pro — $55/mo
                  </Button>
                </div>
              )}

              {/* Credit packs */}
              <h3 className="text-white text-xs font-semibold mb-3">Credit Packs</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3" data-testid="credit-packs">
                {packs.map(p => (
                  <div key={p.id} className={`bg-slate-800/50 rounded-xl p-4 border ${p.id === 'power' ? 'border-amber-500/30' : 'border-slate-600/20'} hover:border-slate-500/40 transition-all`}>
                    {p.id === 'power' && <span className="text-[8px] font-bold uppercase text-amber-400 bg-amber-500/15 px-1.5 py-0.5 rounded mb-2 inline-block">Best Value</span>}
                    <div className="flex items-center justify-between mb-2">
                      <div>
                        <p className="text-white text-sm font-bold">{p.name}</p>
                        <p className="text-amber-400 text-xs font-semibold">{p.credits.toLocaleString()} credits</p>
                      </div>
                      <p className="text-white text-lg font-bold">${p.price}</p>
                    </div>
                    <p className="text-slate-500 text-[9px] mb-3">${(p.per_credit * 100).toFixed(1)} per credit</p>
                    <Button
                      size="sm"
                      onClick={() => purchasePack(p.id)}
                      disabled={purchasing === p.id}
                      className="w-full bg-slate-700 hover:bg-slate-600 text-white h-8 text-xs"
                      data-testid={`buy-pack-${p.id}`}
                    >
                      {purchasing === p.id ? 'Processing...' : `Buy ${p.name}`}
                    </Button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {tab === 'costs' && costs && (
            <div data-testid="credit-costs">
              <h3 className="text-white text-xs font-semibold mb-3">Credits Per Action</h3>
              <div className="space-y-2">
                {Object.entries(costs.costs || {}).map(([action, cost]) => {
                  const isFree = isPro && (costs.pro_free || []).includes(action);
                  const labels = {
                    chat: 'AI Chat Message',
                    war_room: 'War Room Analysis',
                    hypothesis: 'AI Hypothesis',
                    prediction: 'Market Prediction',
                    intelligence: 'AI Score / Patterns / Brief',
                    scanner_validate: 'Scanner AI Validation',
                    api_call: 'Developer API Call',
                  };
                  return (
                    <div key={action} className="flex items-center justify-between bg-slate-800/30 rounded-lg px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        <Zap className="w-3.5 h-3.5 text-amber-400" />
                        <span className="text-slate-300 text-xs">{labels[action] || action}</span>
                      </div>
                      {isFree ? (
                        <span className="text-lime-400 text-xs font-bold flex items-center gap-1">
                          <Check className="w-3 h-3" /> FREE with Pro
                        </span>
                      ) : (
                        <span className="text-amber-400 text-xs font-bold">{cost} credits</span>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {tab === 'history' && (
            <div data-testid="credit-history">
              {history.length === 0 ? (
                <p className="text-slate-500 text-xs text-center py-8">No credit transactions yet</p>
              ) : (
                <div className="space-y-1.5">
                  {history.map((t, i) => (
                    <div key={`txn-${i}`} className="flex items-center justify-between bg-slate-800/30 rounded-lg px-3 py-2">
                      <div>
                        <p className="text-slate-300 text-[10px] font-medium">{t.description}</p>
                        <p className="text-slate-500 text-[9px]">{t.timestamp?.split('T')[0]}</p>
                      </div>
                      <span className={`text-xs font-bold ${t.amount > 0 ? 'text-lime-400' : 'text-red-400'}`}>
                        {t.amount > 0 ? '+' : ''}{t.amount}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default CreditStore;
