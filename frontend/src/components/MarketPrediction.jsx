import React, { useState, useEffect, useCallback } from 'react';
import { RefreshCw, TrendingUp, Lock, Zap, Sparkles, BookOpen, DollarSign } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { useAuth, authFetch } from '../contexts/AuthContext';
import { PredictionCard, MacroDataSection, RealEstateSection } from './prediction/PredictionCards';
import AdversarialHub from './AdversarialHub';
import AccuracyBadge from './AccuracyBadge';
import { getApiBase } from '../utils/apiBase';

const API = `${getApiBase()}/api`;

const MarketPrediction = ({ onSubscribe }) => {
  const { isPro } = useAuth();
  const [prediction, setPrediction] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState(null);
  const [activeTab, setActiveTab] = useState('overview');

  const fetchPrediction = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await authFetch(`${API}/market/prediction`);
      if (!res.ok) throw new Error('Failed to fetch prediction');
      const data = await res.json();
      setPrediction(data);
      setLastUpdated(new Date());
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPrediction();
  }, [fetchPrediction]);

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
            <p className="text-slate-300 text-xs sm:text-sm">Adversarial AI — Strategist predicts, Auditor vetoes weak signals</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {isPro && <AccuracyBadge feature="market_prediction" />}
          {lastUpdated && <span className="text-slate-300 text-xs">Updated {lastUpdated.toLocaleTimeString()}</span>}
          <Button size="sm" variant="outline" className="border-slate-600 text-white hover:bg-slate-700 rounded-xl" onClick={fetchPrediction} disabled={loading} data-testid="prediction-refresh">
            <RefreshCw className={`w-4 h-4 mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </Button>
        </div>
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

      {error && <div className="bg-orange-800 border border-orange-700/50 text-orange-400 text-sm p-3 rounded-lg">{error}</div>}

      {/* Loading */}
      {loading && !prediction && (
        <Card className="bg-slate-700/60 border-slate-400/30/40 rounded-xl p-12 text-center">
          <div className="animate-pulse space-y-3">
            <Sparkles className="w-8 h-8 text-[#3DE8D9] mx-auto animate-spin" />
            <p className="text-white font-medium">Scraping macro data & generating predictions...</p>
            <p className="text-slate-300 text-sm">Analyzing news, crypto, world events, congress, and foreign markets</p>
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
