import React, { useState } from 'react';
import { Brain, BarChart3, Zap, Search, RefreshCw } from 'lucide-react';
import { Card } from './ui/card';
import { Button } from './ui/button';
import { useAuth, authFetch } from '../contexts/AuthContext';
import PatternsView from './intelligence/PatternsView';
import BriefView from './intelligence/BriefView';
import { getApiBase } from '../utils/apiBase';
import InfoTooltip from './InfoTooltip';

const API = `${getApiBase()}/api`;

const TABS = [
  { id: 'patterns', label: 'Patterns', icon: BarChart3, color: 'from-cyan-600 to-blue-600' },
  { id: 'brief', label: 'Quick Brief', icon: Zap, color: 'from-amber-600 to-orange-600' },
];

const AIIntelligence = ({ onSubscribe, prefetched }) => {
  const { isPro } = useAuth();
  const [tab, setTab] = useState('patterns');
  const [symbol, setSymbol] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const [activeSymbol, setActiveSymbol] = useState('');

  // When the WarRoomHub orchestrates a unified search, override the
  // component's internal state. Standalone usages (DashboardView)
  // pass nothing — the component fetches on its own as before.
  // ``prefetched.results`` is a dict keyed by tab id (patterns/brief).
  const isControlled = !!prefetched;
  const effSymbol = isControlled ? (prefetched.symbol || '') : activeSymbol;
  const effLoading = isControlled ? !!prefetched.loading : loading;
  const effError = isControlled ? (prefetched.error || '') : error;
  const effResult = isControlled ? (prefetched.results?.[tab] || null) : result;

  const analyze = async () => {
    if (!symbol.trim()) return;
    setLoading(true);
    setError('');
    setResult(null);
    const endpoint = { score: 'score', patterns: 'patterns', brief: 'brief' }[tab];
    try {
      const res = await authFetch(`${API}/intelligence/${endpoint}/${symbol.trim().toUpperCase()}`);
      if (res.status === 401) throw new Error('Session expired — please log in again.');
      if (res.status === 502 || res.status === 504) throw new Error('Server is busy — please try again in a moment.');
      if (!res.ok) {
        let detail;
        try { detail = (await res.json()).detail; } catch { detail = null; }
        throw new Error(detail || `Server error (${res.status}). Please try again.`);
      }
      const data = await res.json();
      setResult(data);
      setActiveSymbol(symbol.trim().toUpperCase());
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e) => { if (e.key === 'Enter') analyze(); };

  return (
    <div data-testid="ai-intelligence">
      <div className="flex items-center gap-3 mb-5">
        <div className="w-10 h-10 bg-gradient-to-br from-violet-600 to-blue-600 rounded-xl flex items-center justify-center">
          <Brain className="w-6 h-6 text-white" />
        </div>
        <div>
          <h2 className="text-white text-xl sm:text-2xl font-bold" style={{ fontFamily: 'Manrope, sans-serif' }}>AI Intelligence Hub</h2>
          <InfoTooltip id="ai-intelligence" />
          <p className="text-slate-300 text-xs">Strategist-powered stock scoring, pattern detection, and instant briefs</p>
        </div>
      </div>

      <div className="flex gap-2 mb-4 overflow-x-auto">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => { setTab(t.id); if (!isControlled) { setResult(null); setError(''); } }}
            className={`flex items-center gap-1.5 px-3 sm:px-4 py-2 rounded-xl text-xs sm:text-sm font-medium transition-all whitespace-nowrap ${tab === t.id ? `bg-gradient-to-r ${t.color} text-white shadow-lg` : 'bg-slate-800/60 text-slate-400 hover:text-white border border-slate-400/25'}`}
            data-testid={`tab-${t.id}`}
          >
            <t.icon className="w-4 h-4" />
            {t.label}
          </button>
        ))}
      </div>

      {/* Search — hidden when controlled by WarRoomHub */}
      {!isControlled && (
        <div className="flex gap-2 mb-5">
          <div className="relative flex-1">
            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
            <input
              value={symbol}
              onChange={e => setSymbol(e.target.value.toUpperCase())}
              onKeyDown={handleKeyDown}
              placeholder="Enter ticker (AAPL, TSLA, SPY...)"
              className="w-full bg-slate-800/80 border border-slate-400/30/60 rounded-xl pl-10 pr-4 py-3 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-violet-500/60"
              data-testid="intelligence-symbol-input"
            />
          </div>
          <Button
            onClick={analyze}
            disabled={loading || !symbol.trim()}
            className={`bg-gradient-to-r ${TABS.find(t => t.id === tab)?.color} text-white rounded-xl px-6`}
            data-testid="intelligence-analyze-btn"
          >
            {loading ? <RefreshCw className="w-4 h-4 animate-spin" /> : 'Analyze'}
          </Button>
        </div>
      )}

      {effError && <div className="bg-orange-800 border border-orange-700/50 text-orange-400 text-sm p-3 rounded-lg mb-4">{effError}</div>}

      {effLoading && (
        <div className="text-center py-12">
          <div className="w-10 h-10 border-2 border-violet-500 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
          <p className="text-slate-300 text-sm">Analyzing {effSymbol || symbol.toUpperCase()}...</p>
          <p className="text-slate-300 text-xs mt-1">Crunching technicals + AI inference</p>
        </div>
      )}

      {!effLoading && effResult && tab === 'patterns' && <PatternsView data={effResult} />}
      {!effLoading && effResult && tab === 'brief' && <BriefView data={effResult} />}

      {!effLoading && !effResult && !effError && (
        <div className="text-center py-10">
          <Brain className="w-10 h-10 text-slate-700 mx-auto mb-3" />
          <p className="text-slate-300 text-sm">
            {isControlled
              ? 'Enter a ticker in the unified search above to populate Intelligence'
              : 'Enter a ticker symbol and click Analyze'}
          </p>
        </div>
      )}
    </div>
  );
};

export default AIIntelligence;
