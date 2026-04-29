import React, { useCallback, useEffect, useState } from 'react';
import { Rewind, RefreshCw, Database, Activity } from 'lucide-react';
import { Button } from '../ui/button';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

const fmtPct = (n) => (n == null ? '—' : `${(Number(n) * 100).toFixed(1)}%`);
const fmtNum = (n) => (n == null ? '—' : Number(n).toLocaleString());
const fmtDelta = (n) => {
  if (n == null) return '—';
  const sign = n > 0 ? '+' : '';
  const cls = n > 0 ? 'text-lime-400' : n < 0 ? 'text-orange-400' : 'text-slate-400';
  return <span className={cls}>{sign}{(n * 100).toFixed(2)} pp</span>;
};

const WhatIfReplayPanel = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [backfilling, setBackfilling] = useState(false);
  const [dimension, setDimension] = useState('agent');
  const [since, setSince] = useState('');
  const [until, setUntil] = useState('');

  const fetchWhatIf = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const params = new URLSearchParams({ dimension, min_total: '30' });
      if (since) params.set('since', since);
      if (until) params.set('until', until);
      const res = await authFetch(`${API}/admin/replay/whatif?${params.toString()}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, [dimension, since, until]);

  useEffect(() => { fetchWhatIf(); }, [fetchWhatIf]);

  const handleBackfill = useCallback(async () => {
    if (!window.confirm('Run backfill? Idempotent — re-runs only insert genuinely new outcomes.')) return;
    setBackfilling(true);
    try {
      const res = await authFetch(`${API}/admin/replay/backfill`, { method: 'POST' });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
      window.alert(`Backfill complete:\npaper_trades: +${body.paper_trades.inserted} (${body.paper_trades.deduped} dedup)\npredictions: +${body.predictions.inserted} (${body.predictions.deduped} dedup)`);
      await fetchWhatIf();
    } catch (e) {
      window.alert(`Backfill failed: ${e}`);
    } finally {
      setBackfilling(false);
    }
  }, [fetchWhatIf]);

  if (loading && !data) {
    return <div className="p-6 text-slate-400 text-sm" data-testid="whatif-loading">Loading replay…</div>;
  }
  if (err) {
    return (
      <div className="p-6 text-red-400 text-sm" data-testid="whatif-error">
        Failed: {err}
        <Button onClick={fetchWhatIf} size="sm" variant="outline" className="ml-3">Retry</Button>
      </div>
    );
  }
  if (!data) return null;
  const corpus = data.window?.outcomes_in_corpus || 0;

  return (
    <div className="space-y-4" data-testid="whatif-replay-panel">
      <div className="bg-gradient-to-r from-indigo-500/8 to-cyan-500/8 border border-cyan-500/25 rounded-lg p-4">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-start gap-3">
            <Rewind className="w-5 h-5 text-cyan-300 mt-0.5" />
            <div>
              <h2 className="text-white text-base font-bold">What-If Replay</h2>
              <p className="text-slate-300 text-xs mt-0.5">
                Project every engine's schema against the same fixed corpus of resolved outcomes ·
                <span className="text-cyan-300 font-semibold"> {fmtNum(corpus)}</span> outcomes in ledger
              </p>
              <p className="text-slate-500 text-[10px] mt-0.5">
                Reads from <code className="text-slate-400">prd_resolved_outcomes</code> via the firewall · projection is non-destructive (real engine state untouched).
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              onClick={handleBackfill}
              disabled={backfilling}
              size="sm"
              variant="outline"
              className="border-cyan-400/40 text-cyan-300 hover:bg-cyan-500/10"
              data-testid="whatif-backfill-btn"
            >
              <Database className={`w-3.5 h-3.5 ${backfilling ? 'animate-pulse' : ''}`} />
              <span className="ml-1.5">{backfilling ? 'Backfilling…' : 'Backfill ledger'}</span>
            </Button>
            <Button
              onClick={fetchWhatIf}
              disabled={loading}
              size="sm"
              variant="outline"
              className="border-cyan-400/40 text-cyan-300 hover:bg-cyan-500/10"
              data-testid="whatif-refresh-btn"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
              <span className="ml-1.5">Refresh</span>
            </Button>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3 text-[11px]">
          <label className="text-slate-400 flex items-center gap-1.5">
            Since
            <input
              type="datetime-local"
              value={since}
              onChange={(e) => setSince(e.target.value ? e.target.value + ':00+00:00' : '')}
              className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-white"
              data-testid="whatif-since-input"
            />
          </label>
          <label className="text-slate-400 flex items-center gap-1.5">
            Until
            <input
              type="datetime-local"
              value={until}
              onChange={(e) => setUntil(e.target.value ? e.target.value + ':00+00:00' : '')}
              className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-white"
              data-testid="whatif-until-input"
            />
          </label>
          <label className="text-slate-400 flex items-center gap-1.5">
            Dim
            <select
              value={dimension}
              onChange={(e) => setDimension(e.target.value)}
              className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-white"
              data-testid="whatif-dimension-select"
            >
              {['agent', 'regime', 'asset_type', 'confidence_bucket', 'direction_family', 'confidence_x_agent'].map((d) => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {(data.engines || []).map((e) => (
          <div
            key={e.name}
            className={`border rounded-lg p-4 ${e.is_live ? 'border-amber-400/40 bg-amber-500/5' : 'border-fuchsia-400/30 bg-fuchsia-500/5'}`}
            data-testid={`whatif-engine-${e.name}`}
          >
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <Activity className="w-4 h-4 text-cyan-300" />
                <h3 className="text-white text-sm font-semibold">{e.name}</h3>
                {e.is_live && <span className="text-[10px] bg-amber-500/15 text-amber-300 border border-amber-400/30 rounded px-1.5 py-0.5">LIVE</span>}
                <span className="text-[10px] text-slate-400">{e.schema_name}</span>
              </div>
              <div className="text-right">
                <div className="text-slate-400 text-[10px]">Win-rate</div>
                <div className="text-white text-base font-mono font-bold">{fmtPct(e.stats?.win_rate)}</div>
                {!e.is_live && data.winrate_delta_vs_live?.[e.name] != null && (
                  <div className="text-[10px]">vs live: {fmtDelta(data.winrate_delta_vs_live[e.name])}</div>
                )}
              </div>
            </div>
            <div className="grid grid-cols-3 gap-2 text-[11px] mb-3">
              <div className="bg-slate-900/40 rounded p-2">
                <div className="text-slate-400">Total</div>
                <div className="text-white font-mono">{fmtNum(e.stats?.total_resolved)}</div>
              </div>
              <div className="bg-slate-900/40 rounded p-2">
                <div className="text-slate-400">Bucket-lift</div>
                <div className="text-white font-mono">{fmtPct(e.bucket_lift)}</div>
              </div>
              <div className="bg-slate-900/40 rounded p-2">
                <div className="text-slate-400">W / L</div>
                <div className="text-white font-mono">{fmtNum(e.stats?.wins)} / {fmtNum(e.stats?.losses)}</div>
              </div>
            </div>
            <div className="text-[11px]">
              <div className="text-slate-400 mb-1">Top buckets ({dimension}):</div>
              <div className="space-y-0.5 mb-2">
                {(e.top_buckets || []).map((b) => (
                  <div key={b.value} className="flex items-center justify-between bg-slate-900/40 rounded px-2 py-0.5">
                    <span className="text-slate-300 truncate">{b.value}</span>
                    <span className="text-white font-mono">{fmtPct(b.win_rate)} <span className="text-slate-500">({b.total})</span></span>
                  </div>
                ))}
              </div>
              <div className="text-slate-400 mb-1">Bottom buckets:</div>
              <div className="space-y-0.5">
                {(e.bottom_buckets || []).map((b) => (
                  <div key={b.value} className="flex items-center justify-between bg-slate-900/40 rounded px-2 py-0.5">
                    <span className="text-slate-300 truncate">{b.value}</span>
                    <span className="text-white font-mono">{fmtPct(b.win_rate)} <span className="text-slate-500">({b.total})</span></span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default WhatIfReplayPanel;
