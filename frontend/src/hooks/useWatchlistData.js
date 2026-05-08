import { useState, useEffect, useCallback } from 'react';
import { useAuth, authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';
import logger from '../utils/logger';

const API = `${getApiBase()}/api`;
const LS_KEY = 'risedualai_watchlist';
const REFRESH_MS = 60_000;
export const FREE_WATCHLIST_LIMIT = 3;

/**
 * All fetch, persistence and sync logic for the watchlist. Exposes plain
 * values + actions, so the view components can stay concerned with markup.
 *
 * Responsibilities:
 *   - Load watchlist from backend (preferred) or localStorage (fallback)
 *   - Refresh live quotes every 60s while expanded
 *   - Fetch Smart Money Scores + history + shift alerts on expand
 *   - Persist to localStorage on every change
 *   - Sync add/remove to backend for authenticated users
 *   - Listen for external `risedualai-add-watchlist` events
 */
export default function useWatchlistData(isExpanded) {
  const { user, isPro } = useAuth();
  const [watchlist, setWatchlist] = useState([]);
  const [newSymbol, setNewSymbol] = useState('');
  const [capWarning, setCapWarning] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [smartScores, setSmartScores] = useState({});
  const [smsShifts, setSmsShifts] = useState([]);
  const [smsHistory, setSmsHistory] = useState({});

  // ── Data fetchers ────────────────────────────────────────────────────────

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

  const fetchQuotes = useCallback(async (symbols) => {
    if (!symbols.length) return;
    setRefreshing(true);
    try {
      const results = await Promise.allSettled(
        symbols.map(sym => fetch(`${API}/stocks/quote/${sym}`).then(r => r.ok ? r.json() : null))
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
        localStorage.setItem(LS_KEY, JSON.stringify(updated));
        return updated;
      });
    } catch (e) {
      logger.warn('Quote refresh failed:', e);
    } finally {
      setRefreshing(false);
    }
  }, []);

  // ── Backend sync (fire-and-forget) ────────────────────────────────────────

  const syncAdd = useCallback((symbol) => {
    if (!user) return;
    authFetch(`${API}/workspace/watchlist/add`, {
      method: 'POST',
      body: JSON.stringify({ ticker: symbol }),
    }).catch((e) => logger.warn('Watchlist sync add failed:', e));
  }, [user]);

  const syncRemove = useCallback((symbol) => {
    if (!user) return;
    authFetch(`${API}/workspace/watchlist/remove`, {
      method: 'POST',
      body: JSON.stringify({ ticker: symbol }),
    }).catch((e) => logger.warn('Watchlist sync remove failed:', e));
  }, [user]);

  // ── Initial load: backend first (if authed), localStorage fallback ──────

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
              localStorage.setItem(LS_KEY, JSON.stringify(items));
              fetchQuotes(tickers);
              return;
            }
          }
        } catch (e) {
          logger.warn('Backend watchlist load failed, falling back to local:', e);
        }
      }
      const saved = localStorage.getItem(LS_KEY);
      if (saved && !cancelled) {
        const parsed = JSON.parse(saved);
        setWatchlist(parsed);
        const symbols = parsed.map(item => item.symbol);
        fetchQuotes(symbols);
        if (user && parsed.length > 0) {
          for (const item of parsed) syncAdd(item.symbol);
        }
      }
    };
    load();
    return () => { cancelled = true; };
  }, [user, fetchQuotes, syncAdd]);

  // ── External add event listener ───────────────────────────────────────────

  useEffect(() => {
    const handleAdd = (e) => {
      if (!e.detail) return;
      const symbol = e.detail.toUpperCase().trim();
      setWatchlist((prev) => {
        if (prev.find(item => item.symbol === symbol)) return prev;
        const updated = [...prev, { symbol, addedAt: new Date().toISOString(), price: 0, change: 0, changePercent: 0 }];
        localStorage.setItem(LS_KEY, JSON.stringify(updated));
        syncAdd(symbol);
        fetch(`${API}/stocks/quote/${symbol}`).then(r => r.ok ? r.json() : null).then(data => {
          if (!data) return;
          setWatchlist(prev2 => {
            const up = prev2.map(item => item.symbol === symbol
              ? { ...item, price: data.price || 0, change: data.change || 0, changePercent: data.changePercent || 0 }
              : item);
            localStorage.setItem(LS_KEY, JSON.stringify(up));
            return up;
          });
        }).catch(() => {});
        return updated;
      });
    };
    window.addEventListener('risedualai-add-watchlist', handleAdd);
    return () => window.removeEventListener('risedualai-add-watchlist', handleAdd);
  }, [syncAdd]);

  // ── 60s quote refresh while expanded + one-time SM fetch ─────────────────

  useEffect(() => {
    if (!isExpanded || !watchlist.length) return;
    const symbols = watchlist.map(item => item.symbol);
    fetchSmartScores(symbols);
    fetchSmsHistory(symbols);
    fetchSmsShifts();
    const interval = setInterval(() => { fetchQuotes(symbols); }, REFRESH_MS);
    return () => clearInterval(interval);
  }, [isExpanded, watchlist, fetchQuotes, fetchSmartScores, fetchSmsShifts, fetchSmsHistory]);

  // ── Add / remove actions ──────────────────────────────────────────────────

  const addSymbol = useCallback(() => {
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
    localStorage.setItem(LS_KEY, JSON.stringify(updated));
    syncAdd(symbol);
    setNewSymbol('');
    setCapWarning('');
  }, [newSymbol, watchlist, user, isPro, syncAdd]);

  const removeSymbol = useCallback((symbol) => {
    const updated = watchlist.filter(item => item.symbol !== symbol);
    setWatchlist(updated);
    localStorage.setItem(LS_KEY, JSON.stringify(updated));
    syncRemove(symbol);
  }, [watchlist, syncRemove]);

  const refreshQuotes = useCallback(() => {
    fetchQuotes(watchlist.map(item => item.symbol));
  }, [fetchQuotes, watchlist]);

  return {
    // Derived data
    user, isPro,
    // State
    watchlist, smartScores, smsShifts, smsHistory,
    newSymbol, setNewSymbol,
    capWarning,
    refreshing,
    // Actions
    addSymbol, removeSymbol, refreshQuotes,
  };
}
