import React, { useState, useEffect, useCallback } from 'react';
import { Briefcase, Star, Clock, TrendingUp, TrendingDown, Minus, Trash2, RefreshCw, X, Search, Plus, Lock, Gift, Copy, Check, Users, Mail, Bell, BellOff, Send, Loader2, Eye, Sparkles } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { useAuth, authFetch } from '../contexts/AuthContext';
import ReferralLeaderboard from './ReferralLeaderboard';
import SocialShareButtons from './SocialShareButtons';
import { usePushNotifications } from '../hooks/usePushNotifications';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';
import { toast } from 'sonner';

const API = `${getApiBase()}/api`;
const FREE_WATCHLIST_LIMIT = 3;

const getPushToggleClass = (permission, subscribed) => {
  if (permission === 'denied') return 'bg-orange-900 text-orange-400 border border-orange-700/40 cursor-not-allowed';
  if (subscribed) return 'bg-lime-700 text-lime-400 border border-emerald-700/50 hover:bg-emerald-800/40';
  return 'bg-slate-700 text-slate-400 border border-slate-600 hover:bg-slate-600';
};

const PushToggleLabel = ({ permission, subscribed }) => {
  if (permission === 'denied') return <><BellOff className="w-3.5 h-3.5" /> Blocked</>;
  if (subscribed) return <><Bell className="w-3.5 h-3.5" /> Enabled</>;
  return <><BellOff className="w-3.5 h-3.5" /> Enable</>;
};

