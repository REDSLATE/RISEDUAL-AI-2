import React, { useState, useCallback } from 'react';
import { Search, Globe, Loader2 } from 'lucide-react';
import { Button } from './ui/button';
import { WarRoomSourceCard, WarRoomBriefHeader } from './warroom/SearchWarRoomCards';
import { authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';
import { toast } from 'sonner';

const API = getApiBase();

const MODES = [
  { value: 'auto', label: 'Auto' },
  { value: 'company', label: 'Company' },
  { value: 'macro', label: 'Macro' },
  { value: 'filing', label: 'Filings' },
  { value: 'news', label: 'News' },
];

export default function SearchWarRoom({ onClose }) {
  const [query, setQuery] = useState('');
  const [symbol, setSymbol] = useState('');
  const [mode, setMode] = useState('auto');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

  const runSearch = useCallback(async () => {
    if (!query.trim()) return toast.error('Enter a search query');
    setLoading(true);
    setResult(null);
    try {
      const body = { query: query.trim(), mode };
      if (symbol.trim()) body.symbol = symbol.trim().toUpperCase();
      const res = await authFetch(`${API}/api/web-intel/war-room`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Search failed');
      }
      setResult(await res.json());
    } catch (e) {
      toast.error(e.message || 'Search War Room error');
    } finally {
      setLoading(false);
    }
  }, [query, symbol, mode]);

  const brief = result?.brief;
  const engines = result?.engine_results || [];
  const okCount = engines.filter(e => e.status === 'ok' || e.status === 'cached').length;

  return (
    <div className="space-y-5" data-testid="search-war-room">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="w-9 h-9 rounded-xl bg-sky-500/10 flex items-center justify-center">
            <Globe className="w-5 h-5 text-sky-400" />
          </div>
          <div>
            <h2 className="text-white font-bold text-base">Search War Room</h2>
            <p className="text-slate-500 text-[11px]">Multi-engine parallel intelligence</p>
          </div>
        </div>
        {onClose && (
          <button onClick={onClose} className="text-slate-500 hover:text-white text-xs" data-testid="search-war-room-close">Close</button>
        )}
      </div>

      <div className="space-y-3">
        <div className="flex gap-2">
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && runSearch()}
            placeholder="Search anything — NVDA earnings, Fed rates, SEC filings..."
            className="flex-1 bg-white/5 border border-white/10 rounded-xl px-3.5 py-2.5 text-sm text-white placeholder:text-slate-500 focus:outline-none focus:border-sky-500/50"
            data-testid="search-war-room-input"
          />
          <input
            value={symbol}
            onChange={e => setSymbol(e.target.value)}
            placeholder="Ticker"
            className="w-24 bg-white/5 border border-white/10 rounded-xl px-3 py-2.5 text-sm text-white placeholder:text-slate-500 focus:outline-none focus:border-sky-500/50 uppercase"
            data-testid="search-war-room-symbol"
          />
        </div>

        <div className="flex items-center justify-between">
          <div className="flex gap-1.5">
            {MODES.map(m => (
              <button
                key={m.value}
                onClick={() => setMode(m.value)}
                className={`px-2.5 py-1 rounded-lg text-[11px] font-medium transition-colors ${
                  mode === m.value
                    ? 'bg-sky-500/20 text-sky-300 border border-sky-400/30'
                    : 'bg-white/5 text-slate-400 border border-white/10 hover:text-white'
                }`}
                data-testid={`mode-${m.value}`}
              >
                {m.label}
              </button>
            ))}
          </div>
          <Button
            onClick={runSearch}
            disabled={loading || !query.trim()}
            className="bg-sky-500 hover:bg-sky-400 text-white h-8 px-4 text-xs font-bold"
            data-testid="search-war-room-submit"
          >
            {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" /> : <Search className="w-3.5 h-3.5 mr-1" />}
            {loading ? 'Searching...' : 'Deploy'}
          </Button>
        </div>
      </div>

      {brief && (
        <WarRoomBriefHeader brief={brief} degraded={result.degraded} />
      )}

      {engines.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2" data-testid="engine-results">
          {engines.map((e, i) => (
            <WarRoomSourceCard key={`${e.engine}-${i}`} result={e} />
          ))}
        </div>
      )}

      {result?.warnings?.length > 0 && (
        <div className="text-[11px] text-slate-500 space-y-0.5">
          {result.warnings.map((w, i) => (
            <div key={`warn-${i}`}>{w}</div>
          ))}
        </div>
      )}
    </div>
  );
}
