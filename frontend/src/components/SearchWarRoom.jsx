import React, { useState, useCallback } from 'react';
import { Search, Globe, FileText, TrendingUp, BookOpen, AlertTriangle, Loader2, Database } from 'lucide-react';
import { Button } from './ui/button';
import { authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';
import { toast } from 'sonner';

const API = getApiBase();

const trustStyles = {
  official: "bg-emerald-500/15 text-emerald-300 border-emerald-400/30",
  search: "bg-sky-500/15 text-sky-300 border-sky-400/30",
  fallback: "bg-amber-500/15 text-amber-300 border-amber-400/30",
  cached: "bg-violet-500/15 text-violet-300 border-violet-400/30",
};

const statusStyles = {
  live: "bg-white/10 text-white border-white/15",
  partial: "bg-yellow-500/15 text-yellow-200 border-yellow-400/30",
  timeout: "bg-orange-500/15 text-orange-200 border-orange-400/30",
  error: "bg-red-500/15 text-red-200 border-red-400/30",
};

const engineIcons = {
  wikipedia: BookOpen,
  sec: FileText,
  fred: Database,
  ddg: Search,
  ddg_news: Globe,
  yahoo: TrendingUp,
};

function getTrustBadge(result) {
  if (result.cached) return { label: "Cached", style: trustStyles.cached };
  if (result.engine === "sec" || result.engine === "fred") {
    return { label: "Official", style: trustStyles.official };
  }
  if (result.engine === "ddg" || result.engine === "ddg_news") return { label: "Search", style: trustStyles.search };
  return { label: "Fallback", style: trustStyles.fallback };
}

function getStatusBadge(result) {
  if (result.status === "ok" || result.status === "cached") {
    return { label: "Live", style: statusStyles.live };
  }
  if (result.status === "timeout") {
    return { label: "Timeout", style: statusStyles.timeout };
  }
  if (result.status === "error") {
    return { label: "Error", style: statusStyles.error };
  }
  return { label: "Partial", style: statusStyles.partial };
}

function SourceCard({ result }) {
  const trust = getTrustBadge(result);
  const status = getStatusBadge(result);
  const Icon = engineIcons[result.engine] || Globe;

  return (
    <div className="rounded-2xl border border-white/10 bg-[#0B1426] p-4 shadow-lg" data-testid={`source-card-${result.engine}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-white/5 flex items-center justify-center shrink-0">
            <Icon className="w-4 h-4 text-slate-300" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-white">
              {result.title || result.engine.toUpperCase()}
            </h3>
            <p className="text-[11px] text-slate-500">{result.source_type}</p>
          </div>
        </div>

        <div className="flex flex-wrap gap-1.5 justify-end">
          <span
            className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium ${trust.style}`}
          >
            {trust.label}
          </span>
          <span
            className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium ${status.style}`}
          >
            {status.label}
          </span>
        </div>
      </div>

      <p className="mt-3 text-xs leading-5 text-slate-300">
        {result.summary || "No summary available."}
      </p>

      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-slate-500">
        <span>Confidence: {Math.round((result.confidence || 0) * 100)}%</span>
        <span>Items: {result.items?.length || 0}</span>
        {result.freshness_seconds != null && (
          <span>Freshness: {result.freshness_seconds}s</span>
        )}
      </div>

      {result.error && (
        <div className="mt-2 rounded-lg border border-red-400/20 bg-red-500/10 px-2.5 py-1.5 text-[11px] text-red-300">
          {result.error}
        </div>
      )}
    </div>
  );
}

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
      const res = await authFetch(`${API}/web-intel/war-room`, {
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
          <button onClick={onClose} className="text-slate-500 hover:text-white text-xs">Close</button>
        )}
      </div>

      {/* Search controls */}
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

      {/* Brief */}
      {brief && (
        <div className="rounded-2xl border border-sky-400/20 bg-sky-500/5 p-4" data-testid="search-brief">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-sm font-bold text-white">{brief.headline}</h3>
            <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
              result.degraded ? 'bg-yellow-500/15 text-yellow-300 border border-yellow-400/30' : 'bg-emerald-500/15 text-emerald-300 border border-emerald-400/30'
            }`}>
              {okCount}/{engines.length} engines
            </span>
          </div>
          <p className="text-xs text-slate-300 leading-5">{brief.summary}</p>

          {brief.signals?.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {brief.signals.map((s, i) => (
                <span key={`signal-${i}`} className="text-[10px] bg-white/5 text-slate-300 px-2 py-0.5 rounded-full border border-white/10">
                  {s}
                </span>
              ))}
            </div>
          )}

          {brief.risks?.length > 0 && (
            <div className="mt-2 space-y-1">
              {brief.risks.map((r, i) => (
                <div key={`risk-${i}`} className="flex items-center gap-1.5 text-[11px] text-amber-300">
                  <AlertTriangle className="w-3 h-3 shrink-0" />
                  {r}
                </div>
              ))}
            </div>
          )}

          {brief.sources_used?.length > 0 && (
            <div className="mt-2 text-[10px] text-slate-500">
              Sources: {brief.sources_used.join(', ')}
            </div>
          )}
        </div>
      )}

      {/* Engine results */}
      {engines.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2" data-testid="engine-results">
          {engines.map((e, i) => (
            <SourceCard key={`${e.engine}-${i}`} result={e} />
          ))}
        </div>
      )}

      {/* Warnings */}
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
