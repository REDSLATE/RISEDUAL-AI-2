import React, { useState, useEffect, useCallback } from 'react';
import { Briefcase, Star, Clock, TrendingUp, TrendingDown, Minus, Trash2, RefreshCw, X, Search, Plus, Lock, Gift, Copy, Check, Users, Mail, Bell, BellOff } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { useAuth, authFetch } from '../contexts/AuthContext';
import ReferralLeaderboard from './ReferralLeaderboard';
import SocialShareButtons from './SocialShareButtons';
import { usePushNotifications } from '../hooks/usePushNotifications';
import logger from '../utils/logger';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const FREE_WATCHLIST_LIMIT = 3;

const getPushToggleClass = (permission, subscribed) => {
  if (permission === 'denied') return 'bg-red-900/20 text-red-400 border border-red-800/40 cursor-not-allowed';
  if (subscribed) return 'bg-emerald-900/30 text-emerald-400 border border-emerald-700/50 hover:bg-emerald-800/40';
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
    if (v === 'BUY') return <TrendingUp className="w-4 h-4 text-emerald-400" />;
    if (v === 'SELL') return <TrendingDown className="w-4 h-4 text-red-400" />;
    return <Minus className="w-4 h-4 text-amber-400" />;
  };

  const verdictStyle = (v) => {
    if (v === 'BUY') return 'text-emerald-400 bg-emerald-900/30 border-emerald-700/50';
    if (v === 'SELL') return 'text-red-400 bg-red-900/30 border-red-700/50';
    return 'text-amber-400 bg-amber-900/30 border-amber-700/50';
  };

  const tabs = [
    { id: 'watchlist', label: 'Watchlist', icon: Star },
    { id: 'history', label: 'Hypothesis History', icon: Clock },
    { id: 'referrals', label: 'Referrals', icon: Gift },
  ];

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-start justify-center overflow-y-auto p-4" data-testid="user-workspace">
      <div className="bg-slate-900 rounded-2xl max-w-3xl w-full my-4 border border-slate-700/50">
        {/* Header */}
        <div className="p-6 border-b border-slate-700 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-[#0052FF] rounded-xl flex items-center justify-center">
              <Briefcase className="w-6 h-6 text-white" />
            </div>
            <div>
              <h2 className="text-white text-xl font-bold" style={{ fontFamily: 'Manrope, sans-serif' }}>My Workspace</h2>
              <p className="text-slate-400 text-sm">{user?.name || user?.email}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {isPro && <Badge className="bg-gradient-to-r from-[#0052FF] to-cyan-500 text-white border-0 text-xs">PRO</Badge>}
            <button onClick={onClose} className="text-slate-400 hover:text-white text-xl px-2" data-testid="workspace-close">
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-slate-700/50">
          {tabs.map(t => {
            const Icon = t.icon;
            return (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`flex-1 flex items-center justify-center gap-2 py-3 text-sm font-medium transition-colors ${
                  tab === t.id
                    ? 'text-[#0052FF] border-b-2 border-[#0052FF] bg-[#0052FF]/5'
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
              <RefreshCw className="w-6 h-6 text-[#0052FF] animate-spin" />
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
      <Button type="submit" disabled={addLoading || !addTicker.trim()} className="bg-[#0052FF] hover:bg-[#2563EB] text-white rounded-xl" data-testid="add-ticker-btn">
        <Plus className="w-4 h-4 mr-1" /> Add
      </Button>
    </form>
    {!isPro && (
      <div className="flex items-center justify-between text-xs">
        <span className="text-slate-500">{watchlist.length}/{FREE_WATCHLIST_LIMIT} free tickers used</span>
        {watchlist.length >= FREE_WATCHLIST_LIMIT && (
          <button onClick={onSubscribe} className="text-[#0052FF] hover:underline flex items-center gap-1">
            <Lock className="w-3 h-3" /> Upgrade for unlimited
          </button>
        )}
      </div>
    )}
    {addError && (
      <div className="bg-amber-900/20 border-amber-700/40 rounded-xl px-3 py-2 flex items-center justify-between border">
        <span className="text-amber-400 text-xs">{addError}</span>
        <Button size="sm" className="bg-[#0052FF] hover:bg-[#2563EB] text-white rounded-lg text-xs h-7 px-3" onClick={onSubscribe}>
          Upgrade
        </Button>
      </div>
    )}
    {watchlist.length === 0 ? (
      <div className="text-center py-10">
        <Star className="w-10 h-10 text-slate-600 mx-auto mb-3" />
        <p className="text-slate-400 text-sm">No tickers in your watchlist yet</p>
        <p className="text-slate-500 text-xs mt-1">Add tickers above to start tracking</p>
      </div>
    ) : (
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        {watchlist.map(ticker => (
          <div key={ticker} className="flex items-center justify-between bg-slate-800/60 border border-slate-700/40 rounded-xl px-3 py-2.5 group" data-testid={`watchlist-ticker-${ticker}`}>
            <span className="text-white font-semibold text-sm">{ticker}</span>
            <button onClick={() => removeTicker(ticker)} className="text-slate-500 hover:text-red-400 transition-colors opacity-0 group-hover:opacity-100" data-testid={`remove-ticker-${ticker}`}>
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
        <Clock className="w-10 h-10 text-slate-600 mx-auto mb-3" />
        <p className="text-slate-400 text-sm">No hypothesis history yet</p>
        <p className="text-slate-500 text-xs mt-1">Generate an AI Hypothesis to see it here</p>
      </div>
    ) : (
      history.map((h, i) => (
        <div key={`${h.symbol}-${h.searched_at || i}`} className="flex items-center justify-between bg-slate-800/60 border border-slate-700/40 rounded-xl px-4 py-3" data-testid={`history-item-${i}`}>
          <div className="flex items-center gap-3">
            {verdictIcon(h.verdict)}
            <div>
              <span className="text-white font-semibold text-sm">{h.symbol}</span>
              <p className="text-slate-500 text-xs">
                {h.searched_at ? new Date(h.searched_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : ''}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {h.verdict && <Badge className={`text-[10px] border ${verdictStyle(h.verdict)}`}>{h.verdict}</Badge>}
            {h.confidence > 0 && <span className="text-slate-400 text-xs font-mono">{h.confidence}%</span>}
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
        <RefreshCw className="w-6 h-6 text-[#0052FF] animate-spin" />
      </div>
    );
  }

  if (!info) {
    return <div className="text-center py-10 text-slate-400 text-sm">Unable to load referral info</div>;
  }

  return (
    <div className="space-y-5" data-testid="referrals-tab">
      {/* Share Link */}
      <Card className="bg-gradient-to-br from-[#0052FF]/10 to-cyan-900/10 border-[#0052FF]/30 rounded-xl p-4">
        <div className="flex items-center gap-2 mb-3">
          <Gift className="w-5 h-5 text-[#0052FF]" />
          <h3 className="text-white text-sm font-semibold">Share & Earn</h3>
        </div>
        <p className="text-slate-400 text-xs mb-3">
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
            className={`rounded-xl px-4 text-sm ${copied ? 'bg-emerald-600' : 'bg-[#0052FF] hover:bg-[#2563EB]'} text-white`}
            data-testid="copy-referral-btn"
          >
            {copied ? <><Check className="w-4 h-4 mr-1" /> Copied</> : <><Copy className="w-4 h-4 mr-1" /> Copy</>}
          </Button>
        </div>
        <p className="text-slate-500 text-[10px] mt-2">Your code: <span className="text-white font-mono">{info.code}</span></p>
        <div className="mt-3">
          <SocialShareButtons referralLink={referralLink} />
        </div>
      </Card>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-3">
        <Card className="bg-slate-800/60 border-slate-700/40 rounded-xl p-3 text-center">
          <p className="text-2xl font-bold text-white">{info.total_referrals}</p>
          <p className="text-slate-400 text-[10px]">Total Referrals</p>
        </Card>
        <Card className="bg-slate-800/60 border-slate-700/40 rounded-xl p-3 text-center">
          <p className="text-2xl font-bold text-emerald-400">{info.rewards_earned}</p>
          <p className="text-slate-400 text-[10px]">Months Earned</p>
        </Card>
        <Card className="bg-slate-800/60 border-slate-700/40 rounded-xl p-3 text-center">
          <p className="text-2xl font-bold text-[#0052FF]">{info.rewards_remaining}</p>
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
            <Users className="w-10 h-10 text-slate-600 mx-auto mb-3" />
            <p className="text-slate-400 text-sm">No referrals yet</p>
            <p className="text-slate-500 text-xs mt-1">Share your link to start earning free months</p>
          </div>
        ) : (
          <div className="space-y-2 max-h-[250px] overflow-y-auto">
            {info.referrals.map((ref, i) => (
              <div key={`ref-${i}`} className="flex items-center justify-between bg-slate-800/60 border border-slate-700/40 rounded-xl px-4 py-2.5" data-testid={`referral-item-${i}`}>
                <div>
                  <p className="text-white text-sm">{ref.referred_email}</p>
                  <p className="text-slate-500 text-[10px]">
                    {ref.created_at ? new Date(ref.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : ''}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge className={`text-[10px] border ${
                    ref.status === 'completed' ? 'text-emerald-400 bg-emerald-900/30 border-emerald-700/50' : 'text-amber-400 bg-amber-900/30 border-amber-700/50'
                  }`}>
                    {ref.status === 'completed' ? 'Subscribed' : 'Pending'}
                  </Badge>
                  {ref.reward_granted && (
                    <Badge className="text-[10px] bg-[#0052FF]/20 text-[#0052FF] border-[#0052FF]/30">+1 Month</Badge>
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

const DigestToggle = () => {
  const [subscribed, setSubscribed] = useState(true);
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState(false);

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

  if (loading) return null;

  return (
    <Card className="bg-slate-800/40 border-slate-700/30 rounded-xl p-4" data-testid="digest-toggle">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-[#0052FF]/10 flex items-center justify-center">
            <Mail className="w-4 h-4 text-[#0052FF]" />
          </div>
          <div>
            <p className="text-white text-sm font-medium">Daily Market Digest</p>
            <p className="text-slate-500 text-[10px]">Morning briefing at 6:00 AM UTC</p>
          </div>
        </div>
        <button
          onClick={toggle}
          disabled={toggling}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
            subscribed
              ? 'bg-emerald-900/30 text-emerald-400 border border-emerald-700/50 hover:bg-emerald-800/40'
              : 'bg-slate-700 text-slate-400 border border-slate-600 hover:bg-slate-600'
          }`}
          data-testid="digest-toggle-btn"
        >
          {subscribed ? <Bell className="w-3.5 h-3.5" /> : <BellOff className="w-3.5 h-3.5" />}
          {subscribed ? 'Subscribed' : 'Unsubscribed'}
        </button>
      </div>
    </Card>
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
    <Card className="bg-slate-800/40 border-slate-700/30 rounded-xl p-4" data-testid="push-toggle">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-purple-500/10 flex items-center justify-center">
            <Bell className="w-4 h-4 text-purple-400" />
          </div>
          <div>
            <p className="text-white text-sm font-medium">Push Notifications</p>
            <p className="text-slate-500 text-[10px]">
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

export default UserWorkspace;
