import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Globe, BarChart3, Landmark, RefreshCw, AlertTriangle, TrendingUp, TrendingDown, ChevronRight, Clock, Zap, Shield, Radio } from 'lucide-react';
import { Card } from './ui/card';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import ProBlurWall from './ProBlurWall';
import { useAuth } from '../contexts/AuthContext';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

const AUTO_REFRESH_MS = 30000; // 30 seconds

const TABS = [
  { id: 'world', label: 'World Events', icon: Globe },
  { id: 'markets', label: 'Foreign Markets', icon: BarChart3 },
  { id: 'congress', label: 'Congress Trades', icon: Landmark },
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
      console.error(`Error fetching ${endpoint}:`, e);
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
      console.error('Error fetching foreign-markets:', e);
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
          <div className="w-10 h-10 bg-gradient-to-br from-[#0052FF] to-violet-600 rounded-xl flex items-center justify-center">
            <Globe className="w-6 h-6 text-white" />
          </div>
          <div>
            <h2 className="text-white text-xl sm:text-2xl font-bold" style={{fontFamily: 'Manrope, sans-serif'}}>Macro Intelligence</h2>
            <p className="text-slate-400 text-xs sm:text-sm">Real-time world events, foreign markets & government activity</p>
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
                ? 'bg-emerald-900/30 border-emerald-700/50 text-emerald-400'
                : 'bg-slate-800 border-slate-700 text-slate-500'
            }`}
            data-testid="auto-refresh-toggle"
          >
            <Radio className={`w-3 h-3 ${autoRefresh ? 'animate-pulse' : ''}`} />
            {autoRefresh ? 'LIVE' : 'PAUSED'}
          </button>
        </div>
      </div>

      {/* Tab Bar */}
      <div className="flex gap-1 bg-slate-800/60 p-1 rounded-xl border border-slate-700/50" data-testid="macro-tabs">
        {TABS.map(tab => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              data-testid={`macro-tab-${tab.id}`}
              className={`flex-1 flex items-center justify-center gap-2 py-2.5 px-4 rounded-lg text-sm font-medium transition-all ${
                isActive
                  ? 'bg-[#0052FF] text-white shadow-lg shadow-blue-500/20'
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

      {/* Last refresh indicator */}
      {lastRefresh && (
        <div className="flex items-center justify-center gap-2 text-slate-600 text-[10px]">
          <Clock className="w-3 h-3" />
          Last refreshed: {lastRefresh.toLocaleTimeString()}
          {autoRefresh && <span className="text-emerald-600">· Auto-refreshing every 30s</span>}
        </div>
      )}
    </div>
  );
};

