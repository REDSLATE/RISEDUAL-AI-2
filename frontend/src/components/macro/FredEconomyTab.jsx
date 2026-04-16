import React, { useState, useEffect, useCallback } from 'react';
import { TrendingUp, TrendingDown, Minus, Loader2, Search, AlertTriangle, History } from 'lucide-react';
import { Card } from '../ui/card';
import { Input } from '../ui/input';
import { Badge } from '../ui/badge';
import { getApiBase } from '../../utils/apiBase';

const API = getApiBase();

const CAT_ORDER = ['Rates', 'Growth', 'Inflation', 'Employment', 'Housing', 'Consumer', 'Trade'];

const CAT_COLORS = {
  Growth: '#3DE8D9',
  Inflation: '#F59E0B',
  Employment: '#8B5CF6',
  Rates: '#3B82F6',
  Housing: '#10B981',
  Consumer: '#EC4899',
  Trade: '#F97316',
};

const fmtVal = (v, unit) => {
  if (v == null) return 'N/A';
  const n = Number(v);
  if (unit === '$T') return `$${n.toFixed(2)}T`;
  if (unit === '$B') return `$${n.toFixed(1)}B`;
  if (unit === '%') return `${n.toFixed(2)}%`;
  if (unit === 'K') return `${n.toLocaleString()}K`;
  return n.toLocaleString(undefined, { maximumFractionDigits: 1 });
};

const Sparkline = ({ data, color }) => {
  if (!data || data.length < 2) return null;
  const vals = data.map(d => d.value);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const range = max - min || 1;
  const w = 80;
  const h = 24;
  const points = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * w;
    const y = h - ((v - min) / range) * h;
    return `${x},${y}`;
  }).join(' ');

  return (
    <svg width={w} height={h} className="shrink-0">
      <polyline fill="none" stroke={color} strokeWidth="1.5" points={points} strokeLinejoin="round" />
    </svg>
  );
};

const IndicatorCard = ({ ind }) => {
  const color = CAT_COLORS[ind.category] || '#3DE8D9';
  const chg = ind.change_pct;
  const isUp = chg > 0;
  const isFlat = chg === 0 || chg == null;

  return (
    <div
      data-testid={`fred-indicator-${ind.id}`}
      className="bg-slate-800/70 rounded-lg p-3.5 border border-slate-700/40 hover:border-slate-600/60 transition-colors"
    >
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex-1 min-w-0">
          <p className="text-slate-400 text-[10px] uppercase tracking-wider truncate">{ind.name}</p>
          <div className="flex items-baseline gap-2 mt-1">
            <span className="text-white font-bold text-lg leading-none">{fmtVal(ind.value, ind.unit)}</span>
            {!isFlat && (
              <span className={`text-xs flex items-center gap-0.5 ${isUp ? 'text-lime-400' : 'text-orange-400'}`}>
                {isUp ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
                {Math.abs(chg).toFixed(2)}%
              </span>
            )}
            {isFlat && <Minus className="w-3 h-3 text-slate-500" />}
          </div>
        </div>
        <Sparkline data={ind.history} color={color} />
      </div>
      <div className="flex items-center justify-between">
        <span className="text-[9px] text-slate-500">{ind.date}</span>
        <span className="text-[9px] px-1.5 py-0.5 rounded-full" style={{ backgroundColor: `${color}15`, color }}>{ind.id}</span>
      </div>
    </div>
  );
};

const FredSeriesDetail = ({ seriesId, onClose }) => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(`${API}/api/fred/series/${seriesId}?limit=120`);
        if (res.ok) setData(await res.json());
      } catch (e) { /* ignore */ }
      setLoading(false);
    };
    load();
  }, [seriesId]);

  if (loading) return <div className="text-center py-6"><Loader2 className="w-5 h-5 animate-spin text-[#3DE8D9] mx-auto" /></div>;
  if (!data) return <div className="text-slate-400 text-sm text-center py-6">No data</div>;

  const obs = data.observations || [];
  const vals = obs.map(o => o.value);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const range = max - min || 1;

  return (
    <Card className="bg-slate-900/80 border-slate-700/40 p-4 mt-3">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h4 className="text-white font-medium text-sm">{data.title}</h4>
          <p className="text-slate-500 text-[10px]">{data.frequency} · {data.units} · Updated {data.last_updated?.slice(0, 10)}</p>
        </div>
        <button onClick={onClose} className="text-slate-400 hover:text-white text-xs px-2 py-1 rounded bg-slate-800">Close</button>
      </div>
      {obs.length > 0 && (
        <svg width="100%" height="120" viewBox={`0 0 ${obs.length} 120`} preserveAspectRatio="none" className="rounded">
          <defs>
            <linearGradient id="fredGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#3DE8D9" stopOpacity="0.3" />
              <stop offset="100%" stopColor="#3DE8D9" stopOpacity="0" />
            </linearGradient>
          </defs>
          <path
            d={`M0,120 ${obs.map((o, i) => `L${i},${120 - ((o.value - min) / range) * 110}`).join(' ')} L${obs.length - 1},120 Z`}
            fill="url(#fredGrad)"
          />
          <polyline
            fill="none"
            stroke="#3DE8D9"
            strokeWidth="1.5"
            points={obs.map((o, i) => `${i},${120 - ((o.value - min) / range) * 110}`).join(' ')}
          />
        </svg>
      )}
      <div className="flex justify-between text-[9px] text-slate-500 mt-1 px-1">
        <span>{obs[0]?.date}</span>
        <span>{obs[obs.length - 1]?.date}</span>
      </div>
    </Card>
  );
};

