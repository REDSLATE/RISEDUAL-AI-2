import React, { useState, useEffect, useCallback } from 'react';
import { RefreshCw, TrendingUp, Lock, Zap, Sparkles, BookOpen, DollarSign, Search } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { useAuth, authFetch } from '../contexts/AuthContext';
import { PredictionCard, MacroDataSection, RealEstateSection } from './prediction/PredictionCards';
import AdversarialHub from './AdversarialHub';
import AccuracyBadge from './AccuracyBadge';
import { getApiBase } from '../utils/apiBase';
import InfoTooltip from './InfoTooltip';

const API = `${getApiBase()}/api`;

const POPULAR_TICKERS = ['AAPL', 'TSLA', 'NVDA', 'BTC', 'SPY', 'AMZN', 'META', 'GOOGL'];

const MarketPrediction = ({ onSubscribe }) => {
  const { isPro } = useAuth();
  const [prediction, setPrediction] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState(null);
  const [activeTab, setActiveTab] = useState('overview');
  const [searchSymbol, setSearchSymbol] = useState('');
  const [activeSymbol, setActiveSymbol] = useState(null); // null = general market

  const [refreshing, setRefreshing] = useState(false);
  const [jobId, setJobId] = useState(null);

  const fetchPrediction = useCallback(async (symbol = null) => {
    setLoading(true);
    setError('');
    try {
      const endpoint = symbol
        ? `${API}/market/prediction/${symbol.toUpperCase()}`
        : `${API}/market/prediction`;
      const res = await authFetch(endpoint);
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `Server returned ${res.status}`);
      }
      const data = await res.json();
      setPrediction(data);
      setLastUpdated(new Date());
      setActiveSymbol(symbol ? symbol.toUpperCase() : null);
      // Track background job if running
      if (data.jobRunning && data.jobId) {
        setJobId(data.jobId);
        setRefreshing(true);
      } else {
        setRefreshing(false);
        setJobId(null);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const triggerRefresh = useCallback(async () => {
    try {
      setRefreshing(true);
      const res = await authFetch(`${API}/market/prediction/refresh`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        if (data.jobId) setJobId(data.jobId);
      }
    } catch {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchPrediction();
  }, [fetchPrediction]);

  // Poll job status when a background refresh is running
  useEffect(() => {
    if (!jobId) return;
    const timer = setInterval(async () => {
      try {
        const res = await authFetch(`${API}/market/prediction/status/${jobId}`);
        if (!res.ok) { clearInterval(timer); setRefreshing(false); return; }
        const data = await res.json();
        if (data.status === 'ready') {
          clearInterval(timer);
          setRefreshing(false);
          setJobId(null);
          fetchPrediction(activeSymbol);
        } else if (data.status === 'failed') {
          clearInterval(timer);
          setRefreshing(false);
          setJobId(null);
        }
      } catch {
        clearInterval(timer);
        setRefreshing(false);
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [jobId, activeSymbol, fetchPrediction]);

  const handleSearch = (e) => {
    e.preventDefault();
    const s = searchSymbol.trim().toUpperCase();
    if (s) fetchPrediction(s);
  };

  const handleQuickTicker = (ticker) => {
    setSearchSymbol(ticker);
    fetchPrediction(ticker);
  };

  const tabs = [
    { key: 'overview', label: 'Overview', icon: TrendingUp },
    { key: 'macro', label: 'Macro Intelligence', icon: BookOpen },
    { key: 'real_estate', label: 'Real Estate', icon: DollarSign },
  ];

  return (
    <div className="space-y-6" data-testid="market-prediction">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-gradient-to-br from-[#3DE8D9] to-cyan-500 rounded-xl flex items-center justify-center">
            <Sparkles className="w-6 h-6 text-white" />
          </div>
          <div>
            <h2 className="text-white text-xl sm:text-2xl font-bold" style={{ fontFamily: 'Manrope, sans-serif' }}>AI Market Predictions</h2>
            <InfoTooltip id="market-prediction" />
            <p className="text-slate-300 text-xs sm:text-sm">Adversarial AI — Strategist predicts, Auditor vetoes weak signals</p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {isPro && <AccuracyBadge feature="market_prediction" />}
          {prediction?.isStale && !refreshing && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-300 border border-amber-400/30 font-medium" data-testid="stale-badge">
              Stale
            </span>
          )}
          {refreshing && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-blue-500/15 text-blue-300 border border-blue-400/30 font-medium animate-pulse" data-testid="refreshing-badge">
              Refreshing...
            </span>
          )}
          {prediction?._cache?.hit && !prediction?.isStale && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-violet-500/15 text-violet-300 border border-violet-400/30 font-medium" data-testid="cache-badge">
              Cached
            </span>
          )}
          {prediction?._cache?.policy && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-slate-800/60 text-slate-500 font-mono" data-testid="prediction-source-badge">
              {prediction._cache.hit && !prediction.isStale ? 'instant' : refreshing ? 'updating' : 'live'}
            </span>
          )}
          {lastUpdated && <span className="text-slate-300 text-xs">Updated {lastUpdated.toLocaleTimeString()}</span>}
          <Button size="sm" variant="outline" className="border-slate-600 text-white hover:bg-slate-700 rounded-xl" onClick={() => refreshing ? null : triggerRefresh()} disabled={loading || refreshing} data-testid="prediction-refresh">
            <RefreshCw className={`w-4 h-4 mr-1 ${loading || refreshing ? 'animate-spin' : ''}`} /> {refreshing ? 'Refreshing' : 'Refresh'}
          </Button>
        </div>
      </div>

      {/* Ticker Search */}
      <div className="space-y-2">
        <form onSubmit={handleSearch} className="flex gap-2">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4" />
            <Input
              type="text"
              placeholder="Search any ticker (AAPL, BTC, TSLA, NVDA...)"
              value={searchSymbol}
              onChange={e => setSearchSymbol(e.target.value.toUpperCase())}
              className="pl-10 bg-slate-800 border-slate-600 text-white placeholder-slate-500 focus:border-[#3DE8D9] rounded-xl"
              data-testid="prediction-search"
            />
          </div>
          <Button type="submit" disabled={loading || !searchSymbol.trim()}
            className="bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white rounded-xl px-5"
            data-testid="prediction-search-btn">
            {loading ? <RefreshCw className="w-4 h-4 animate-spin" /> : 'Analyze'}
          </Button>
        </form>
        <div className="flex items-center gap-1.5 flex-wrap">
          <button
            onClick={() => { setSearchSymbol(''); fetchPrediction(null); }}
            className={`text-[10px] px-2.5 py-1 rounded-full border transition-colors ${
              !activeSymbol ? 'bg-[#3DE8D9]/15 text-[#3DE8D9] border-[#3DE8D9]/30' : 'bg-slate-800 text-slate-400 border-slate-600 hover:text-white'
            }`}
            data-testid="prediction-general-market"
          >
            General Market
          </button>
          {POPULAR_TICKERS.map(t => (
            <button
              key={t}
              onClick={() => handleQuickTicker(t)}
              className={`text-[10px] px-2.5 py-1 rounded-full border transition-colors ${
                activeSymbol === t ? 'bg-[#3DE8D9]/15 text-[#3DE8D9] border-[#3DE8D9]/30' : 'bg-slate-800 text-slate-400 border-slate-600 hover:text-white'
              }`}
              data-testid={`prediction-quick-${t}`}
            >
              {t}
            </button>
          ))}
        </div>
        {activeSymbol && (
          <div className="flex items-center gap-2">
            <Badge className="bg-[#3DE8D9]/10 text-[#3DE8D9] border-[#3DE8D9]/20 text-xs">
              Analyzing: {activeSymbol}
            </Badge>
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-slate-400/25 pb-1 overflow-x-auto">
        {tabs.map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`flex items-center gap-1.5 px-3 sm:px-4 py-2 text-xs font-medium rounded-t-lg transition-colors whitespace-nowrap ${
              activeTab === tab.key
                ? 'bg-slate-800 text-[#3DE8D9] border-b-2 border-[#3DE8D9]'
                : 'text-slate-400 hover:text-white hover:bg-slate-700/70'
            }`}
            data-testid={`tab-${tab.key}`}
          >
            <tab.icon className="w-3.5 h-3.5" /> {tab.label}
          </button>
        ))}
      </div>

      {error && (
        <div className={`border text-sm p-3 rounded-lg ${error.toLowerCase().includes('credit') ? 'bg-amber-900/30 border-amber-600/30 text-amber-300' : 'bg-orange-800 border-orange-700/50 text-orange-400'}`} data-testid="prediction-error">
          {error.toLowerCase().includes('credit') ? `${error} — Go to Settings to top up or upgrade your plan.` : error}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <Card className="bg-slate-700/60 border-slate-400/30/40 rounded-xl p-12 text-center">
          <div className="animate-pulse space-y-3">
            <Sparkles className="w-8 h-8 text-[#3DE8D9] mx-auto animate-spin" />
            <p className="text-white font-medium">
              {activeSymbol || searchSymbol
                ? `Analyzing ${(activeSymbol || searchSymbol).toUpperCase()}...`
                : 'Scraping macro data & generating predictions...'}
            </p>
            <p className="text-slate-300 text-sm">
              {activeSymbol || searchSymbol
                ? `Running adversarial AI pipeline for ${(activeSymbol || searchSymbol).toUpperCase()}`
                : 'Analyzing news, crypto, world events, congress, and foreign markets'}
            </p>
          </div>
        </Card>
      )}

      {/* Content */}
      {prediction && (
        <div className="space-y-5">
          {activeTab === 'overview' && (
            <>
              <AdversarialHub prediction={prediction} />
              <PredictionCard prediction={prediction} />
            </>
          )}

          {activeTab === 'macro' && (
            <>
              {!isPro ? (
                <LockedMacro macroData={prediction.macro_data} onSubscribe={onSubscribe} />
              ) : (
                <MacroDataSection macroData={prediction.macro_data} />
              )}
            </>
          )}

          {activeTab === 'real_estate' && (
            <>
              {!isPro ? (
                <LockedSection label="Real Estate Intelligence" onSubscribe={onSubscribe} />
              ) : (
                <RealEstateSection realEstate={prediction.real_estate_summary} />
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
};

const LockedMacro = ({ macroData, onSubscribe }) => {
  const gf = macroData?.gov_filings || {};
  return (
    <Card className="relative bg-slate-700/60 border-slate-400/30/40 rounded-xl overflow-hidden">
      <div className="p-6 space-y-3">
        <p className="text-white font-semibold">Macro Intelligence Summary</p>
        <div className="grid grid-cols-3 gap-3">
          <div className="bg-slate-800/50 rounded-lg p-3 text-center">
            <p className="text-white font-bold">{macroData?.world_events?.total || 0}</p>
            <p className="text-slate-400 text-[10px]">World Events</p>
          </div>
          <div className="bg-slate-800/50 rounded-lg p-3 text-center">
            <p className="text-white font-bold">{gf.congressional_trades || 0}</p>
            <p className="text-slate-400 text-[10px]">Congress Trades</p>
          </div>
          <div className="bg-slate-800/50 rounded-lg p-3 text-center">
            <p className="text-white font-bold">{macroData?.foreign_markets?.total_indices || 0}</p>
            <p className="text-slate-400 text-[10px]">Market Indices</p>
          </div>
        </div>
      </div>
      <LockedOverlay label="Macro Intelligence" onSubscribe={onSubscribe} />
    </Card>
  );
};

const LockedSection = ({ label, onSubscribe }) => (
  <Card className="relative bg-slate-700/60 border-slate-400/30/40 rounded-xl overflow-hidden p-6 min-h-[150px]">
    <LockedOverlay label={label} onSubscribe={onSubscribe} />
  </Card>
);

const LockedOverlay = ({ label, onSubscribe }) => (
  <div className="absolute inset-0 flex flex-col items-center justify-center bg-slate-900/60 backdrop-blur-sm">
    <Lock className="w-8 h-8 text-[#3DE8D9] mb-3" />
    <p className="text-white font-semibold mb-1">Unlock {label}</p>
    <p className="text-slate-300 text-xs text-center mb-3">Full analysis available with Pro subscription</p>
    <Button size="sm" onClick={onSubscribe} className="bg-[#3DE8D9] hover:bg-[#7AEEE0] text-white rounded-xl" data-testid="prediction-subscribe-btn">
      <Zap className="w-3 h-3 mr-1" /> Upgrade to Pro
    </Button>
  </div>
);

export default MarketPrediction;