/* ── World Events Tab ── */
const WorldEventsTab = ({ data, loading }) => {
  if (loading || !data) return <LoadingState text="Scanning world events..." />;

  const { high_impact_events = [], all_events = [], affected_sectors = [] } = data;

  return (
    <div className="space-y-5" data-testid="world-events-tab">
      {/* Stats Bar */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 sm:gap-4">
        <StatCard icon={<Zap className="w-4 h-4 text-amber-400" />} label="Total Events" value={data.total_events || 0} />
        <StatCard icon={<AlertTriangle className="w-4 h-4 text-red-400" />} label="High Impact" value={data.high_impact_count || 0} accent="red" />
        <StatCard icon={<Shield className="w-4 h-4 text-blue-400" />} label="Sectors Affected" value={affected_sectors.length} accent="blue" />
      </div>

      {/* Affected Sectors Heat Strip */}
      {affected_sectors.length > 0 && (
        <Card className="bg-slate-800/50 border-slate-700/40 p-4 rounded-xl">
          <h3 className="text-white text-sm font-semibold mb-3">Sector Impact Map</h3>
          <div className="flex flex-wrap gap-2">
            {affected_sectors.map((s) => (
              <div key={s.sector} className="flex items-center gap-2 bg-slate-900/60 border border-slate-700/50 rounded-lg px-3 py-2" data-testid={`sector-${s.sector}`}>
                <div className={`w-2 h-2 rounded-full ${s.avg_impact >= 75 ? 'bg-red-500' : s.avg_impact >= 50 ? 'bg-amber-500' : 'bg-emerald-500'}`} />
                <span className="text-white text-xs font-medium">{s.sector}</span>
                <span className={`text-xs font-bold ${s.avg_impact >= 75 ? 'text-red-400' : s.avg_impact >= 50 ? 'text-amber-400' : 'text-emerald-400'}`}>{s.avg_impact}</span>
                <div className="flex gap-1 ml-1">
                  {(s.tickers || []).slice(0, 3).map(t => (
                    <span key={t} className="text-[10px] text-slate-400 bg-slate-800 px-1.5 py-0.5 rounded">{t}</span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* High Impact Events */}
      {high_impact_events.length > 0 && (
        <div>
          <h3 className="text-red-400 text-sm font-semibold mb-3 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" /> High Impact Events
          </h3>
          <div className="space-y-2">
            {high_impact_events.map((e, i) => (
              <EventCard key={e.title || i} event={e} isHighImpact />
            ))}
          </div>
        </div>
      )}

      {/* All Events */}
      <div>
        <h3 className="text-slate-300 text-sm font-semibold mb-3">All Events ({all_events.length})</h3>
        <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1 custom-scrollbar">
          {all_events.map((e, i) => (
            <EventCard key={e.title || i} event={e} />
          ))}
        </div>
      </div>
    </div>
  );
};

const EventCard = ({ event, isHighImpact }) => (
  <Card className={`p-3 rounded-xl border transition-all hover:border-slate-600 ${
    isHighImpact ? 'bg-red-950/20 border-red-900/40' : 'bg-slate-800/40 border-slate-700/30'
  }`}>
    <div className="flex items-start justify-between gap-3">
      <div className="flex-1 min-w-0">
        <p className="text-white text-sm font-medium leading-snug truncate">{event.title}</p>
        <div className="flex items-center gap-2 mt-1.5">
          <span className="text-[10px] text-slate-500 bg-slate-800 px-2 py-0.5 rounded-full">{event.source}</span>
          {event.published && <span className="text-[10px] text-slate-500">{event.published}</span>}
        </div>
      </div>
      {event.affected_sectors?.length > 0 && (
        <div className="flex gap-1 flex-shrink-0">
          {event.affected_sectors.slice(0, 2).map((s) => (
            <Badge key={s.sector} variant="outline" className={`text-[10px] border-slate-700 ${
              s.impact_score >= 50 ? 'text-amber-400 border-amber-800/50' : 'text-slate-400'
            }`}>{s.sector}</Badge>
          ))}
        </div>
      )}
    </div>
  </Card>
);

/* ── Foreign Markets Tab ── */
const ForeignMarketsTab = ({ data, loading, changedSymbols = new Set() }) => {
  if (loading || !data) return <LoadingState text="Fetching global markets..." />;

  const regions = [
    { key: 'asia', label: 'Asia-Pacific', emoji: '🌏' },
    { key: 'europe', label: 'Europe', emoji: '🌍' },
    { key: 'americas', label: 'Americas', emoji: '🌎' },
  ];

  return (
    <div className="space-y-5" data-testid="foreign-markets-tab">
      {/* Correlation Signals Banner */}
      {data.correlation_signals?.length > 0 && (
        <Card className="bg-gradient-to-r from-amber-950/30 to-orange-950/20 border-amber-800/40 p-4 rounded-xl">
          <h3 className="text-amber-400 text-xs font-bold uppercase tracking-wider mb-2 flex items-center gap-2">
            <Zap className="w-3.5 h-3.5" /> Pre-Market Correlation Signals
          </h3>
          <div className="space-y-1">
            {data.correlation_signals.slice(0, 4).map((sig, i) => (
              <div key={sig.signal || i} className="flex items-center gap-2 text-sm">
                {sig.change_percent > 0
                  ? <TrendingUp className="w-3.5 h-3.5 text-emerald-400 flex-shrink-0" />
                  : <TrendingDown className="w-3.5 h-3.5 text-red-400 flex-shrink-0" />}
                <span className="text-slate-300">{sig.signal}</span>
                <Badge className={`ml-auto text-[10px] ${sig.severity === 'high' ? 'bg-red-900/40 text-red-400 border-red-800' : 'bg-amber-900/40 text-amber-400 border-amber-800'}`}>
                  {sig.severity}
                </Badge>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Regional Indices */}
      {regions.map(region => {
        const items = data[region.key] || [];
        if (!items.length) return null;
        return (
          <div key={region.key}>
            <h3 className="text-slate-300 text-sm font-semibold mb-3 flex items-center gap-2">
              <span>{region.emoji}</span> {region.label}
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
              {items.map((mkt) => (
                <MarketCard key={mkt.symbol || mkt.name} market={mkt} isPulsing={changedSymbols.has(mkt.symbol)} />
              ))}
            </div>
          </div>
        );
      })}

      {/* Commodities & Currencies Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {data.commodities?.length > 0 && (
          <div>
            <h3 className="text-slate-300 text-sm font-semibold mb-3">Commodities</h3>
            <div className="grid grid-cols-2 gap-3">
              {data.commodities.map((c) => <MarketCard key={c.symbol || c.name} market={c} compact isPulsing={changedSymbols.has(c.symbol)} />)}
            </div>
          </div>
        )}
        {data.currencies?.length > 0 && (
          <div>
            <h3 className="text-slate-300 text-sm font-semibold mb-3">Currencies</h3>
            <div className="grid grid-cols-2 gap-3">
              {data.currencies.map((c) => <MarketCard key={c.symbol || c.name} market={c} compact isPulsing={changedSymbols.has(c.symbol)} />)}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

const MarketCard = ({ market, compact, isPulsing }) => {
  const isUp = market.change_percent >= 0;
  const absPct = Math.abs(market.change_percent || 0).toFixed(2);
  const isHot = Math.abs(market.change_percent || 0) >= 2;

  return (
    <Card className={`p-3 rounded-xl border transition-all hover:border-slate-600 ${
      isPulsing ? 'ring-2 ring-[#0052FF]/50 animate-pulse' :
      isHot
        ? isUp ? 'bg-emerald-950/15 border-emerald-800/30' : 'bg-red-950/15 border-red-800/30'
        : 'bg-slate-800/40 border-slate-700/30'
    }`} data-testid={`market-card-${market.symbol}`}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-white text-xs font-semibold truncate">{market.name}</span>
        <span className={`text-[10px] px-1.5 py-0.5 rounded ${
          market.market_state === 'REGULAR' ? 'bg-emerald-900/40 text-emerald-400' :
          market.market_state === 'PRE' ? 'bg-amber-900/40 text-amber-400' :
          'bg-slate-700 text-slate-400'
        }`}>{market.market_state === 'REGULAR' ? 'OPEN' : market.market_state || 'CLOSED'}</span>
      </div>
      <div className="flex items-end justify-between">
        <span className="text-white text-lg font-bold tabular-nums">
          {market.price ? (market.region === 'Currency' ? market.price.toFixed(4) : market.price.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})) : 'N/A'}
        </span>
        <div className={`flex items-center gap-1 text-xs font-semibold ${isUp ? 'text-emerald-400' : 'text-red-400'}`}>
          {isUp ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
          {isUp ? '+' : '-'}{absPct}%
        </div>
      </div>
      {!compact && (
        <div className="mt-2 w-full bg-slate-700/40 rounded-full h-1">
          <div className={`h-1 rounded-full ${isUp ? 'bg-emerald-500' : 'bg-red-500'}`}
            style={{ width: `${Math.min(Math.abs(market.change_percent || 0) * 10, 100)}%` }} />
        </div>
      )}
    </Card>
  );
};

/* ── Congressional Trades Tab ── */
const CongressTab = ({ data, loading, isPro, onSubscribe }) => {
  if (loading || !data) return <LoadingState text="Fetching government filings..." />;

  const { congressional_trades = [], fed_announcements = [], insider_trades = [] } = data;

  return (
    <div className="space-y-5" data-testid="congress-tab">
      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 sm:gap-4">
        <StatCard icon={<Landmark className="w-4 h-4 text-violet-400" />} label="Congressional Trades" value={data.congressional_count || 0} accent="violet" />
        <StatCard icon={<Shield className="w-4 h-4 text-blue-400" />} label="Fed Announcements" value={data.fed_count || 0} accent="blue" />
        <StatCard icon={<BarChart3 className="w-4 h-4 text-emerald-400" />} label="SEC Insider Filings" value={data.insider_count || 0} accent="emerald" />
      </div>

      {/* Congressional Trades Table */}
      {congressional_trades.length > 0 && (
        <Card className="bg-slate-800/50 border-slate-700/40 rounded-xl overflow-hidden">
          <div className="p-4 border-b border-slate-700/40">
            <h3 className="text-white text-sm font-semibold flex items-center gap-2">
              <Landmark className="w-4 h-4 text-violet-400" /> Recent Congressional Stock Trades
            </h3>
          </div>
          <ProBlurWall freeRowCount={3} onSubscribe={onSubscribe} label="Congressional Trades">
          <div className="overflow-x-auto -mx-4 px-4 sm:mx-0 sm:px-0 overflow-hidden" style={{maxHeight: isPro ? 'none' : '320px'}}>
            <table className="w-full text-sm" data-testid="congress-trades-table">
              <thead>
                <tr className="border-b border-slate-700/40">
                  <th className="text-left text-slate-400 text-xs font-medium px-4 py-2.5">Representative</th>
                  <th className="text-left text-slate-400 text-xs font-medium px-4 py-2.5">Party</th>
                  <th className="text-left text-slate-400 text-xs font-medium px-4 py-2.5">Ticker</th>
                  <th className="text-left text-slate-400 text-xs font-medium px-4 py-2.5">Type</th>
                  <th className="text-left text-slate-400 text-xs font-medium px-4 py-2.5">Amount</th>
                  <th className="text-left text-slate-400 text-xs font-medium px-4 py-2.5">Date</th>
                </tr>
              </thead>
              <tbody>
                {congressional_trades.map((trade, i) => (
                  <tr key={trade.ticker ? `${trade.representative}-${trade.ticker}-${i}` : i} className="border-b border-slate-800/40 hover:bg-slate-700/20 transition-colors">
                    <td className="px-4 py-2.5 text-white font-medium">{trade.representative || 'N/A'}</td>
                    <td className="px-4 py-2.5">
                      <span className={`inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full ${
                        trade.party === 'R' ? 'bg-red-900/30 text-red-400' :
                        trade.party === 'D' ? 'bg-blue-900/30 text-blue-400' :
                        'bg-slate-700 text-slate-400'
                      }`}>
                        {trade.party === 'R' ? 'R' : trade.party === 'D' ? 'D' : trade.party || '—'}
                        {trade.chamber ? ` · ${trade.chamber}` : ''}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="text-[#0052FF] font-bold">{trade.ticker || '—'}</span>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={`text-xs font-semibold uppercase ${
                        trade.type?.toLowerCase() === 'buy' || trade.type?.toLowerCase() === 'purchase'
                          ? 'text-emerald-400' : 'text-red-400'
                      }`}>{trade.type || '—'}</span>
                    </td>
                    <td className="px-4 py-2.5 text-slate-300 text-xs">{trade.amount || '—'}</td>
                    <td className="px-4 py-2.5 text-slate-500 text-xs">{trade.transaction_date || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </ProBlurWall>
        </Card>
      )}

      {/* Fed Announcements */}
      {fed_announcements.length > 0 && (
        <div>
          <h3 className="text-slate-300 text-sm font-semibold mb-3 flex items-center gap-2">
            <Shield className="w-4 h-4 text-blue-400" /> Federal Reserve Announcements
          </h3>
          <div className="space-y-2">
            {fed_announcements.map((ann) => (
              <Card key={ann.title || ann.date} className="bg-slate-800/40 border-slate-700/30 rounded-xl p-3 hover:border-slate-600 transition-all">
                <p className="text-white text-sm font-medium leading-snug">{ann.title}</p>
                <div className="flex items-center gap-2 mt-2">
                  <Clock className="w-3 h-3 text-slate-500" />
                  <span className="text-[10px] text-slate-500">{ann.date}</span>
                  {ann.url && (
                    <a href={ann.url} target="_blank" rel="noopener noreferrer" className="ml-auto text-[10px] text-[#0052FF] hover:underline flex items-center gap-1">
                      View <ChevronRight className="w-3 h-3" />
                    </a>
                  )}
                </div>
              </Card>
            ))}
          </div>
        </div>
      )}

      {/* SEC Insider Trades */}
      {insider_trades.length > 0 && (
        <div>
          <h3 className="text-slate-300 text-sm font-semibold mb-3 flex items-center gap-2">
            <BarChart3 className="w-4 h-4 text-emerald-400" /> SEC Insider Filings
          </h3>
          <div className="space-y-2 max-h-[300px] overflow-y-auto pr-1 custom-scrollbar">
            {insider_trades.map((t, i) => (
              <Card key={`insider-${t.company || t.ticker || i}-${i}`} className="bg-slate-800/40 border-slate-700/30 rounded-xl p-3">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-white text-sm font-medium">{t.company || t.ticker || 'Unknown'}</p>
                    <p className="text-slate-500 text-xs mt-0.5">{t.description}</p>
                  </div>
                  <span className="text-slate-500 text-xs">{t.filed_date}</span>
                </div>
              </Card>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

/* ── Shared helpers ── */
const StatCard = ({ icon, label, value, accent }) => {
  const colors = {
    red: 'from-red-950/30 to-red-900/10 border-red-800/30',
    blue: 'from-blue-950/30 to-blue-900/10 border-blue-800/30',
    violet: 'from-violet-950/30 to-violet-900/10 border-violet-800/30',
    emerald: 'from-emerald-950/30 to-emerald-900/10 border-emerald-800/30',
  };
  return (
    <Card className={`bg-gradient-to-br ${colors[accent] || 'from-slate-800/50 to-slate-800/30 border-slate-700/40'} p-4 rounded-xl`}>
      <div className="flex items-center gap-2 mb-1">{icon}<span className="text-slate-400 text-xs">{label}</span></div>
      <p className="text-white text-2xl font-bold tabular-nums">{value}</p>
    </Card>
  );
};

const LoadingState = ({ text }) => (
  <div className="bg-slate-800/30 border border-slate-700/40 rounded-xl p-12 flex items-center justify-center">
    <RefreshCw className="w-5 h-5 text-blue-400 animate-spin mr-3" />
    <span className="text-slate-400 text-sm">{text}</span>
  </div>
);

export default MacroDashboard;