const FredSearchResults = ({ results }) => {
  if (!results?.length) return <p className="text-slate-500 text-xs text-center py-4">No results</p>;
  return (
    <div className="space-y-1.5 max-h-60 overflow-y-auto">
      {results.map(s => (
        <div key={s.id} className="flex items-center justify-between bg-slate-800/50 rounded-lg px-3 py-2 text-xs hover:bg-slate-700/50 transition-colors">
          <div className="min-w-0 flex-1">
            <span className="text-[#3DE8D9] font-mono mr-2">{s.id}</span>
            <span className="text-slate-300">{s.title?.slice(0, 60)}</span>
          </div>
          <span className="text-slate-500 text-[10px] shrink-0 ml-2">{s.frequency}</span>
        </div>
      ))}
    </div>
  );
};

const VintageCompare = ({ seriesId, onClose }) => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      // Compare 3 vintage points: 6 months ago, 3 months ago, 1 month ago
      const now = new Date();
      const dates = [6, 3, 1].map(m => {
        const d = new Date(now);
        d.setMonth(d.getMonth() - m);
        return d.toISOString().slice(0, 10);
      });
      try {
        const res = await fetch(`${API}/api/fred/vintage/${seriesId}?dates=${dates.join(',')}`);
        if (res.ok) setData(await res.json());
      } catch (e) { /* ignore */ }
      setLoading(false);
    };
    load();
  }, [seriesId]);

  if (loading) return <div className="text-center py-6"><Loader2 className="w-5 h-5 animate-spin text-[#3DE8D9] mx-auto" /></div>;
  if (!data) return null;

  const totalRevisions = (data.vintages || []).reduce((sum, v) => sum + (v.revision_count || 0), 0);

  return (
    <Card className="bg-slate-900/80 border-slate-700/40 p-4 mt-3" data-testid={`vintage-${seriesId}`}>
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="flex items-center gap-2">
            <History className="w-4 h-4 text-amber-400" />
            <h4 className="text-white font-medium text-sm">ALFRED Vintage: {data.title}</h4>
          </div>
          <p className="text-slate-500 text-[10px] mt-0.5">{data.units} · {data.frequency} · {totalRevisions} total revisions detected</p>
        </div>
        <button onClick={onClose} className="text-slate-400 hover:text-white text-xs px-2 py-1 rounded bg-slate-800">Close</button>
      </div>

      {data.vintages?.map((v, vi) => (
        <div key={v.date} className="mb-3">
          <div className="flex items-center gap-2 mb-1.5">
            <Badge className="bg-slate-800 text-slate-300 border-slate-700 text-[10px]">
              Vintage: {v.date}
            </Badge>
            {v.revision_count > 0 && (
              <Badge className="bg-amber-500/10 text-amber-400 border-amber-500/20 text-[10px]">
                {v.revision_count} revisions
              </Badge>
            )}
          </div>
          {v.revisions?.length > 0 ? (
            <div className="space-y-1">
              {v.revisions.slice(0, 5).map(r => (
                <div key={r.observation_date} className="flex items-center justify-between bg-slate-800/50 rounded px-3 py-1.5 text-xs">
                  <span className="text-slate-400">{r.observation_date}</span>
                  <div className="flex items-center gap-3">
                    <span className="text-slate-500">was <span className="text-white">{r.original_value?.toLocaleString()}</span></span>
                    <span className="text-slate-600">-&gt;</span>
                    <span className="text-slate-500">now <span className="text-white">{r.revised_value?.toLocaleString()}</span></span>
                    <span className={`font-medium ${r.revision > 0 ? 'text-lime-400' : 'text-orange-400'}`}>
                      {r.revision_pct > 0 ? '+' : ''}{r.revision_pct}%
                    </span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-slate-600 text-xs pl-2">No revisions from this vintage</p>
          )}
        </div>
      ))}
    </Card>
  );
};

const RevisionAlerts = () => {
  const [revisions, setRevisions] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(`${API}/api/fred/revisions`);
        if (res.ok) {
          const d = await res.json();
          setRevisions(d);
        }
      } catch (e) { /* ignore */ }
      setLoading(false);
    };
    load();
  }, []);

  if (loading || !revisions?.has_revisions) return null;

  return (
    <Card className="bg-amber-500/5 border-amber-500/20 p-3" data-testid="fred-revision-alerts">
      <div className="flex items-center gap-2 mb-2">
        <AlertTriangle className="w-4 h-4 text-amber-400" />
        <span className="text-amber-400 font-medium text-sm">Data Revisions Detected</span>
        <Badge className="bg-amber-500/10 text-amber-400 border-amber-500/20 text-[10px]">{revisions.count}</Badge>
      </div>
      <div className="space-y-1">
        {revisions.revisions.map(r => (
          <div key={r.series_id} className="flex items-center justify-between text-xs bg-slate-800/40 rounded px-3 py-1.5">
            <span className="text-slate-300">{r.name}</span>
            <span className={`font-medium ${r.revision > 0 ? 'text-lime-400' : 'text-orange-400'}`}>
              {r.stored_value?.toLocaleString()} -&gt; {r.current_value?.toLocaleString()} ({r.revision > 0 ? '+' : ''}{r.revision})
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
};

export default function FredEconomyTab({ loading: parentLoading }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [selectedSeries, setSelectedSeries] = useState(null);
  const [vintageSeries, setVintageSeries] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState(null);
  const [searching, setSearching] = useState(false);

  const fetchIndicators = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/fred/indicators`);
      if (res.ok) setData(await res.json());
    } catch (e) { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { fetchIndicators(); }, [fetchIndicators]);

  const doSearch = async () => {
    if (!searchQuery.trim()) return;
    setSearching(true);
    try {
      const res = await fetch(`${API}/api/fred/search?q=${encodeURIComponent(searchQuery)}&limit=15`);
      if (res.ok) {
        const d = await res.json();
        setSearchResults(d.results);
      }
    } catch (e) { /* ignore */ }
    setSearching(false);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="w-6 h-6 animate-spin text-[#3DE8D9]" />
        <span className="ml-3 text-slate-400 text-sm">Loading FRED economic data...</span>
      </div>
    );
  }

  const categories = data?.categories || {};

  return (
    <div data-testid="fred-economy-tab" className="space-y-5">
      {/* Search bar */}
      <div className="flex gap-2">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
          <Input
            data-testid="fred-search-input"
            placeholder="Search FRED series (e.g. GDP, inflation, housing)"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && doSearch()}
            className="pl-9 bg-slate-800/60 border-slate-700 text-white placeholder:text-slate-500 h-9 text-sm"
          />
        </div>
        <button
          onClick={doSearch}
          disabled={searching}
          data-testid="fred-search-btn"
          className="px-3 h-9 rounded-lg bg-slate-800 border border-slate-700 text-slate-300 hover:text-white text-sm transition-colors"
        >
          {searching ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Search'}
        </button>
      </div>

      {searchResults && (
        <Card className="bg-slate-900/60 border-slate-700/40 p-3">
          <div className="flex items-center justify-between mb-2">
            <span className="text-slate-400 text-xs">{searchResults.length} results for "{searchQuery}"</span>
            <button onClick={() => setSearchResults(null)} className="text-slate-500 hover:text-white text-xs">Clear</button>
          </div>
          <FredSearchResults results={searchResults} />
        </Card>
      )}

      {/* Revision Alerts */}
      <RevisionAlerts />

      {/* Vintage Compare (if active) */}
      {vintageSeries && (
        <VintageCompare seriesId={vintageSeries} onClose={() => setVintageSeries(null)} />
      )}

      {/* Categories */}
      {CAT_ORDER.filter(c => categories[c]).map(cat => {
        const items = categories[cat];
        const color = CAT_COLORS[cat] || '#3DE8D9';
        return (
          <div key={cat}>
            <div className="flex items-center gap-2 mb-3">
              <div className="w-1.5 h-5 rounded-full" style={{ backgroundColor: color }} />
              <h3 className="text-white font-medium text-sm">{cat}</h3>
              <span className="text-slate-500 text-[10px]">{items.length} indicators</span>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
              {items.map(ind => (
                <div key={ind.id} className="relative group">
                  <div onClick={() => setSelectedSeries(selectedSeries === ind.id ? null : ind.id)} className="cursor-pointer">
                    <IndicatorCard ind={ind} />
                  </div>
                  <button
                    onClick={(e) => { e.stopPropagation(); setVintageSeries(vintageSeries === ind.id ? null : ind.id); }}
                    className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity bg-slate-700/80 hover:bg-amber-500/20 text-slate-400 hover:text-amber-400 rounded p-1"
                    title="ALFRED Vintage Compare"
                    data-testid={`vintage-btn-${ind.id}`}
                  >
                    <History className="w-3 h-3" />
                  </button>
                </div>
              ))}
            </div>
            {selectedSeries && items.some(i => i.id === selectedSeries) && (
              <FredSeriesDetail seriesId={selectedSeries} onClose={() => setSelectedSeries(null)} />
            )}
          </div>
        );
      })}

      <p className="text-slate-600 text-[10px] text-center">
        Data from Federal Reserve Economic Data (FRED) &amp; ALFRED, Federal Reserve Bank of St. Louis.
      </p>
    </div>
  );
}
