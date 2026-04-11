import React, { useState } from 'react';
import { Brain, Target, BarChart3, Zap, Search, RefreshCw } from 'lucide-react';
import { Card } from './ui/card';
import { Button } from './ui/button';
import { useAuth, authFetch } from '../contexts/AuthContext';
import ScoreView from './intelligence/ScoreView';
import PatternsView from './intelligence/PatternsView';
import BriefView from './intelligence/BriefView';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

const TABS = [
  { id: 'score', label: 'AI Score', icon: Target, color: 'from-violet-600 to-purple-600' },
  { id: 'patterns', label: 'Patterns', icon: BarChart3, color: 'from-cyan-600 to-blue-600' },
  { id: 'brief', label: 'Quick Brief', icon: Zap, color: 'from-amber-600 to-orange-600' },
];

const AIIntelligence = ({ onSubscribe }) => {
  const { isPro } = useAuth();
  const [tab, setTab] = useState('score');
  const [symbol, setSymbol] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const [activeSymbol, setActiveSymbol] = useState('');

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
          <p className="text-slate-300 text-xs">Strategist-powered stock scoring, pattern detection, and instant briefs</p>
        </div>
      </div>

      <div className="flex gap-2 mb-4">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => { setTab(t.id); setResult(null); setError(''); }}
            className={`flex items-center gap-1.5 px-4 py-2 rounded-xl text-sm font-medium transition-all ${tab === t.id ? `bg-gradient-to-r ${t.color} text-white shadow-lg` : 'bg-slate-800/60 text-slate-400 hover:text-white border border-slate-400/25'}`}
            data-testid={`tab-${t.id}`}
          >
            <t.icon className="w-4 h-4" />
            {t.label}
          </button>
        ))}
      </div>

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

      {error && <div className="bg-orange-900/30 border border-orange-700/50 text-orange-400 text-sm p-3 rounded-lg mb-4">{error}</div>}

      {loading && (
        <div className="text-center py-12">
          <div className="w-10 h-10 border-2 border-violet-500 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
          <p className="text-slate-300 text-sm">Analyzing {symbol.toUpperCase()}...</p>
          <p className="text-slate-300 text-xs mt-1">Crunching technicals + AI inference</p>
        </div>
      )}

      {!loading && result && tab === 'score' && <ScoreView data={result} />}
      {!loading && result && tab === 'patterns' && <PatternsView data={result} />}
      {!loading && result && tab === 'brief' && <BriefView data={result} />}

      {!loading && !result && !error && (
        <div className="text-center py-10">
          <Brain className="w-10 h-10 text-slate-700 mx-auto mb-3" />
          <p className="text-slate-300 text-sm">Enter a ticker symbol and click Analyze</p>
        </div>
      )}
    </div>
  );
};

export default AIIntelligence;
