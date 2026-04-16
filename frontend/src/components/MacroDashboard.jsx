import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Globe, RefreshCw, Clock, Radio, BarChart3, Landmark, Activity } from 'lucide-react';
import { Button } from './ui/button';
import { useAuth } from '../contexts/AuthContext';
import WorldEventsTab from './macro/WorldEventsTab';
import ForeignMarketsTab from './macro/ForeignMarketsTab';
import CongressTab from './macro/CongressTab';
import FredEconomyTab from './macro/FredEconomyTab';
import logger from '../utils/logger';
import { getApiBase } from '../utils/apiBase';
import InfoTooltip from './InfoTooltip';

const BACKEND_URL = getApiBase();

const AUTO_REFRESH_MS = 30000; // 30 seconds

const TABS = [
  { id: 'world', label: 'World Events', icon: Globe },
  { id: 'markets', label: 'Foreign Markets', icon: BarChart3 },
  { id: 'congress', label: 'Congress Trades', icon: Landmark },
  { id: 'fred', label: 'FRED Economy', icon: Activity },
];

const MacroDashboard = ({ onSubscribe }) => {
  const { isPro } = useAuth();
  const [activeTab, setActiveTab] = useState('world');
  const [worldEvents, setWorldEvents] = useState(null);
  const [foreignMarkets, setForeignMarkets] = useState(null);
  const [govFilings, setGovFilings] = useState(null);
  const [loading, setLoading] = useState({});
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [changedSymbols, setChangedSymbols] = useState(new Set());
  const prevMarketsRef = useRef(null);

  const fetchData = useCallback(async (endpoint, setter, key) => {
    setLoading(prev => ({ ...prev, [key]: true }));
    try {
      const res = await fetch(`${BACKEND_URL}/api/${endpoint}`);
      if (res.ok) setter(await res.json());
    } catch (e) {
      logger.error(`Error fetching ${endpoint}:`, e);
    } finally {
      setLoading(prev => ({ ...prev, [key]: false }));
    }
  }, []);

  // Detect price changes for pulse animation
  const fetchMarketsWithDiff = useCallback(async () => {
    setLoading(prev => ({ ...prev, markets: true }));
    try {
      const res = await fetch(`${BACKEND_URL}/api/foreign-markets`);
      if (res.ok) {
        const newData = await res.json();
        // Compare with previous data to find changed symbols
        if (prevMarketsRef.current) {
          const changes = new Set();
          const allPrev = [
            ...(prevMarketsRef.current.asia || []),
            ...(prevMarketsRef.current.europe || []),
            ...(prevMarketsRef.current.americas || []),
            ...(prevMarketsRef.current.commodities || []),
            ...(prevMarketsRef.current.currencies || []),
          ];
          const allNew = [
            ...(newData.asia || []),
            ...(newData.europe || []),
            ...(newData.americas || []),
            ...(newData.commodities || []),
            ...(newData.currencies || []),
          ];
          const prevMap = {};
          allPrev.forEach(m => { prevMap[m.symbol] = m.price; });
          allNew.forEach(m => {
            if (prevMap[m.symbol] !== undefined && prevMap[m.symbol] !== m.price) {
              changes.add(m.symbol);
            }
          });
          if (changes.size > 0) {
            setChangedSymbols(changes);
            setTimeout(() => setChangedSymbols(new Set()), 2000);
          }
        }
        prevMarketsRef.current = newData;
        setForeignMarkets(newData);
        setLastRefresh(new Date());
      }
    } catch (e) {
      logger.error('Error fetching foreign-markets:', e);
    } finally {
      setLoading(prev => ({ ...prev, markets: false }));
    }
  }, []);

  // Initial load
  useEffect(() => {
    fetchData('world-events', setWorldEvents, 'world');
    fetchMarketsWithDiff();
    fetchData('gov-filings', setGovFilings, 'congress');
  }, [fetchData, fetchMarketsWithDiff]);

  // Auto-refresh timer
  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(() => {
      if (activeTab === 'markets') {
        fetchMarketsWithDiff();
      } else if (activeTab === 'world') {
        fetchData('world-events', setWorldEvents, 'world');
        setLastRefresh(new Date());
      } else {
        fetchData('gov-filings', setGovFilings, 'congress');
        setLastRefresh(new Date());
      }
    }, AUTO_REFRESH_MS);
    return () => clearInterval(interval);
  }, [autoRefresh, activeTab, fetchData, fetchMarketsWithDiff]);

  const refresh = () => {
    if (activeTab === 'world') { fetchData('world-events', setWorldEvents, 'world'); setLastRefresh(new Date()); }
    if (activeTab === 'markets') fetchMarketsWithDiff();
    if (activeTab === 'congress') { fetchData('gov-filings', setGovFilings, 'congress'); setLastRefresh(new Date()); }
  };

  return (
    <div className="space-y-6" data-testid="macro-dashboard">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-gradient-to-br from-[#3DE8D9] to-violet-600 rounded-xl flex items-center justify-center">
            <Globe className="w-6 h-6 text-white" />
          </div>
          <div>
            <h2 className="text-white text-xl sm:text-2xl font-bold" style={{fontFamily: 'Manrope, sans-serif'}}>Macro Intelligence</h2>
            <InfoTooltip id="macro-dashboard" />
            <p className="text-slate-300 text-xs sm:text-sm">Real-time world events, foreign markets & government activity</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <Button onClick={refresh} disabled={loading[activeTab]} variant="outline" className="bg-[#1E293B] border-slate-600 text-white hover:bg-slate-700" data-testid="macro-refresh-btn">
            <RefreshCw className={`w-4 h-4 mr-2 ${loading[activeTab] ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
          <button
            onClick={() => setAutoRefresh(prev => !prev)}
            className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium transition-all border ${
              autoRefresh
                ? 'bg-lime-700 border-emerald-700/50 text-lime-400'
                : 'bg-slate-800 border-slate-400/30 text-slate-400'
            }`}
            data-testid="auto-refresh-toggle"
          >
            <Radio className={`w-3 h-3 ${autoRefresh ? 'animate-pulse' : ''}`} />
            {autoRefresh ? 'LIVE' : 'PAUSED'}
          </button>
        </div>
      </div>

      {/* Tab Bar */}
      <div className="flex gap-1 bg-slate-800/60 p-1 rounded-xl border border-slate-400/25 overflow-x-auto" data-testid="macro-tabs">
        {TABS.map(tab => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              data-testid={`macro-tab-${tab.id}`}
              className={`flex-1 flex items-center justify-center gap-1.5 py-2.5 px-2 sm:px-4 rounded-lg text-xs sm:text-sm font-medium transition-all whitespace-nowrap min-w-0 ${
                isActive
                  ? 'bg-[#3DE8D9] text-white shadow-lg shadow-blue-500/20'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700/50'
              }`}
            >
              <Icon className="w-4 h-4" />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Tab Content */}
      {activeTab === 'world' && <WorldEventsTab data={worldEvents} loading={loading.world} />}
      {activeTab === 'markets' && <ForeignMarketsTab data={foreignMarkets} loading={loading.markets} changedSymbols={changedSymbols} />}
      {activeTab === 'congress' && <CongressTab data={govFilings} loading={loading.congress} isPro={isPro} onSubscribe={onSubscribe} />}
      {activeTab === 'fred' && <FredEconomyTab />}

      {lastRefresh && (
        <div className="flex items-center justify-center gap-2 text-slate-400 text-[10px]">
          <Clock className="w-3 h-3" />
          Last refreshed: {lastRefresh.toLocaleTimeString()}
          {autoRefresh && <span className="text-emerald-600">· Auto-refreshing every 30s</span>}
        </div>
      )}
    </div>
  );
};

export default MacroDashboard;
