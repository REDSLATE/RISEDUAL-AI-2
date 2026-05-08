import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, ArrowUpRight, Layers, RefreshCw, Trophy, Crown } from 'lucide-react';
import { Button } from '../ui/button';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

// ── tone palette — different from autopsy so admins don't confuse them ──
const ENGINE_COLOR = {
  live: 'border-[#3DE8D9]/40 bg-[#3DE8D9]/5',
  candidate_v2: 'border-fuchsia-400/30 bg-fuchsia-500/5',
};
const ENGINE_BADGE = {
  live: 'bg-[#3DE8D9]/15 text-[#3DE8D9] border-[#3DE8D9]/30',
  candidate_v2: 'bg-fuchsia-500/15 text-fuchsia-300 border-fuchsia-400/30',
};
const fmtPct = (n) => (n == null ? '—' : `${(Number(n) * 100).toFixed(1)}%`);
const fmtNum = (n) => (n == null ? '—' : Number(n).toLocaleString());

const EngineCard = ({ engine, isLive, onPromote, promoting }) => {
  const palette = ENGINE_COLOR[engine.name] || 'border-slate-700/50 bg-slate-800/40';
  const badge = ENGINE_BADGE[engine.name] || 'bg-slate-700/40 text-slate-300 border-slate-600/40';
  const stats = engine.stats || {};
  return (
    <div className={`border ${palette} rounded-lg p-4`} data-testid={`engine-card-${engine.name}`}>
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <div className="flex items-center gap-2 mb-0.5">
            <h3 className="text-white text-sm font-semibold" data-testid={`engine-name-${engine.name}`}>
              {engine.name}
            </h3>
            {isLive && (
              <span className="inline-flex items-center gap-1 text-[10px] bg-amber-500/15 text-amber-300 border border-amber-400/30 rounded px-1.5 py-0.5">
                <Crown className="w-3 h-3" /> LIVE
              </span>
            )}
            <span className={`text-[10px] border rounded px-1.5 py-0.5 ${badge}`}>
              {engine.schema_name}
            </span>
          </div>
          <p className="text-slate-400 text-[11px]">
            {engine.schema_dimensions?.length} dims · {engine.confidence_buckets?.length} conf bins
          </p>
        </div>
        {!isLive && (
          <Button
            onClick={() => onPromote(engine.name)}
            disabled={promoting === engine.name}
            size="sm"
            variant="outline"
            className="border-amber-400/40 text-amber-300 hover:bg-amber-500/10 text-xs"
            data-testid={`promote-btn-${engine.name}`}
          >
            <ArrowUpRight className="w-3 h-3 mr-1" />
            Promote
          </Button>
        )}
      </div>

      <div className="grid grid-cols-3 gap-2 text-[11px]">
        <div className="bg-slate-900/40 rounded p-2">
          <div className="text-slate-400">Total</div>
          <div className="text-white font-mono text-base" data-testid={`engine-total-${engine.name}`}>
            {fmtNum(stats.total_resolved)}
          </div>
        </div>
        <div className="bg-slate-900/40 rounded p-2">
          <div className="text-slate-400">Win-rate</div>
          <div className="text-white font-mono text-base" data-testid={`engine-winrate-${engine.name}`}>
            {fmtPct(stats.win_rate)}
          </div>
          <div className="text-slate-500 text-[10px]">over {fmtNum(stats.denominator)}</div>
        </div>
        <div className="bg-slate-900/40 rounded p-2">
          <div className="text-slate-400">W / L / Flat</div>
          <div className="text-white font-mono text-sm">
            {fmtNum(stats.wins)} / {fmtNum(stats.losses)} / {fmtNum(stats.flats || 0)}
          </div>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-1">
        {(engine.schema_dimensions || []).map((d) => (
          <span key={d} className="text-[10px] bg-slate-700/40 text-slate-300 border border-slate-600/40 rounded px-1.5 py-0.5">
            {d}
          </span>
        ))}
      </div>
    </div>
  );
};

