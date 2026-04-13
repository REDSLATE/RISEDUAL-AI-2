import React, { useState, useEffect, useCallback } from 'react';
import { Coins, Zap, Crown, ArrowRight, Check, History, X, ShoppingCart, Star } from 'lucide-react';
import { Button } from './ui/button';
import { toast } from './ui/sonner';
import { authFetch, useAuth } from '../contexts/AuthContext';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

const ACTION_LABELS = {
  ai_chat: 'AI Chat',
  war_room: 'War Room',
  ai_hypothesis: 'AI Hypothesis',
  market_prediction: 'Market Prediction',
  ai_intelligence: 'AI Score / Patterns / Brief',
  scanner_validation: 'Scanner + AI Validation',
  api_call: 'Developer API Call',
};

const CreditStore = ({ onClose, onSubscribe }) => {
  const { user } = useAuth();
  const [balance, setBalance] = useState(null);
  const [topups, setTopups] = useState([]);
  const [costs, setCosts] = useState(null);
  const [events, setEvents] = useState([]);
  const [tab, setTab] = useState('topup');
  const [purchasing, setPurchasing] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const [bRes, tRes, cRes] = await Promise.all([
        authFetch(`${API}/credits/balance`),
        authFetch(`${API}/credits/topups`),
        authFetch(`${API}/credits/costs`),
      ]);
      if (bRes.ok) setBalance(await bRes.json());
      if (tRes.ok) { const d = await tRes.json(); setTopups(d.topups || []); }
      if (cRes.ok) setCosts(await cRes.json());
    } catch (e) {
      logger.warn('Credit store fetch error:', e);
    }
  }, []);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/credits/history?limit=20`);
      if (res.ok) { const d = await res.json(); setEvents(d.events || []); }
    } catch (e) {
      logger.warn('Credit history error:', e);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const buyTopup = async (topupId) => {
    setPurchasing(topupId);
    try {
      const res = await authFetch(`${API}/billing/checkout/topup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pack: topupId }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.checkout_url) {
          window.location.href = data.checkout_url;
        } else {
          toast.error('No checkout URL returned');
        }
      } else {
        const d = await res.json();
        toast.error(d.detail || 'Checkout failed');
      }
    } catch (e) {
      toast.error('Checkout error');
    } finally {
      setPurchasing(null);
    }
  };

  const credits = balance?.credits || 0;
  const planKey = balance?.plan_key || 'free';
  const planLabel = balance?.plan_label || 'Free';
  const monthlyCredits = balance?.monthly_credits || 50;
  const isPro = planKey === 'pro' || planKey === 'pro_max';
  const lowCredits = credits < 10 && credits > 0;

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
                <p className="text-slate-400 text-[10px]">{planLabel} &middot; {monthlyCredits.toLocaleString()} credits/month</p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <div className={`rounded-xl px-4 py-2 border ${lowCredits ? 'bg-red-900/20 border-red-500/30' : 'bg-slate-800 border-slate-600/30'}`} data-testid="credit-balance-display">
                <p className="text-[9px] text-slate-400">Balance</p>
                <p className={`text-xl font-bold ${lowCredits ? 'text-red-400' : 'text-amber-400'}`}>{credits.toLocaleString()}</p>
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
            { id: 'topup', label: 'Buy Credits', icon: ShoppingCart },
            { id: 'costs', label: 'Credit Costs', icon: Zap },
            { id: 'history', label: 'History', icon: History },
          ].map(t => (
            <button key={t.id} onClick={() => { setTab(t.id); if (t.id === 'history' && events.length === 0) fetchHistory(); }}
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
          {tab === 'topup' && (
            <div>
              {/* Upgrade upsell for non-Pro */}
              {!isPro && (
                <div className="bg-gradient-to-r from-violet-900/30 to-purple-900/20 border border-violet-500/30 rounded-xl p-4 mb-5" data-testid="pro-upsell">
                  <div className="flex items-center gap-2 mb-2">
                    <Crown className="w-4 h-4 text-violet-400" />
                    <span className="text-violet-300 text-xs font-bold">
                      {planKey === 'free' ? 'Upgrade for more credits & better rates' : 'Go Pro for unlimited Chat & War Room'}
                    </span>
                  </div>
                  <p className="text-slate-300 text-[10px] mb-3">
                    Pro: 15,000 credits/month + unlimited AI Chat & War Room for $55/mo
                  </p>
                  <Button size="sm" onClick={() => { onClose(); onSubscribe?.(); }} className="bg-violet-600 hover:bg-violet-500 text-white h-8 text-xs" data-testid="pro-upsell-btn">
                    <Crown className="w-3.5 h-3.5 mr-1" /> View Plans
                  </Button>
                </div>
              )}

              {/* Top-up rate info */}
              <div className="bg-slate-800/40 rounded-lg px-3 py-2 mb-4 flex items-center gap-2">
                <Star className="w-3.5 h-3.5 text-amber-400" />
                <p className="text-slate-400 text-[10px]">
                  Your top-up rate: <span className="text-white font-semibold">${balance?.topup_rate || 15}/1,000 credits</span>
                  {!isPro && <span className="text-slate-500"> &middot; Upgrade for better rates</span>}
                </p>
              </div>

              {/* Top-up packs */}
              <div className="grid grid-cols-2 gap-3" data-testid="topup-packs">
                {topups.map(p => (
                  <div key={p.id} className="bg-slate-800/50 rounded-xl p-4 border border-slate-600/20 hover:border-slate-500/40 transition-all">
                    <div className="flex items-center justify-between mb-2">
                      <p className="text-amber-400 text-sm font-bold">{p.label}</p>
                      <p className="text-white text-lg font-bold">${p.price}</p>
                    </div>
                    <Button
                      size="sm"
                      onClick={() => buyTopup(p.id)}
                      disabled={purchasing === p.id}
                      className="w-full bg-slate-700 hover:bg-slate-600 text-white h-8 text-xs mt-2"
                      data-testid={`buy-topup-${p.id}`}
                    >
                      {purchasing === p.id ? 'Processing...' : 'Buy'}
                    </Button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {tab === 'costs' && costs && (
            <div data-testid="credit-costs">
              <p className="text-slate-400 text-[10px] mb-4">Pro includes unlimited Chat and War Room. Advanced AI actions use credits on every plan.</p>
              <div className="space-y-2">
                {Object.entries(costs.costs || {}).map(([action, info]) => (
                  <div key={action} className="flex items-center justify-between bg-slate-800/30 rounded-lg px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      <Zap className="w-3.5 h-3.5 text-amber-400" />
                      <span className="text-slate-300 text-xs">{ACTION_LABELS[action] || action}</span>
                    </div>
                    {info.unlimited ? (
                      <span className="text-lime-400 text-xs font-bold flex items-center gap-1">
                        <Check className="w-3 h-3" /> Unlimited
                      </span>
                    ) : (
                      <span className="text-amber-400 text-xs font-bold">{info.cost} credits</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {tab === 'history' && (
            <div data-testid="credit-history">
              {events.length === 0 ? (
                <p className="text-slate-500 text-xs text-center py-8">No credit activity yet</p>
              ) : (
                <div className="space-y-1.5">
                  {events.map((e, i) => (
                    <div key={`evt-${i}`} className="flex items-center justify-between bg-slate-800/30 rounded-lg px-3 py-2">
                      <div>
                        <p className="text-slate-300 text-[10px] font-medium">{e.description}</p>
                        <p className="text-slate-500 text-[9px]">{e.created_at?.split('T')[0]}</p>
                      </div>
                      <span className={`text-xs font-bold ${e.credits_charged > 0 ? 'text-lime-400' : e.credits_charged < 0 ? 'text-red-400' : 'text-slate-400'}`}>
                        {e.credits_charged > 0 ? '+' : ''}{e.credits_charged}
                        {e.was_unlimited && <span className="text-lime-400 text-[9px] ml-1">FREE</span>}
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