const UserWorkspace = ({ onClose, onSubscribe }) => {
  const { user, isPro } = useAuth();
  const [tab, setTab] = useState('watchlist');
  const [watchlist, setWatchlist] = useState([]);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [addTicker, setAddTicker] = useState('');
  const [addLoading, setAddLoading] = useState(false);
  const [addError, setAddError] = useState('');

  // authFetch and API are module-level constants — stable across renders
  const fetchWatchlist = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/workspace/watchlist`);
      if (res.ok) {
        const data = await res.json();
        setWatchlist(data.tickers || []);
      }
    } catch (e) {
      logger.error('Watchlist fetch error:', e);
    }
  }, []);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/workspace/history`);
      if (res.ok) {
        const data = await res.json();
        setHistory(data.history || []);
      }
    } catch (e) {
      logger.error('History fetch error:', e);
    }
  }, []);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      await Promise.all([fetchWatchlist(), fetchHistory()]);
      setLoading(false);
    };
    load();
  }, [fetchWatchlist, fetchHistory]);

  const handleAddTicker = async (e) => {
    e.preventDefault();
    if (!addTicker.trim()) return;
    setAddLoading(true);
    setAddError('');
    try {
      const res = await authFetch(`${API}/workspace/watchlist/add`, {
        method: 'POST',
        body: JSON.stringify({ ticker: addTicker.trim().toUpperCase() }),
      });
      if (res.ok) {
        setAddTicker('');
        await fetchWatchlist();
      } else if (res.status === 403) {
        setAddError(`Free accounts are limited to ${FREE_WATCHLIST_LIMIT} tickers. Upgrade to Pro for unlimited.`);
      }
    } catch (e) {
      logger.error('Add ticker error:', e);
    } finally {
      setAddLoading(false);
    }
  };

  const removeTicker = async (ticker) => {
    try {
      await authFetch(`${API}/workspace/watchlist/remove`, {
        method: 'POST',
        body: JSON.stringify({ ticker }),
      });
      await fetchWatchlist();
    } catch (e) {
      logger.error('Remove ticker error:', e);
    }
  };

  const verdictIcon = (v) => {
    if (v === 'BUY') return <TrendingUp className="w-4 h-4 text-lime-400" />;
    if (v === 'SELL') return <TrendingDown className="w-4 h-4 text-orange-400" />;
    return <Minus className="w-4 h-4 text-amber-300" />;
  };

  const verdictStyle = (v) => {
    if (v === 'BUY') return 'text-lime-400 bg-lime-700 border-emerald-700/50';
    if (v === 'SELL') return 'text-orange-400 bg-orange-800 border-red-700/50';
    return 'text-amber-300 bg-amber-900/30 border-amber-700/50';
  };

  const tabs = [
    { id: 'watchlist', label: 'Watchlist', icon: Star },
    { id: 'history', label: 'Hypothesis History', icon: Clock },
    { id: 'referrals', label: 'Referrals', icon: Gift },
  ];

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-start justify-center overflow-y-auto p-4" data-testid="user-workspace">
      <div className="bg-slate-900 rounded-2xl max-w-3xl w-full my-4 border border-slate-400/25">
        {/* Header */}
        <div className="p-6 border-b border-slate-400/30 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-[#3DE8D9] rounded-xl flex items-center justify-center">
              <Briefcase className="w-6 h-6 text-white" />
            </div>
            <div>
              <h2 className="text-white text-xl font-bold" style={{ fontFamily: 'Manrope, sans-serif' }}>My Workspace</h2>
              <p className="text-slate-300 text-sm">{user?.name || user?.email}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {isPro && <Badge className="bg-gradient-to-r from-[#3DE8D9] to-cyan-500 text-white border-0 text-xs">PRO</Badge>}
            <button onClick={onClose} className="text-slate-400 hover:text-white text-xl px-2" data-testid="workspace-close">
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-slate-400/25">
          {tabs.map(t => {
            const Icon = t.icon;
            return (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`flex-1 flex items-center justify-center gap-2 py-3 text-sm font-medium transition-colors ${
                  tab === t.id
                    ? 'text-[#3DE8D9] border-b-2 border-[#3DE8D9] bg-[#3DE8D9]/5'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
                data-testid={`workspace-tab-${t.id}`}
              >
                <Icon className="w-4 h-4" />
                {t.label}
              </button>
            );
          })}
        </div>

        {/* Content */}
        <div className="p-4 sm:p-6 min-h-[300px]">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <RefreshCw className="w-6 h-6 text-[#3DE8D9] animate-spin" />
            </div>
          ) : (
            <TabContent
              tab={tab}
              watchlist={watchlist}
              history={history}
              isPro={isPro}
              addTicker={addTicker}
              setAddTicker={setAddTicker}
              addLoading={addLoading}
              addError={addError}
              handleAddTicker={handleAddTicker}
              removeTicker={removeTicker}
              onSubscribe={onSubscribe}
            />
          )}
        </div>
      </div>
    </div>
  );
};

const TabContent = ({ tab, watchlist, history, isPro, addTicker, setAddTicker, addLoading, addError, handleAddTicker, removeTicker, onSubscribe }) => {
  if (tab === 'watchlist') return (
    <WatchlistTab
      watchlist={watchlist} isPro={isPro} addTicker={addTicker} setAddTicker={setAddTicker}
      addLoading={addLoading} addError={addError} handleAddTicker={handleAddTicker}
      removeTicker={removeTicker} onSubscribe={onSubscribe}
    />
  );
  if (tab === 'history') return <HistoryTab history={history} />;
  if (tab === 'referrals') return <ReferralsTab />;
  return null;
};

const WatchlistTab = ({ watchlist, isPro, addTicker, setAddTicker, addLoading, addError, handleAddTicker, removeTicker, onSubscribe }) => (
  <div className="space-y-4">
    <form onSubmit={handleAddTicker} className="flex gap-2" data-testid="add-ticker-form">
      <div className="relative flex-1">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
        <Input
          placeholder="Add ticker (e.g., AAPL, TSLA)"
          value={addTicker}
          onChange={e => setAddTicker(e.target.value.toUpperCase())}
          className="pl-10 bg-slate-800 border-slate-600 text-white rounded-xl"
          data-testid="add-ticker-input"
        />
      </div>
      <Button type="submit" disabled={addLoading || !addTicker.trim()} className="bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white rounded-xl" data-testid="add-ticker-btn">
        <Plus className="w-4 h-4 mr-1" /> Add
      </Button>
    </form>
    {!isPro && (
      <div className="flex items-center justify-between text-xs">
        <span className="text-slate-400">{watchlist.length}/{FREE_WATCHLIST_LIMIT} free tickers used</span>
        {watchlist.length >= FREE_WATCHLIST_LIMIT && (
          <button onClick={onSubscribe} className="text-[#3DE8D9] hover:underline flex items-center gap-1">
            <Lock className="w-3 h-3" /> Upgrade for unlimited
          </button>
        )}
      </div>
    )}
    {addError && (
      <div className="bg-amber-900/20 border-amber-700/40 rounded-xl px-3 py-2 flex items-center justify-between border">
        <span className="text-amber-300 text-xs">{addError}</span>
        <Button size="sm" className="bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white rounded-lg text-xs h-7 px-3" onClick={onSubscribe}>
          Upgrade
        </Button>
      </div>
    )}
    {watchlist.length === 0 ? (
      <div className="text-center py-10">
        <Star className="w-10 h-10 text-slate-400 mx-auto mb-3" />
        <p className="text-slate-300 text-sm">No tickers in your watchlist yet</p>
        <p className="text-slate-300 text-xs mt-1">Add tickers above to start tracking</p>
      </div>
    ) : (
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        {watchlist.map(ticker => (
          <div key={ticker} className="flex items-center justify-between bg-slate-800/60 border border-slate-400/30/40 rounded-xl px-3 py-2.5 group" data-testid={`watchlist-ticker-${ticker}`}>
            <span className="text-white font-semibold text-sm">{ticker}</span>
            <button onClick={() => removeTicker(ticker)} className="text-slate-400 hover:text-orange-400 transition-colors opacity-0 group-hover:opacity-100" data-testid={`remove-ticker-${ticker}`}>
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        ))}
      </div>
    )}
  </div>
);

const HistoryTab = ({ history }) => (
  <div className="space-y-3">
    {history.length === 0 ? (
      <div className="text-center py-10">
        <Clock className="w-10 h-10 text-slate-400 mx-auto mb-3" />
        <p className="text-slate-300 text-sm">No hypothesis history yet</p>
        <p className="text-slate-300 text-xs mt-1">Generate an AI Hypothesis to see it here</p>
      </div>
    ) : (
      history.map((h, i) => (
        <div key={`${h.symbol}-${h.searched_at || i}`} className="flex items-center justify-between bg-slate-800/60 border border-slate-400/30/40 rounded-xl px-4 py-3" data-testid={`history-item-${i}`}>
          <div className="flex items-center gap-3">
            {verdictIcon(h.verdict)}
            <div>
              <span className="text-white font-semibold text-sm">{h.symbol}</span>
              <p className="text-slate-300 text-xs">
                {h.searched_at ? new Date(h.searched_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : ''}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {h.verdict && <Badge className={`text-[10px] border ${verdictStyle(h.verdict)}`}>{h.verdict}</Badge>}
            {h.confidence > 0 && <span className="text-slate-300 text-xs font-mono">{h.confidence}%</span>}
          </div>
        </div>
      ))
    )}
  </div>
);

const ReferralsTab = () => {
  const [info, setInfo] = useState(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await authFetch(`${API}/referral/info`);
        if (res.ok) setInfo(await res.json());
      } catch (e) {
        logger.error('Referral info error:', e);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  const referralLink = info ? `${window.location.origin}?ref=${info.code}` : '';

  const copyLink = () => {
    navigator.clipboard.writeText(referralLink);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <RefreshCw className="w-6 h-6 text-[#3DE8D9] animate-spin" />
      </div>
    );
  }

  if (!info) {
    return <div className="text-center py-10 text-slate-300 text-sm">Unable to load referral info</div>;
  }

  return (
    <div className="space-y-5" data-testid="referrals-tab">
      {/* Share Link */}
      <Card className="bg-gradient-to-br from-[#3DE8D9]/10 to-cyan-900/10 border-[#3DE8D9]/30 rounded-xl p-4">
        <div className="flex items-center gap-2 mb-3">
          <Gift className="w-5 h-5 text-[#3DE8D9]" />
          <h3 className="text-white text-sm font-semibold">Share & Earn</h3>
        </div>
        <p className="text-slate-300 text-xs mb-3">
          Invite friends to RISEDUAL AI. When they subscribe to Pro, you earn <strong className="text-white">1 free month</strong>. They get a <strong className="text-white">7-day Pro trial</strong>.
        </p>
        <div className="flex gap-2">
          <Input
            readOnly
            value={referralLink}
            className="bg-slate-800 border-slate-600 text-white text-xs rounded-xl flex-1"
            data-testid="referral-link-input"
          />
          <Button
            onClick={copyLink}
            className={`rounded-xl px-4 text-sm ${copied ? 'bg-green-600' : 'bg-[#3DE8D9] hover:bg-[#7AEEE0]'} text-white`}
            data-testid="copy-referral-btn"
          >
            {copied ? <><Check className="w-4 h-4 mr-1" /> Copied</> : <><Copy className="w-4 h-4 mr-1" /> Copy</>}
          </Button>
        </div>
        <p className="text-slate-400 text-[10px] mt-2">Your code: <span className="text-white font-mono">{info.code}</span></p>
        <div className="mt-3">
          <SocialShareButtons referralLink={referralLink} />
        </div>
      </Card>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-3">
        <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-3 text-center">
          <p className="text-2xl font-bold text-white">{info.total_referrals}</p>
          <p className="text-slate-400 text-[10px]">Total Referrals</p>
        </Card>
        <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-3 text-center">
          <p className="text-2xl font-bold text-lime-400">{info.rewards_earned}</p>
          <p className="text-slate-400 text-[10px]">Months Earned</p>
        </Card>
        <Card className="bg-slate-800/60 border-slate-400/30/40 rounded-xl p-3 text-center">
          <p className="text-2xl font-bold text-[#3DE8D9]">{info.rewards_remaining}</p>
          <p className="text-slate-400 text-[10px]">Remaining ({info.reward_cap}/yr)</p>
        </Card>
      </div>

      {/* Referral History */}
      <div>
        <h4 className="text-white text-sm font-semibold mb-2 flex items-center gap-2">
          <Users className="w-4 h-4 text-slate-400" /> Referral History
        </h4>
        {info.referrals.length === 0 ? (
          <div className="text-center py-8">
            <Users className="w-10 h-10 text-slate-400 mx-auto mb-3" />
            <p className="text-slate-300 text-sm">No referrals yet</p>
            <p className="text-slate-300 text-xs mt-1">Share your link to start earning free months</p>
          </div>
        ) : (
          <div className="space-y-2 max-h-[250px] overflow-y-auto">
            {info.referrals.map((ref, i) => (
              <div key={`ref-${i}`} className="flex items-center justify-between bg-slate-800/60 border border-slate-400/30/40 rounded-xl px-4 py-2.5" data-testid={`referral-item-${i}`}>
                <div>
                  <p className="text-white text-sm">{ref.referred_email}</p>
                  <p className="text-slate-400 text-[10px]">
                    {ref.created_at ? new Date(ref.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : ''}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge className={`text-[10px] border ${
                    ref.status === 'completed' ? 'text-lime-400 bg-lime-700 border-emerald-700/50' : 'text-amber-300 bg-amber-900/30 border-amber-700/50'
                  }`}>
                    {ref.status === 'completed' ? 'Subscribed' : 'Pending'}
                  </Badge>
                  {ref.reward_granted && (
                    <Badge className="text-[10px] bg-[#3DE8D9]/20 text-[#3DE8D9] border-[#3DE8D9]/30">+1 Month</Badge>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      {/* Leaderboard */}
      <ReferralLeaderboard />

      {/* Digest Email Preferences */}
      <DigestToggle />

      {/* Push Notifications */}
      <PushToggle />
    </div>
  );
};

const DigestPreviewModal = ({ open, onClose, onConfirm, preview, loading, sending, email }) => {
  // Close on Escape — stateless handler that reads `sending` each
  // keypress so we don't steal focus while the POST is in flight.
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape' && !sending) onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose, sending]);
  if (!open) return null;
  const cs = preview?.content_summary || {};
  const pv = preview?.preview || {};
  const total = (cs.predictions || 0) + (cs.smart_money || 0) + (cs.alerts || 0);
  return (
    <div
      className="fixed inset-0 bg-black/70 backdrop-blur-sm z-[120] flex items-center justify-center p-4"
      data-testid="digest-preview-modal"
      onClick={onClose}
    >
      <Card
        className="bg-slate-900 border-slate-400/30 rounded-2xl max-w-md w-full p-5 max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 mb-4">
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-[#3DE8D9]/20 to-purple-500/10 flex items-center justify-center shrink-0">
              <Sparkles className="w-4 h-4 text-[#3DE8D9]" />
            </div>
            <div className="min-w-0">
              <h3 className="text-white text-sm font-semibold">Digest preview</h3>
              <p className="text-slate-400 text-[10px] truncate">
                We&rsquo;ll send this to{' '}
                <span className="text-slate-300">{email || 'your inbox'}</span>
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-white rounded p-1 hover:bg-slate-800"
            data-testid="digest-preview-close"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {loading && (
          <div className="py-10 flex items-center justify-center gap-2 text-slate-400 text-xs">
            <Loader2 className="w-4 h-4 animate-spin" />
            Building your preview…
          </div>
        )}

        {!loading && preview && (
          <>
            {/* KPI row */}
            <div className="grid grid-cols-3 gap-2 mb-4" data-testid="digest-preview-kpis">
              <div className="bg-slate-800/60 rounded-lg p-2 border border-slate-700/40 text-center">
                <div className="text-lg font-bold text-white tabular-nums">{cs.predictions || 0}</div>
                <div className="text-[9px] uppercase tracking-wider text-slate-400">Predictions</div>
              </div>
              <div className="bg-slate-800/60 rounded-lg p-2 border border-slate-700/40 text-center">
                <div className="text-lg font-bold text-white tabular-nums">{cs.smart_money || 0}</div>
                <div className="text-[9px] uppercase tracking-wider text-slate-400">Smart $</div>
              </div>
              <div className="bg-slate-800/60 rounded-lg p-2 border border-slate-700/40 text-center">
                <div className="text-lg font-bold text-white tabular-nums">{cs.alerts || 0}</div>
                <div className="text-[9px] uppercase tracking-wider text-slate-400">Alerts</div>
              </div>
            </div>

            {pv.overview_headline && (
              <div className="mb-3" data-testid="digest-preview-overview">
                <div className="text-[9px] uppercase tracking-wider text-slate-500 mb-1">Market overview</div>
                <p className="text-xs text-slate-200 leading-snug italic border-l-2 border-[#3DE8D9]/40 pl-2">
                  {pv.overview_headline}
                </p>
              </div>
            )}

            {pv.top_predictions?.length > 0 && (
              <div className="mb-3" data-testid="digest-preview-predictions">
                <div className="text-[9px] uppercase tracking-wider text-slate-500 mb-1">Top AI predictions</div>
                <ul className="space-y-1">
                  {pv.top_predictions.map((p, i) => (
                    <li key={`${p.ticker || 'x'}-${i}`} className="flex items-center gap-2 text-xs">
                      <span className="text-white font-bold font-mono">{p.ticker || '?'}</span>
                      {p.direction && (
                        <span className={`text-[10px] font-bold uppercase px-1.5 rounded ${
                          p.direction === 'up' || p.direction === 'UP'
                            ? 'bg-emerald-500/20 text-emerald-300'
                            : 'bg-rose-500/20 text-rose-300'
                        }`}>
                          {p.direction}
                        </span>
                      )}
                      {typeof p.confidence === 'number' && (
                        <span className="text-[10px] text-slate-400 tabular-nums">
                          {(p.confidence > 1 ? p.confidence : p.confidence * 100).toFixed(0)}%
                        </span>
                      )}
                      {p.horizon && <span className="text-[10px] text-slate-500">· {p.horizon}</span>}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {pv.top_smart_money?.length > 0 && (
              <div className="mb-3" data-testid="digest-preview-smart-money">
                <div className="text-[9px] uppercase tracking-wider text-slate-500 mb-1">Smart-money shifts</div>
                <ul className="space-y-1">
                  {pv.top_smart_money.map((s, i) => (
                    <li key={`${s.ticker || 'x'}-${i}`} className="flex items-center gap-2 text-xs">
                      <span className="text-white font-bold font-mono">{s.ticker || '?'}</span>
                      {typeof s.score === 'number' && (
                        <span className="text-[10px] text-purple-300 tabular-nums">
                          score {s.score.toFixed(2)}
                        </span>
                      )}
                      {typeof s.shift === 'number' && (
                        <span className={`text-[10px] tabular-nums ${s.shift >= 0 ? 'text-emerald-300' : 'text-rose-300'}`}>
                          {s.shift >= 0 ? '+' : ''}{s.shift.toFixed(2)}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {pv.alert_titles?.length > 0 && (
              <div className="mb-3" data-testid="digest-preview-alerts">
                <div className="text-[9px] uppercase tracking-wider text-slate-500 mb-1">Regime alerts</div>
                <ul className="space-y-1">
                  {pv.alert_titles.filter(Boolean).map((t, i) => (
                    <li key={i} className="text-xs text-slate-300 truncate">• {t}</li>
                  ))}
                </ul>
              </div>
            )}

            {cs.has_watchlist_intel && (
              <div className="mb-3 px-2 py-1.5 rounded bg-[#3DE8D9]/5 border border-[#3DE8D9]/20 text-[10px] text-[#3DE8D9]">
                ✦ Your watchlist intel will be included
              </div>
            )}

            {total === 0 && !pv.overview_headline && (
              <div className="mb-3 px-3 py-3 rounded bg-slate-800/40 border border-slate-700/40 text-[11px] text-slate-400 italic">
                Quiet market — the digest will ship with overview + watchlist intel only.
              </div>
            )}
          </>
        )}

        <div className="flex items-center gap-2 justify-end pt-3 border-t border-slate-700/40 mt-2">
          <button
            onClick={onClose}
            disabled={sending}
            className="px-3 py-1.5 rounded-lg text-xs font-medium text-slate-300 hover:text-white hover:bg-slate-800 transition"
            data-testid="digest-preview-cancel"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={sending || loading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-[#3DE8D9] text-black hover:bg-[#7AEEE0] transition disabled:opacity-50 disabled:cursor-not-allowed"
            data-testid="digest-preview-send"
          >
            {sending
              ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Sending…</>
              : <><Send className="w-3.5 h-3.5" /> Send to my inbox</>}
          </button>
        </div>
      </Card>
    </div>
  );
};

const DigestToggle = () => {
  const [subscribed, setSubscribed] = useState(true);
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState(false);
  const [sending, setSending] = useState(false);
  // Preview modal state
  const [previewOpen, setPreviewOpen] = useState(false);
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await authFetch(`${API}/digest/status`);
        if (res.ok) {
          const data = await res.json();
          setSubscribed(data.subscribed);
        }
      } catch (e) { logger.error('Digest status error:', e); }
      finally { setLoading(false); }
    };
    load();
  }, []);

  const toggle = async () => {
    setToggling(true);
    try {
      const endpoint = subscribed ? 'opt-out' : 'opt-in';
      const res = await authFetch(`${API}/digest/${endpoint}`, { method: 'POST' });
      if (res.ok) setSubscribed(!subscribed);
    } catch (e) { logger.error('Digest toggle error:', e); }
    finally { setToggling(false); }
  };

  const openPreview = async () => {
    setPreviewOpen(true);
    setPreview(null);
    setPreviewLoading(true);
    try {
      const res = await authFetch(`${API}/digest/my-preview`);
      if (res.ok) {
        setPreview(await res.json());
      } else {
        toast.error('Could not load preview', {
          description: 'Falling back to direct send.',
        });
        // Degrade: keep the modal open with an empty preview so
        // the user can still confirm-send.
        setPreview({ content_summary: {}, preview: {}, email: null });
      }
    } catch (e) {
      logger.error('digest preview error:', e);
      setPreview({ content_summary: {}, preview: {}, email: null });
    } finally {
      setPreviewLoading(false);
    }
  };

  const confirmSend = async () => {
    if (sending) return;
    setSending(true);
    try {
      const res = await authFetch(`${API}/digest/send-now`, { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        const cs = data.content_summary || {};
        toast.success('Fresh digest is on its way', {
          description: `${cs.predictions || 0} predictions · ${cs.smart_money || 0} smart-money alerts · ${cs.alerts || 0} market alerts${data.has_watchlist_intel ? ' · watchlist intel included' : ''}.`,
          duration: 5000,
        });
        setPreviewOpen(false);
      } else if (res.status === 429) {
        toast.error('Already sent recently', { description: data.detail || 'Please wait before requesting another digest.' });
      } else {
        toast.error('Could not send digest', { description: data.detail || 'Please try again in a minute.' });
      }
    } catch (e) {
      logger.error('On-demand digest send error:', e);
      toast.error('Could not send digest');
    } finally {
      setSending(false);
    }
  };

  if (loading) return null;

  return (
    <>
      <Card className="bg-slate-700/60 border-slate-400/30/30 rounded-xl p-4" data-testid="digest-toggle">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-8 h-8 rounded-lg bg-[#3DE8D9]/10 flex items-center justify-center shrink-0">
              <Mail className="w-4 h-4 text-[#3DE8D9]" />
            </div>
            <div className="min-w-0">
              <p className="text-white text-sm font-medium">Daily Market Digest</p>
              <p className="text-slate-400 text-[10px]">
                Morning briefing at 6:00 AM UTC · preview on-demand
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={openPreview}
              disabled={sending}
              title="Preview the fresh digest before sending (1 send per hour)"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-[#3DE8D9]/10 text-[#3DE8D9] border border-[#3DE8D9]/30 hover:bg-[#3DE8D9]/20 transition-all disabled:opacity-60 disabled:cursor-not-allowed"
              data-testid="digest-send-now-btn"
            >
              <Eye className="w-3.5 h-3.5" /> Preview &amp; send
            </button>
            <button
              onClick={toggle}
              disabled={toggling}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                subscribed
                  ? 'bg-lime-700 text-lime-400 border border-emerald-700/50 hover:bg-emerald-800/40'
                  : 'bg-slate-700 text-slate-400 border border-slate-600 hover:bg-slate-600'
              }`}
              data-testid="digest-toggle-btn"
            >
              {subscribed ? <Bell className="w-3.5 h-3.5" /> : <BellOff className="w-3.5 h-3.5" />}
              {subscribed ? 'Subscribed' : 'Unsubscribed'}
            </button>
          </div>
        </div>
      </Card>
      <DigestPreviewModal
        open={previewOpen}
        onClose={() => { if (!sending) setPreviewOpen(false); }}
        onConfirm={confirmSend}
        preview={preview}
        loading={previewLoading}
        sending={sending}
        email={preview?.email}
      />
    </>
  );
};

const PushToggle = () => {
  const { permission, subscribed, supported, subscribe, unsubscribe } = usePushNotifications();
  const [toggling, setToggling] = useState(false);
  const { isPro } = useAuth();

  if (!supported) return null;

  const handleToggle = async () => {
    setToggling(true);
    try {
      if (subscribed) await unsubscribe();
      else await subscribe();
    } finally {
      setToggling(false);
    }
  };

  return (
    <Card className="bg-slate-700/60 border-slate-400/30/30 rounded-xl p-4" data-testid="push-toggle">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-purple-500/10 flex items-center justify-center">
            <Bell className="w-4 h-4 text-purple-400" />
          </div>
          <div>
            <p className="text-white text-sm font-medium">Push Notifications</p>
            <p className="text-slate-400 text-[10px]">
              {isPro ? 'All alerts: predictions, dark pool, watchlist, signals' : '1 alert/day (Pro: unlimited)'}
            </p>
          </div>
        </div>
        <button
          onClick={handleToggle}
          disabled={toggling || permission === 'denied'}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${getPushToggleClass(permission, subscribed)}`}
          data-testid="push-toggle-btn"
        >
          <PushToggleLabel permission={permission} subscribed={subscribed} />
        </button>
      </div>
    </Card>
  );
};

export { DigestToggle };
export default UserWorkspace;