const ComparisonTable = ({ comparison, onDimensionChange }) => {
  if (!comparison) return null;
  const { engines = [], dimension, min_total } = comparison;
  return (
    <div className="bg-slate-800/40 border border-slate-700/50 rounded-lg p-4" data-testid="engines-comparison">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <Trophy className="w-4 h-4 text-[#3DE8D9]" />
          <h3 className="text-white text-sm font-semibold">Bucket-Lift Comparison</h3>
          <span className="text-slate-400 text-[11px]">min {min_total} per bucket</span>
        </div>
        <label className="text-slate-400 text-xs flex items-center gap-1.5">
          Dimension
          <select
            value={dimension}
            onChange={(e) => onDimensionChange(e.target.value)}
            className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-white text-xs"
            data-testid="comparison-dimension-select"
          >
            {['agent', 'regime', 'asset_type', 'confidence_bucket', 'direction_family', 'confidence_x_agent'].map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-slate-400 border-b border-slate-700/50">
            <tr>
              <th className="text-left py-1.5 pr-3">Engine</th>
              <th className="text-right py-1.5 pr-3">Buckets</th>
              <th className="text-right py-1.5 pr-3">Lift</th>
              <th className="text-left py-1.5 pr-3">Top bucket (best)</th>
              <th className="text-left py-1.5">Bottom bucket (worst)</th>
            </tr>
          </thead>
          <tbody>
            {engines.map((e) => (
              <tr key={e.name} className="border-b border-slate-800/50" data-testid={`comparison-row-${e.name}`}>
                <td className="py-1.5 pr-3">
                  <span className="text-white font-semibold">{e.name}</span>
                  {e.is_live && <span className="ml-1 text-[10px] text-amber-300">·LIVE</span>}
                </td>
                <td className="py-1.5 pr-3 text-right text-slate-300 font-mono">{e.buckets_meeting_min}</td>
                <td className="py-1.5 pr-3 text-right text-white font-mono font-bold">{fmtPct(e.lift)}</td>
                <td className="py-1.5 pr-3 text-slate-300">
                  {e.top_bucket ? (
                    <span><span className="text-lime-400">{e.top_bucket.value}</span> · {fmtPct(e.top_bucket.win_rate)} ({e.top_bucket.total})</span>
                  ) : '—'}
                </td>
                <td className="py-1.5 text-slate-300">
                  {e.bottom_bucket ? (
                    <span><span className="text-orange-400">{e.bottom_bucket.value}</span> · {fmtPct(e.bottom_bucket.win_rate)} ({e.bottom_bucket.total})</span>
                  ) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-slate-500 text-[10px] mt-2">
        Lift = max win-rate − min win-rate across buckets in this dimension. Higher = candidate's bucketing separates winners from losers more cleanly.
      </p>
    </div>
  );
};

const EngineRegistryPanel = () => {
  const [data, setData] = useState(null);
  const [comparison, setComparison] = useState(null);
  const [dimension, setDimension] = useState('agent');
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [promoting, setPromoting] = useState(null);

  const fetchAll = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const [enginesRes, cmpRes] = await Promise.all([
        authFetch(`${API}/ai-core/engines`),
        authFetch(`${API}/ai-core/engines/compare?dimension=${dimension}&min_total=30`),
      ]);
      if (!enginesRes.ok) throw new Error(`engines HTTP ${enginesRes.status}`);
      if (!cmpRes.ok) throw new Error(`compare HTTP ${cmpRes.status}`);
      setData(await enginesRes.json());
      setComparison(await cmpRes.json());
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, [dimension]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const handlePromote = useCallback(async (name) => {
    if (!window.confirm(`Promote ${name} to LIVE? Previous live engine becomes a candidate.`)) return;
    setPromoting(name);
    try {
      const res = await authFetch(`${API}/ai-core/engines/${name}/promote`, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await fetchAll();
    } catch (e) {
      window.alert(`Promotion failed: ${e}`);
    } finally {
      setPromoting(null);
    }
  }, [fetchAll]);

  const live = data?.live;

  if (loading && !data) {
    return <div className="p-6 text-slate-400 text-sm" data-testid="engines-loading">Loading engines…</div>;
  }
  if (err) {
    return (
      <div className="p-6 text-red-400 text-sm" data-testid="engines-error">
        Failed to load: {err}
        <Button onClick={fetchAll} size="sm" variant="outline" className="ml-3">Retry</Button>
      </div>
    );
  }

  return (
    <div className="space-y-4" data-testid="engine-registry-panel">
      <div className="bg-gradient-to-r from-[#3DE8D9]/8 to-fuchsia-500/8 border border-[#3DE8D9]/25 rounded-lg p-4">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-start gap-3">
            <Activity className="w-5 h-5 text-[#3DE8D9] mt-0.5" />
            <div>
              <h2 className="text-white text-base font-bold">AI Core Engine Registry</h2>
              <p className="text-slate-300 text-xs mt-0.5">
                Parallel learning engines observing the same firehose · live: <span className="text-amber-300 font-semibold">{live}</span> · {data?.engines?.length} engines registered
              </p>
              <p className="text-slate-500 text-[10px] mt-0.5">
                Promotion is a registry-level label flip · does NOT cross the dual-stack firewall · Council still reads prediction-tracker, not AI Core.
              </p>
            </div>
          </div>
          <Button
            onClick={fetchAll}
            disabled={loading}
            size="sm"
            variant="outline"
            className="border-[#3DE8D9]/40 text-[#3DE8D9] hover:bg-[#3DE8D9]/10"
            data-testid="engines-refresh-btn"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            <span className="ml-1.5">Refresh</span>
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {(data?.engines || []).map((e) => (
          <EngineCard
            key={e.name}
            engine={e}
            isLive={e.name === live}
            onPromote={handlePromote}
            promoting={promoting}
          />
        ))}
      </div>

      <ComparisonTable comparison={comparison} onDimensionChange={setDimension} />
    </div>
  );
};

export default EngineRegistryPanel;
