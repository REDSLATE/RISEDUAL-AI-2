import React, { useState, useEffect, useCallback } from 'react';
import { Star, X, Plus, TrendingUp, TrendingDown, Lock, RefreshCw, Sparkles, Swords } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Card } from './ui/card';
import { useAuth, authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';
import logger from '../utils/logger';
import InfoTooltip from './InfoTooltip';
import SparkLine from './SparkLine';
import ShareSmartMoneyBoard from './ShareSmartMoneyBoard';
import ShareBoardLeaderboard from './ShareBoardLeaderboard';

const API = `${getApiBase()}/api`;
const FREE_WATCHLIST_LIMIT = 3;

const Watchlist = ({ onSubscribe }) => {
  const { user, isPro } = useAuth();
  const [watchlist, setWatchlist] = useState([]);
  const [newSymbol, setNewSymbol] = useState('');
  const [isExpanded, setIsExpanded] = useState(false);
  const [capWarning, setCapWarning] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [smartScores, setSmartScores] = useState({});

  // Fetch Smart Money Scores for all watchlist symbols (from 13F data)
  const fetchSmartScores = useCallback(async (symbols) => {
    if (!symbols.length) return;
    try {
      const res = await fetch(`${API}/stockfit/13f/smart-money-scores?symbols=${symbols.join(',')}`, { credentials: 'include' });
      if (!res.ok) return;
      const data = await res.json();
      setSmartScores(data.scores || {});
    } catch (e) {
      logger.warn('Smart money score fetch failed:', e);
    }
  }, []);

  const [smsShifts, setSmsShifts] = useState([]);
  const [smsHistory, setSmsHistory] = useState({});

  // Fetch recent Smart Money Score shift alerts for this user's watchlist
  const fetchSmsShifts = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/stockfit/13f/alerts?limit=20`);
      if (!res.ok) return;
      const data = await res.json();
      const shifts = (data.alerts || []).filter(a => a.type === 'smart_money_shift');
      setSmsShifts(shifts.slice(0, 3));
    } catch (e) {
      logger.debug('SMS shifts fetch failed:', e);
    }
  }, []);

  // Batch fetch 30-day SMS history for all watchlist symbols (one request)
  const fetchSmsHistory = useCallback(async (symbols) => {
    if (!symbols.length) return;
    try {
      const res = await fetch(`${API}/stockfit/13f/smart-money-history?symbols=${symbols.join(',')}&days=30`, { credentials: 'include' });
      if (!res.ok) return;
      const data = await res.json();
      setSmsHistory(data.histories || {});
    } catch (e) {
      logger.debug('SMS history fetch failed:', e);
    }
  }, []);

  // Fetch live quotes for all watchlist symbols
  const fetchQuotes = useCallback(async (symbols) => {
    if (!symbols.length) return;
    setRefreshing(true);
    try {
      const results = await Promise.allSettled(
        symbols.map(sym =>
          fetch(`${API}/stocks/quote/${sym}`).then(r => r.ok ? r.json() : null)
        )
      );
      setWatchlist(prev => {
        const updated = prev.map((item, i) => {
          const data = results[i]?.value;
          if (!data) return item;
          return {
            ...item,
            price: data.price || 0,
            change: data.change || 0,
            changePercent: data.changePercent || 0,
          };
        });
        localStorage.setItem('risedualai_watchlist', JSON.stringify(updated));
        return updated;
      });
    } catch (e) {
      logger.warn('Quote refresh failed:', e);
    } finally {
      setRefreshing(false);
    }
  }, []);

  // Sync a ticker add to the backend (fire-and-forget)
  const syncAdd = useCallback((symbol) => {
    if (!user) return;
    authFetch(`${API}/workspace/watchlist/add`, {
      method: 'POST',
      body: JSON.stringify({ ticker: symbol }),
    }).catch((e) => logger.warn('Watchlist sync add failed:', e));
  }, [user]);

  // Sync a ticker remove to the backend (fire-and-forget)
  const syncRemove = useCallback((symbol) => {
    if (!user) return;
    authFetch(`${API}/workspace/watchlist/remove`, {
      method: 'POST',
      body: JSON.stringify({ ticker: symbol }),
    }).catch((e) => logger.warn('Watchlist sync remove failed:', e));
  }, [user]);

  // Load watchlist: backend first (if logged in), localStorage fallback
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      if (user) {
        try {
          const res = await authFetch(`${API}/workspace/watchlist`);
          if (res.ok) {
            const data = await res.json();
            const tickers = data.tickers || [];
            if (tickers.length > 0 && !cancelled) {
              const items = tickers.map(sym => ({ symbol: sym, addedAt: '', price: 0, change: 0, changePercent: 0 }));
              setWatchlist(items);
              localStorage.setItem('risedualai_watchlist', JSON.stringify(items));
              fetchQuotes(tickers);
              return;
            }
          }
        } catch (e) {
          logger.warn('Backend watchlist load failed, falling back to local:', e);
        }
      }
      // Fallback: localStorage
      const saved = localStorage.getItem('risedualai_watchlist');
      if (saved && !cancelled) {
        const parsed = JSON.parse(saved);
        setWatchlist(parsed);
        const symbols = parsed.map(item => item.symbol);
        fetchQuotes(symbols);
        // If logged in, sync local watchlist to backend
        if (user && parsed.length > 0) {
          for (const item of parsed) {
            syncAdd(item.symbol);
          }
        }
      }
    };
    load();
    return () => { cancelled = true; };
  }, [user, fetchQuotes, syncAdd]);

  // Listen for external add-to-watchlist events
  useEffect(() => {
    const handleAdd = (e) => {
      if (!e.detail) return;
      const symbol = e.detail.toUpperCase().trim();
      setWatchlist((prev) => {
        if (prev.find(item => item.symbol === symbol)) return prev;
        const updated = [...prev, { symbol, addedAt: new Date().toISOString(), price: 0, change: 0, changePercent: 0 }];
        localStorage.setItem('risedualai_watchlist', JSON.stringify(updated));
        syncAdd(symbol);
        fetch(`${API}/stocks/quote/${symbol}`).then(r => r.ok ? r.json() : null).then(data => {
          if (!data) return;
          setWatchlist(prev2 => {
            const up = prev2.map(item => item.symbol === symbol ? { ...item, price: data.price || 0, change: data.change || 0, changePercent: data.changePercent || 0 } : item);
            localStorage.setItem('risedualai_watchlist', JSON.stringify(up));
            return up;
          });
        }).catch(() => {});
        return updated;
      });
      setIsExpanded(true);
    };
    window.addEventListener('risedualai-add-watchlist', handleAdd);
    return () => window.removeEventListener('risedualai-add-watchlist', handleAdd);
  }, [syncAdd]);

  // Refresh quotes every 60 seconds when expanded
  useEffect(() => {
    if (!isExpanded || !watchlist.length) return;
    const symbols = watchlist.map(item => item.symbol);
    // Fetch smart scores + history + shift alerts once per expand
    fetchSmartScores(symbols);
    fetchSmsHistory(symbols);
    fetchSmsShifts();
    const interval = setInterval(() => {
      fetchQuotes(symbols);
    }, 60000);
    return () => clearInterval(interval);
  }, [isExpanded, watchlist, fetchQuotes, fetchSmartScores, fetchSmsShifts, fetchSmsHistory]);

  const addSymbol = () => {
    if (!newSymbol.trim()) return;
    const symbol = newSymbol.toUpperCase().trim();
    if (watchlist.find(item => item.symbol === symbol)) {
      setCapWarning('Symbol already in watchlist');
      setTimeout(() => setCapWarning(''), 3000);
      return;
    }
    if (user && !isPro && watchlist.length >= FREE_WATCHLIST_LIMIT) {
      setCapWarning(`Free accounts are limited to ${FREE_WATCHLIST_LIMIT} tickers. Upgrade to Pro for unlimited.`);
      return;
    }
    const newItem = { symbol, addedAt: new Date().toISOString(), price: 0, change: 0, changePercent: 0 };
    const updated = [...watchlist, newItem];
    setWatchlist(updated);
    localStorage.setItem('risedualai_watchlist', JSON.stringify(updated));
    syncAdd(symbol);
    setNewSymbol('');
    setCapWarning('');
  };

  const removeSymbol = (symbol) => {
    const updated = watchlist.filter(item => item.symbol !== symbol);
    setWatchlist(updated);
    localStorage.setItem('risedualai_watchlist', JSON.stringify(updated));
    syncRemove(symbol);
  };

  return (
    <Card className="bg-slate-700/60 border-slate-400/25 rounded-xl p-4">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Star className="w-5 h-5 text-yellow-500 fill-yellow-500" />
          <h3 className="text-white font-semibold">My Watchlist</h3>
          <InfoTooltip id="watchlist" />
          <span className="text-slate-300 text-sm">({watchlist.length}{user && !isPro ? `/${FREE_WATCHLIST_LIMIT}` : ''})</span>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setIsExpanded(!isExpanded)}
          className="text-slate-400 hover:text-slate-50"
        >
          {isExpanded ? 'Collapse' : 'Expand'}
        </Button>
        {isExpanded && watchlist.length > 0 && (
          <div className="flex items-center gap-1 ml-1">
            <ShareSmartMoneyBoard
              watchlist={watchlist}
              smartScores={smartScores}
              smsHistory={smsHistory}
              userId={user?.id || user?._id || user?.email}
            />
            <Button
              variant="ghost"
              size="sm"
              onClick={() => fetchQuotes(watchlist.map(item => item.symbol))}
              disabled={refreshing}
              className="text-slate-400 hover:text-slate-50"
              data-testid="watchlist-refresh-btn"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
            </Button>
          </div>
        )}
      </div>

      {isExpanded && (
        <>
          {/* Add Symbol Input */}
          <div className="flex gap-2 mb-2">
            <Input
              placeholder="Add symbol (e.g., AAPL)"
              value={newSymbol}
              onChange={(e) => setNewSymbol(e.target.value)}
              onKeyPress={(e) => e.key === 'Enter' && addSymbol()}
              className="bg-[#1E293B] border-slate-600 text-white"
              data-testid="watchlist-input"
            />
            <Button onClick={addSymbol} className="bg-[#3DE8D9] hover:bg-[#7AEEE0]" data-testid="watchlist-add-btn">
              <Plus className="w-4 h-4" />
            </Button>
          </div>
          {capWarning && (
            <div className="flex items-center gap-2 mb-3 bg-amber-900/20 border border-amber-800/40 rounded-lg px-3 py-2" data-testid="watchlist-cap-warning">
              <Lock className="w-3.5 h-3.5 text-amber-300 flex-shrink-0" />
              <p className="text-amber-300 text-xs flex-1">{capWarning}</p>
              {onSubscribe && <Button size="sm" className="bg-[#3DE8D9] text-white text-xs h-6 px-2 rounded-lg" onClick={onSubscribe}>Upgrade</Button>}
            </div>
          )}
          {/* Smart Money Score Shift Alerts */}
          {smsShifts.length > 0 && (
            <div className="mb-3 space-y-1.5" data-testid="watchlist-sms-shifts">
              {smsShifts.map((shift) => {
                const up = shift.delta > 0;
                const topMover = shift.top_movers?.[0];
                return (
                  <div key={`${shift.symbol}-${shift.date}`} className="flex items-stretch gap-1">
                    <button
                      onClick={() => {
                        const prompt = `${shift.symbol} Smart Money Score shifted from ${shift.prev_score} to ${shift.new_score} (${shift.delta > 0 ? '+' : ''}${shift.delta} pts) — now ${shift.signal}. ${topMover ? `Notable move: ${topMover.institution} ${topMover.type} its position.` : ''} What's driving this regime change? Actionable take?`;
                        window.dispatchEvent(new CustomEvent('risedualai-open-chat', { detail: { prefill: prompt, autoSend: true } }));
                      }}
                      className={`flex-1 flex items-center gap-2 text-left px-3 py-2 rounded-lg border text-xs transition-colors hover:brightness-110 ${
                        up ? 'bg-emerald-500/10 border-emerald-500/30' : 'bg-red-500/10 border-red-500/30'
                      }`}
                      data-testid={`watchlist-sms-shift-${shift.symbol}`}
                    >
                      <Sparkles className={`w-3.5 h-3.5 flex-shrink-0 ${up ? 'text-emerald-400' : 'text-red-400'}`} />
                      <span className="text-white font-bold">{shift.symbol}</span>
                      <span className="text-slate-300">Smart Money</span>
                      <span className={`font-mono font-bold ${up ? 'text-emerald-400' : 'text-red-400'}`}>
                        {shift.prev_score} {up ? '↗' : '↘'} {shift.new_score}
                      </span>
                      <span className={`text-[10px] ${up ? 'text-emerald-400' : 'text-red-400'}`}>
                        ({shift.delta > 0 ? '+' : ''}{shift.delta} pts)
                      </span>
                      {shift.signal_change && (
                        <span className="text-slate-400 text-[10px] truncate">now {shift.signal}</span>
                      )}
                    </button>
                    <button
                      onClick={() => {
                        try {
                          fetch(`${API}/analytics/chip-event`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            credentials: 'include',
                            body: JSON.stringify({
                              action: 'action-clicked',
                              chip_text: `Open ${shift.symbol} War Room (SM Shift Alert)`,
                              context_hub: typeof window !== 'undefined'
                                ? (window.__risedualActiveView || null)
                                : null,
                            }),
                          }).catch(() => { /* silent */ });
                        } catch { /* silent */ }
                        window.dispatchEvent(new CustomEvent('risedualai-navigate', { detail: { view: 'warroom', subTab: 'adversarial' } }));
                        setTimeout(() => {
                          window.dispatchEvent(new CustomEvent('risedualai-warroom', { detail: shift.symbol }));
                        }, 180);
                      }}
                      className={`shrink-0 inline-flex items-center gap-1 px-2 rounded-lg border text-[10px] font-bold uppercase tracking-wider transition-colors hover:brightness-125 ${
                        up ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-300' : 'bg-red-500/15 border-red-500/40 text-red-300'
                      }`}
                      title={`Open ${shift.symbol} in AI War Room`}
                      data-testid={`watchlist-sms-shift-warroom-${shift.symbol}`}
                    >
                      <Swords className="w-3 h-3" />
                      <span className="hidden sm:inline">War Room</span>
                      <span>→</span>
                    </button>
                  </div>
                );
              })}
            </div>
          )}

          {/* Watchlist Items */}
          <div className="space-y-2 max-h-96 overflow-y-auto">
            {watchlist.length === 0 ? (
              <div className="text-center py-10 text-slate-400" data-testid="watchlist-empty">
                <div className="w-14 h-14 bg-slate-800/80 rounded-2xl flex items-center justify-center mx-auto mb-3 border border-slate-400/30/40">
                  <Star className="w-7 h-7 text-slate-400" />
                </div>
                <p className="text-slate-300 text-sm font-medium mb-1">No tickers yet</p>
                <p className="text-slate-300 text-xs">Search for a stock symbol above or use the search bar to add tickers to your watchlist</p>
              </div>
            ) : (
              watchlist.map((item) => {
                const sm = smartScores[item.symbol];
                const hasScore = sm && sm.score != null;
                const scoreColor = !hasScore ? 'text-slate-500 bg-slate-700/30 border-slate-600/30'
                  : sm.score >= 60 ? 'text-emerald-400 bg-emerald-500/15 border-emerald-500/30'
                  : sm.score <= 40 ? 'text-red-400 bg-red-500/15 border-red-500/30'
                  : 'text-amber-400 bg-amber-500/15 border-amber-500/30';
                const scoreTooltip = hasScore
                  ? `Smart Money Score ${sm.score}/100 — ${sm.signal.toUpperCase()} (${sm.bullish_count} institutions adding · ${sm.bearish_count} trimming${sm.holder_count ? ` · ${sm.holder_count} tracked holders` : ''})`
                  : 'Smart Money Score: insufficient 13F data';
                return (
                <div
                  key={item.symbol}
                  className="flex items-center justify-between p-3 bg-[#1E293B] rounded-lg hover:bg-slate-700 transition-colors"
                >
                  <div className="flex items-center gap-3">
                    <span className="text-white font-medium">{item.symbol}</span>
                    {hasScore ? (
                      <button
                        onClick={() => {
                          const prompt = `Break down the Smart Money Score for ${item.symbol} (currently ${sm.score}/100, ${sm.signal}). ${sm.bullish_count} tracked institutions increased their position last quarter and ${sm.bearish_count} reduced. What's the likely thesis behind the biggest moves?`;
                          window.dispatchEvent(new CustomEvent('risedualai-open-chat', { detail: { prefill: prompt, autoSend: true } }));
                        }}
                        className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px] font-bold tabular-nums transition-colors ${scoreColor} hover:brightness-125 cursor-pointer`}
                        title={scoreTooltip}
                        data-testid={`watchlist-smart-score-${item.symbol}`}
                      >
                        SM {sm.score}
                      </button>
                    ) : null}
                    {smsHistory[item.symbol]?.length >= 2 && (
                      <SparkLine
                        points={smsHistory[item.symbol]}
                        width={50}
                        height={14}
                        className="opacity-90 hover:opacity-100"
                        data-testid={`watchlist-sparkline-${item.symbol}`}
                      />
                    )}
                    {item.changePercent !== 0 && (
                      <div className={`flex items-center gap-1 text-sm ${
                        item.changePercent >= 0 ? 'text-lime-400' : 'text-orange-400'
                      }`}>
                        {item.changePercent >= 0 ? (
                          <TrendingUp className="w-3 h-3" />
                        ) : (
                          <TrendingDown className="w-3 h-3" />
                        )}
                        {Math.abs(item.changePercent).toFixed(2)}%
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {item.price > 0 && (
                      <span className="text-slate-300 text-sm">${item.price.toFixed(2)}</span>
                    )}
                    <button
                      onClick={() => {
                        const prompt = `Analyze ${item.symbol} for me: current price, technical levels (support/resistance, RSI, moving averages), recent news or catalysts, and the latest 13F institutional holder changes. Give me a concise take.`;
                        window.dispatchEvent(new CustomEvent('risedualai-open-chat', { detail: { prefill: prompt, autoSend: true } }));
                      }}
                      className="text-slate-400 hover:text-[#3DE8D9] transition-colors"
                      title={`Ask AI to analyze ${item.symbol}`}
                      data-testid={`watchlist-delegate-ai-${item.symbol}`}
                    >
                      <Sparkles className="w-4 h-4" />
                    </button>
                    {hasScore && (
                      <button
                        onClick={() => {
                          // Level-2 deep-link: SM Board row → War Room (pre-filled ticker).
                          // Fire-and-forget telemetry so we can measure adoption alongside chat chips.
                          try {
                            fetch(`${API}/analytics/chip-event`, {
                              method: 'POST',
                              headers: { 'Content-Type': 'application/json' },
                              credentials: 'include',
                              body: JSON.stringify({
                                action: 'action-clicked',
                                chip_text: `Open ${item.symbol} War Room (SM Board)`,
                                context_hub: typeof window !== 'undefined'
                                  ? (window.__risedualActiveView || null)
                                  : null,
                              }),
                            }).catch(() => { /* silent */ });
                          } catch { /* silent */ }
                          window.dispatchEvent(new CustomEvent('risedualai-navigate', { detail: { view: 'warroom', subTab: 'adversarial' } }));
                          setTimeout(() => {
                            window.dispatchEvent(new CustomEvent('risedualai-warroom', { detail: item.symbol }));
                          }, 180);
                        }}
                        className="text-slate-400 hover:text-orange-400 transition-colors"
                        title={`Open ${item.symbol} in AI War Room`}
                        data-testid={`watchlist-warroom-${item.symbol}`}
                      >
                        <Swords className="w-4 h-4" />
                      </button>
                    )}
                    <button
                      onClick={() => removeSymbol(item.symbol)}
                      className="text-slate-400 hover:text-orange-400 transition-colors"
                      title="Remove"
                      data-testid={`watchlist-remove-${item.symbol}`}
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                </div>
                );
              })
            )}
          </div>
          <ShareBoardLeaderboard authenticated={!!user} />
        </>
      )}
    </Card>
  );
};

export default Watchlist;