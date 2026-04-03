import React, { useState, useEffect, useCallback } from 'react';
import { Briefcase, Star, Clock, TrendingUp, TrendingDown, Minus, Trash2, RefreshCw, X, Search, Plus } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { useAuth, authFetch } from '../contexts/AuthContext';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const UserWorkspace = ({ onClose }) => {
  const { user, isPro } = useAuth();
  const [tab, setTab] = useState('watchlist');
  const [watchlist, setWatchlist] = useState([]);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [addTicker, setAddTicker] = useState('');
  const [addLoading, setAddLoading] = useState(false);

  // Stable deps: API and authFetch are module-level constants
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const fetchWatchlist = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/workspace/watchlist`);
      if (res.ok) {
        const data = await res.json();
        setWatchlist(data.tickers || []);
      }
    } catch (e) {
      console.error('Watchlist fetch error:', e);
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
      console.error('History fetch error:', e);
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
    try {
      const res = await authFetch(`${API}/workspace/watchlist/add`, {
        method: 'POST',
        body: JSON.stringify({ ticker: addTicker.trim().toUpperCase() }),
      });
      if (res.ok) {
        setAddTicker('');
        await fetchWatchlist();
      }
    } catch (e) {
      console.error('Add ticker error:', e);
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
      console.error('Remove ticker error:', e);
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
          ) : tab === 'watchlist' ? (
            <div className="space-y-4">
              {/* Add Ticker Form */}
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

              {/* Watchlist */}
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
                      <button
                        onClick={() => removeTicker(ticker)}
                        className="text-slate-500 hover:text-red-400 transition-colors opacity-0 group-hover:opacity-100"
                        data-testid={`remove-ticker-${ticker}`}
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
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
                      {h.verdict && (
                        <Badge className={`text-[10px] border ${verdictStyle(h.verdict)}`}>
                          {h.verdict}
                        </Badge>
                      )}
                      {h.confidence > 0 && (
                        <span className="text-slate-400 text-xs font-mono">{h.confidence}%</span>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default UserWorkspace;
