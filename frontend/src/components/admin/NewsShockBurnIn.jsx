import React, { useEffect, useState, useCallback } from 'react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import { RefreshCw, Radio, Newspaper, Database, ShieldCheck, Activity } from 'lucide-react';

const API = `${getApiBase()}/api`;
const POLL_INTERVAL_MS = 60_000;

// Six signals the operator watches on Monday AM:
// 1. Scheduler tick running?        — scheduler.last_updated_at fresh
// 2. Feeders persisting articles?   — catalyst_events.total rising
// 3. Shock compute recording?       — news_telemetry.total_rows rising
// 4. Snapshots projecting?          — catalyst_snapshots.total > 0
// 5. Smart-Money blocks posting?    — smart_money_blocks_24h > 0
// 6. Warm feeder running?           — equity_telemetry.total_symbols > 0
//
// A single chip per signal with traffic-light colouring turns the
// Monday-AM curl ritual into a one-glance read.
const Chip = ({ label, value, status, sub }) => {
  const colours = {
    green:  'bg-emerald-500/10 border-emerald-500/40 text-emerald-300',
    yellow: 'bg-amber-500/10 border-amber-500/40 text-amber-300',
    red:    'bg-rose-500/10 border-rose-500/40 text-rose-300',
    gray:   'bg-slate-700/40 border-slate-600/60 text-slate-300',
  };
  return (
    <div
      data-testid={`burn-in-chip-${label.toLowerCase().replace(/\s+/g, '-')}`}
      className={`flex flex-col gap-1 rounded-xl border px-4 py-3 ${colours[status] || colours.gray}`}
    >
      <div className="text-xs uppercase tracking-wider opacity-70">{label}</div>
      <div className="text-2xl font-semibold tabular-nums">{value ?? '—'}</div>
      {sub ? <div className="text-xs opacity-60">{sub}</div> : null}
    </div>
  );
};

const freshness = (iso) => {
  if (!iso) return { label: 'never', status: 'gray' };
  const ageMs = Date.now() - new Date(iso).getTime();
  const min = Math.floor(ageMs / 60_000);
  if (min < 20)  return { label: `${min}m ago`, status: 'green' };
  if (min < 120) return { label: `${min}m ago`, status: 'yellow' };
  return { label: `${Math.floor(min / 60)}h ago`, status: 'red' };
};

const NewsShockBurnIn = () => {
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);
  const [lastPoll, setLastPoll] = useState(null);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await authFetch(`${API}/admin/news-shock/burn-in`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setData(json);
      setError(null);
      setLastPoll(new Date());
    } catch (e) {
      setError(e.message || 'fetch failed');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    const id = setInterval(fetchStatus, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [fetchStatus]);

  if (loading && !data) {
    return <div className="text-slate-400 text-sm">Loading burn-in status…</div>;
  }
  if (error && !data) {
    return <div className="text-rose-400 text-sm">Error: {error}</div>;
  }
  if (!data) return null;

  const sched = data.scheduler || {};
  const ev    = data.catalyst_events || {};
  const tel   = data.news_telemetry || {};
  const snaps = data.catalyst_snapshots || {};
  const eq    = data.equity_telemetry || {};

  const schedFresh = freshness(sched.last_updated_at);
  const eventsFresh = freshness(ev.latest_event_time);
  const telFresh = freshness(tel.latest_created_at);

  return (
    <div data-testid="news-shock-burn-in" className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold text-slate-100">NEWS_SHOCK burn-in</h3>
          <p className="text-xs text-slate-400">
            Polls every 60 s · last refresh {lastPoll ? lastPoll.toLocaleTimeString() : '—'}
          </p>
        </div>
        <button
          onClick={fetchStatus}
          data-testid="burn-in-refresh"
          className="flex items-center gap-2 rounded-lg border border-slate-600 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-700"
        >
          <RefreshCw className="h-3.5 w-3.5" /> Refresh
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
        <Chip
          label="Scheduler"
          value={sched.last_offset ?? '—'}
          status={sched.last_updated_at ? schedFresh.status : 'gray'}
          sub={`offset · ${schedFresh.label}`}
        />
        <Chip
          label="Catalyst Events"
          value={ev.total ?? 0}
          status={ev.total > 0 ? eventsFresh.status : 'gray'}
          sub={`latest: ${ev.latest_symbol ?? '—'} · ${eventsFresh.label}`}
        />
        <Chip
          label="News Telemetry"
          value={tel.total_rows ?? 0}
          status={tel.total_rows > 0 ? telFresh.status : 'gray'}
          sub={`last: ${tel.latest_symbol ?? '—'} vol=${tel.latest_news_volume ?? '—'}`}
        />
        <Chip
          label="Snapshots"
          value={snaps.total ?? 0}
          status={snaps.total > 0 ? 'green' : 'gray'}
          sub={`${snaps.zscore_ready ?? 0} z-ready`}
        />
        <Chip
          label="Smart-Money 24 h"
          value={data.smart_money_blocks_24h ?? 0}
          status={(data.smart_money_blocks_24h ?? 0) > 0 ? 'green' : 'gray'}
          sub="proof-chain blocks"
        />
        <Chip
          label="Equity Telemetry"
          value={eq.total_symbols_tracked ?? 0}
          status={(eq.total_symbols_tracked ?? 0) > 0 ? 'green' : 'red'}
          sub={`${(eq.sample || []).filter((s) => s.has_dollar_volume).length}/${(eq.sample || []).length} w/ $vol`}
        />
      </div>

      {snaps.most_recent && snaps.most_recent.length > 0 ? (
        <div className="rounded-xl border border-slate-700 bg-slate-800/40 p-3">
          <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-wider text-slate-400">
            <Activity className="h-3.5 w-3.5" /> most recent snapshots
          </div>
          <div className="grid grid-cols-1 gap-1 md:grid-cols-3">
            {snaps.most_recent.map((s) => (
              <div
                key={s.symbol}
                className="flex items-center justify-between rounded-md border border-slate-700/60 bg-slate-900/40 px-2.5 py-1.5 text-sm text-slate-300"
              >
                <span className="font-mono text-slate-100">{s.symbol}</span>
                <span className="text-xs text-slate-400">
                  {s.news_shock?.shock_state ?? '—'} · {s.event_risk ?? '—'}
                </span>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="grid grid-cols-2 gap-2 text-xs text-slate-400 md:grid-cols-4">
        <div className="flex items-center gap-1.5"><Radio className="h-3.5 w-3.5" /> Tick 15 min RTH</div>
        <div className="flex items-center gap-1.5"><Newspaper className="h-3.5 w-3.5" /> Benzinga + AV</div>
        <div className="flex items-center gap-1.5"><Database className="h-3.5 w-3.5" /> Async Motor</div>
        <div className="flex items-center gap-1.5"><ShieldCheck className="h-3.5 w-3.5" /> Patent J + M</div>
      </div>
    </div>
  );
};

export default NewsShockBurnIn;
