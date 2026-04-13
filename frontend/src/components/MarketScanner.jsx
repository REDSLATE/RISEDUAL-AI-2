import React, { useState, useEffect, useCallback } from 'react';
import { X, Search, RefreshCw, Filter, TrendingUp, TrendingDown, Minus, Zap, BarChart3, Activity, Wrench } from 'lucide-react';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { toast } from './ui/sonner';
import { authFetch } from '../contexts/AuthContext';
import { getApiBase } from '../utils/apiBase';
import RuleBuilder from './scanner/RuleBuilder';

const API = `${getApiBase()}/api/scanner`;

const SIGNAL_COLORS = {
  bullish: { bg: 'bg-lime-500/10', text: 'text-lime-400', border: 'border-lime-500/20', icon: TrendingUp },
  bearish: { bg: 'bg-red-500/10', text: 'text-red-400', border: 'border-red-500/20', icon: TrendingDown },
  neutral: { bg: 'bg-amber-500/10', text: 'text-amber-400', border: 'border-amber-500/20', icon: Minus },
};

const CATEGORY_ICONS = { momentum: Zap, mean_reversion: Activity, trend: TrendingUp, volatility: BarChart3, volume: Activity };

const StrengthBar = ({ strength }) => {
  const color = strength >= 70 ? 'bg-lime-500' : strength >= 40 ? 'bg-amber-500' : 'bg-red-500';
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1.5 bg-slate-800 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${Math.min(strength, 100)}%` }} />
      </div>
      <span className="text-[9px] text-slate-400 w-8">{strength}%</span>
    </div>
  );
};

const StrategyCard = ({ id, info, matches, matchCount, isSelected, onSelect, isScanning }) => {
  const sig = SIGNAL_COLORS[info.signal] || SIGNAL_COLORS.neutral;
  const CatIcon = CATEGORY_ICONS[info.category] || Activity;
  return (
    <button
      onClick={() => onSelect(id)}
      className={`w-full text-left p-3 rounded-xl border transition-all ${
        isSelected ? `${sig.bg} ${sig.border} border-opacity-100` : 'bg-slate-800/30 border-slate-600/20 hover:border-slate-500/40'
      }`}
      data-testid={`strategy-card-${id}`}
    >
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-1.5">
          <CatIcon className={`w-3.5 h-3.5 ${sig.text}`} />
          <span className="text-white text-xs font-semibold">{info.name}</span>
        </div>
        <div className="flex items-center gap-1.5">
          {matchCount > 0 && (
            <Badge className={`text-[8px] px-1.5 ${sig.bg} ${sig.text}`}>{matchCount}</Badge>
          )}
          <Badge className={`text-[8px] px-1.5 ${sig.bg} ${sig.text}`}>{info.signal}</Badge>
        </div>
      </div>
      <p className="text-slate-400 text-[10px] leading-snug">{info.description}</p>
    </button>
  );
};

const MatchRow = ({ match }) => (
  <div className="flex items-center justify-between py-2 px-3 bg-slate-800/30 rounded-lg border border-slate-600/10" data-testid={`match-${match.symbol}`}>
    <div className="flex items-center gap-3 min-w-0">
      <span className="text-white text-sm font-bold w-14">{match.symbol}</span>
      <span className="text-slate-300 text-xs">${match.price}</span>
      <Badge className={`text-[8px] ${match.trend === 'bullish' || match.trend === 'strong_bullish' ? 'bg-lime-500/10 text-lime-400' : match.trend === 'bearish' ? 'bg-red-500/10 text-red-400' : 'bg-slate-700 text-slate-400'}`}>
        {match.trend}
      </Badge>
    </div>
    <div className="flex items-center gap-3">
      <span className="text-slate-400 text-[10px] max-w-[200px] truncate">{match.detail}</span>
      <StrengthBar strength={match.strength} />
    </div>
  </div>
);

const MarketScanner = ({ onClose }) => {
  const [strategies, setStrategies] = useState([]);
  const [results, setResults] = useState(null);
  const [selected, setSelected] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [filter, setFilter] = useState('all');
  const [mode, setMode] = useState('presets');
  const [customResults, setCustomResults] = useState(null);

  const loadStrategies = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/strategies`);
      if (res.ok) {
        const data = await res.json();
        setStrategies(data.strategies || []);
      }
    } catch { /* */ }
  }, []);

  const runFullScan = useCallback(async () => {
    setScanning(true);
    try {
      const res = await authFetch(`${API}/scan`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
      });
      if (res.ok) {
        const data = await res.json();
        setResults(data);
        toast.success(`Scanned ${data.scanned} symbols`);
        // Auto-select first strategy with matches
        const firstMatch = Object.entries(data.strategies || {}).find(([, v]) => v.match_count > 0);
        if (firstMatch) setSelected(firstMatch[0]);
      } else {
        toast.error('Scan failed');
      }
    } catch { toast.error('Scan error'); }
    finally { setScanning(false); }
  }, []);

  useEffect(() => { loadStrategies(); }, [loadStrategies]);
  useEffect(() => { runFullScan(); }, [runFullScan]);

  const filteredStrategies = strategies.filter(s => {
    if (filter === 'all') return true;
    return s.signal === filter || s.category === filter;
  });

  const selectedData = results?.strategies?.[selected];
  const totalMatches = results ? Object.values(results.strategies || {}).reduce((s, v) => s + (v.match_count || 0), 0) : 0;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm" data-testid="market-scanner">
      <div className="w-full max-w-4xl max-h-[90vh] overflow-hidden bg-[#0B1426] border border-slate-600/30 rounded-2xl flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-400/20 shrink-0">
          <div className="flex items-center gap-2">
            <Search className="w-5 h-5 text-[#3DE8D9]" />
            <h2 className="text-white font-bold text-base">Market Scanner</h2>
            {results && (
              <span className="text-slate-400 text-[10px]">
                {results.scanned} symbols | {totalMatches} signals
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1 bg-slate-800/60 rounded-lg p-0.5">
              <button onClick={() => setMode('presets')}
                className={`px-2.5 py-1 rounded-md text-[10px] font-medium ${mode === 'presets' ? 'bg-[#3DE8D9] text-white' : 'text-slate-400 hover:text-white'}`}
                data-testid="scanner-mode-presets">
                <Search className="w-3 h-3 inline mr-1" />Presets
              </button>
              <button onClick={() => setMode('custom')}
                className={`px-2.5 py-1 rounded-md text-[10px] font-medium ${mode === 'custom' ? 'bg-violet-500 text-white' : 'text-slate-400 hover:text-white'}`}
                data-testid="scanner-mode-custom">
                <Wrench className="w-3 h-3 inline mr-1" />Builder
              </button>
            </div>
            {mode === 'presets' && (
              <Button size="sm" variant="outline" onClick={runFullScan} disabled={scanning}
                className="bg-slate-800 border-slate-400/30 text-slate-300 text-xs rounded-xl h-7" data-testid="scanner-refresh">
                <RefreshCw className={`w-3 h-3 mr-1 ${scanning ? 'animate-spin' : ''}`} /> {scanning ? 'Scanning...' : 'Scan'}
              </Button>
            )}
            <button onClick={onClose} className="text-slate-400 hover:text-white"><X className="w-5 h-5" /></button>
          </div>
        </div>

        {mode === 'custom' ? (
          /* Custom Rule Builder Mode */
          <div className="flex flex-1 min-h-0">
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              <RuleBuilder onScanResults={setCustomResults} />
              {customResults && customResults.matches?.length > 0 && (
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-white text-xs font-semibold">Results</span>
                    <Badge className="bg-[#3DE8D9]/15 text-[#3DE8D9] text-[10px]">{customResults.match_count} matches</Badge>
                  </div>
                  {customResults.matches.map(m => <MatchRow key={m.symbol} match={{...m, strength: 75, detail: `RSI=${m.rsi || '?'} Vol=${m.volume_ratio || '?'}x`}} />)}
                </div>
              )}
              {customResults && customResults.match_count === 0 && (
                <div className="text-center py-8">
                  <Filter className="w-8 h-8 text-slate-600 mx-auto mb-2" />
                  <p className="text-slate-400 text-sm">No matches — try adjusting your conditions</p>
                </div>
              )}
            </div>
          </div>
        ) : (
        <>
        {/* Filter Bar */}
        <div className="flex items-center gap-1.5 px-5 py-2 border-b border-slate-400/15 shrink-0 overflow-x-auto">
          {[
            { id: 'all', label: 'All' },
            { id: 'bullish', label: 'Bullish' },
            { id: 'bearish', label: 'Bearish' },
            { id: 'momentum', label: 'Momentum' },
            { id: 'mean_reversion', label: 'Reversion' },
            { id: 'trend', label: 'Trend' },
            { id: 'volatility', label: 'Volatility' },
            { id: 'volume', label: 'Volume' },
          ].map(f => (
            <button key={f.id} onClick={() => setFilter(f.id)}
              className={`px-2.5 py-1 rounded-lg text-[10px] font-medium whitespace-nowrap transition-colors ${
                filter === f.id ? 'bg-[#3DE8D9] text-white' : 'bg-slate-800/60 text-slate-400 hover:text-white'
              }`} data-testid={`scanner-filter-${f.id}`}>
              {f.label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex flex-1 min-h-0">
          {/* Strategy List */}
          <div className="w-72 border-r border-slate-400/15 overflow-y-auto p-3 space-y-2 shrink-0" data-testid="strategy-list">
            {scanning && !results ? (
              <div className="flex flex-col items-center justify-center py-12 gap-2">
                <RefreshCw className="w-6 h-6 text-[#3DE8D9] animate-spin" />
                <p className="text-slate-400 text-xs">Scanning markets...</p>
              </div>
            ) : (
              filteredStrategies.map(s => (
                <StrategyCard
                  key={s.id} id={s.id} info={s}
                  matches={results?.strategies?.[s.id]?.matches || []}
                  matchCount={results?.strategies?.[s.id]?.match_count || 0}
                  isSelected={selected === s.id}
                  onSelect={setSelected}
                  isScanning={scanning}
                />
              ))
            )}
          </div>

          {/* Results Panel */}
          <div className="flex-1 overflow-y-auto p-4" data-testid="scanner-results">
            {!selected ? (
              <div className="flex flex-col items-center justify-center h-full text-center">
                <Search className="w-10 h-10 text-slate-600 mb-3" />
                <p className="text-slate-400 text-sm">Select a strategy to view matches</p>
                <p className="text-slate-500 text-[10px] mt-1">Click any strategy card on the left</p>
              </div>
            ) : selectedData ? (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="text-white font-semibold text-sm">{selectedData.info?.name}</h3>
                    <p className="text-slate-400 text-[10px]">{selectedData.info?.description}</p>
                  </div>
                  <Badge className={`text-xs ${
                    selectedData.match_count > 0 ? 'bg-[#3DE8D9]/15 text-[#3DE8D9]' : 'bg-slate-700 text-slate-400'
                  }`}>
                    {selectedData.match_count} match{selectedData.match_count !== 1 ? 'es' : ''}
                  </Badge>
                </div>

                {selectedData.matches?.length > 0 ? (
                  <div className="space-y-1.5">
                    {selectedData.matches.map(m => (
                      <MatchRow key={m.symbol} match={m} />
                    ))}
                  </div>
                ) : (
                  <div className="flex flex-col items-center justify-center py-12 text-center">
                    <Filter className="w-8 h-8 text-slate-600 mb-2" />
                    <p className="text-slate-400 text-sm">No matches for this strategy</p>
                    <p className="text-slate-500 text-[10px] mt-1">Try scanning more symbols or check back later</p>
                  </div>
                )}
              </div>
            ) : null}
          </div>
        </div>
        </>
        )}
      </div>
    </div>
  );
};

export default MarketScanner;
