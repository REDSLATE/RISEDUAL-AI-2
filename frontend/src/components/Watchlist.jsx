import React, { useState, useEffect, useCallback } from 'react';
import { Star, X, Plus, TrendingUp, TrendingDown, Lock, RefreshCw } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Card } from './ui/card';
import { useAuth } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;
const FREE_WATCHLIST_LIMIT = 3;

const Watchlist = ({ onSubscribe }) => {
  const { user, isPro } = useAuth();
  const [watchlist, setWatchlist] = useState([]);
  const [newSymbol, setNewSymbol] = useState('');
  const [isExpanded, setIsExpanded] = useState(false);
  const [capWarning, setCapWarning] = useState('');
  const [refreshing, setRefreshing] = useState(false);

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
      /* quote fetch is best-effort */
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    const saved = localStorage.getItem('risedualai_watchlist');
    if (saved) {
      const parsed = JSON.parse(saved);
      setWatchlist(parsed);
      // Fetch live prices on load
      const symbols = parsed.map(item => item.symbol);
      fetchQuotes(symbols);
    }

    const handleAdd = (e) => {
      if (!e.detail) return;
      const symbol = e.detail.toUpperCase().trim();
      setWatchlist((prev) => {
        if (prev.find(item => item.symbol === symbol)) return prev;
        const updated = [...prev, { symbol, addedAt: new Date().toISOString(), price: 0, change: 0, changePercent: 0 }];
        localStorage.setItem('risedualai_watchlist', JSON.stringify(updated));
        // Fetch quote for new symbol
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
  }, [fetchQuotes]);

  // Refresh quotes every 60 seconds when expanded
  useEffect(() => {
    if (!isExpanded || !watchlist.length) return;
    const symbols = watchlist.map(item => item.symbol);
    const interval = setInterval(() => {
      fetchQuotes(symbols);
    }, 60000);
    return () => clearInterval(interval);
  }, [isExpanded, watchlist, fetchQuotes]);

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
    setNewSymbol('');
    setCapWarning('');
  };

  const removeSymbol = (symbol) => {
    const updated = watchlist.filter(item => item.symbol !== symbol);
    setWatchlist(updated);
    localStorage.setItem('risedualai_watchlist', JSON.stringify(updated));
  };

  return (
    <Card className="bg-slate-700/60 border-slate-400/25 rounded-xl p-4">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Star className="w-5 h-5 text-yellow-500 fill-yellow-500" />
          <h3 className="text-white font-semibold">My Watchlist</h3>
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
          <Button
            variant="ghost"
            size="sm"
            onClick={() => fetchQuotes(watchlist.map(item => item.symbol))}
            disabled={refreshing}
            className="text-slate-400 hover:text-slate-50 ml-1"
            data-testid="watchlist-refresh-btn"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
          </Button>
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
              watchlist.map((item) => (
                <div
                  key={item.symbol}
                  className="flex items-center justify-between p-3 bg-[#1E293B] rounded-lg hover:bg-slate-700 transition-colors"
                >
                  <div className="flex items-center gap-3">
                    <span className="text-white font-medium">{item.symbol}</span>
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
                      onClick={() => removeSymbol(item.symbol)}
                      className="text-slate-400 hover:text-orange-400 transition-colors"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </>
      )}
    </Card>
  );
};

export default Watchlist;