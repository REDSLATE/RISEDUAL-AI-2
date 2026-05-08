import React, { useEffect, useState, useCallback, useRef } from 'react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import {
  Activity, TrendingUp, Bitcoin, AlertTriangle, CheckCircle2,
  Clock, RefreshCw, Snowflake, Heart,
} from 'lucide-react';

const API = `${getApiBase()}/api`;

const LANE_ICON = { equity: TrendingUp, crypto: Bitcoin };

const REFRESH_INTERVAL_MS = 30_000;

const formatAge = (hours) => {
  if (hours === null || hours === undefined) return '—';
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  if (hours < 48) return `${hours.toFixed(1)}h`;
  return `${(hours / 24).toFixed(1)}d`;
};

const formatLastRun = (iso) => {
  if (!iso) return '—';
  try {
    const t = new Date(iso);
    const sec = Math.max(0, Math.floor((Date.now() - t.getTime()) / 1000));
    if (sec < 60) return `${sec}s ago`;
    if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
    if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
    return `${Math.floor(sec / 86400)}d ago`;
  } catch {
    return iso;
  }
};

const formatHealth = (v) => {
  if (v === null || v === undefined) return '—';
  return `${(v * 100).toFixed(0)}%`;
};

const StatCell = ({ label, value, mono = true, testid, tone = 'default' }) => {
  const toneClass = (
    tone === 'good' ? 'text-emerald-300'
    : tone === 'warn' ? 'text-amber-300'
    : tone === 'bad' ? 'text-rose-300'
    : 'text-slate-100'
  );
  return (
    <div className="flex flex-col" data-testid={testid}>
      <span className="text-[9px] uppercase tracking-[0.12em] text-slate-500">{label}</span>
      <span className={`text-sm ${mono ? 'font-mono' : ''} ${toneClass}`}>{value}</span>
    </div>
  );
};

const HeartbeatLane = ({ info }) => {
  if (!info) return null;
  const lane = info.lane;
  const Icon = LANE_ICON[lane] || Activity;
  const frozen = !!info.frozen;
  const stale = !!info.model_stale;
  const fhAvg = info.feature_health_avg;
  const fhTone = (
    fhAvg === null || fhAvg === undefined ? 'default'
    : fhAvg < 0.3 ? 'bad'
    : fhAvg < 0.6 ? 'warn'
    : 'good'
  );
  const ageHours = info.model_age_hours;
  const ageTone = (
    stale ? 'bad'
    : ageHours !== null && ageHours !== undefined && ageHours > 48 ? 'warn'
    : 'good'
  );

  // Status pill — "Frozen" wins over "Stale" wins over "Live".
  let statusLabel = 'Live';
  let statusClass = 'bg-emerald-950/60 border-emerald-800 text-emerald-200';
  let StatusIcon = CheckCircle2;
  if (frozen) {
    statusLabel = 'Frozen';
    statusClass = 'bg-rose-950/60 border-rose-800 text-rose-200';
    StatusIcon = Snowflake;
  } else if (stale) {
    statusLabel = 'Model Stale';
    statusClass = 'bg-amber-950/60 border-amber-800 text-amber-200';
    StatusIcon = AlertTriangle;
  }

  return (
    <div
      data-testid={`heartbeat-lane-${lane}`}
      className="flex flex-col gap-3 p-4 rounded-lg bg-slate-950/60 border border-slate-800/80"
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-md bg-slate-900 border border-slate-800 flex items-center justify-center">
            <Icon className="w-3.5 h-3.5 text-slate-300" />
          </div>
          <div className="flex flex-col">
            <span
              data-testid={`heartbeat-lane-name-${lane}`}
              className="text-sm font-semibold text-slate-100 capitalize leading-tight"
            >
              {lane}
            </span>
            <span className="text-[10px] text-slate-500 font-mono leading-tight">
              last run · {formatLastRun(info.last_pipeline_run_at)}
            </span>
          </div>
        </div>
        <div
          data-testid={`heartbeat-status-${lane}`}
          className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] uppercase tracking-wide border ${statusClass}`}
        >
          <StatusIcon className="w-3 h-3" />
          {statusLabel}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <StatCell
          label="Signals 1h"
          value={info.signals_1h ?? 0}
          testid={`heartbeat-signals-${lane}`}
        />
        <StatCell
          label="Buy/Sell 1h"
          value={info.buy_sell_1h ?? 0}
          testid={`heartbeat-buysell-${lane}`}
          tone={(info.buy_sell_1h ?? 0) > 0 ? 'good' : 'default'}
        />
        <StatCell
          label="Holds 1h"
          value={info.holds_1h ?? 0}
          testid={`heartbeat-holds-${lane}`}
        />
        <StatCell
          label="Health Avg"
          value={formatHealth(fhAvg)}
          testid={`heartbeat-health-${lane}`}
          tone={fhTone}
        />
        <StatCell
          label="Model Age"
          value={formatAge(ageHours)}
          testid={`heartbeat-modelage-${lane}`}
          tone={ageTone}
        />
        <StatCell
          label="Stale?"
          value={stale ? 'YES' : 'no'}
          testid={`heartbeat-stale-${lane}`}
          tone={stale ? 'bad' : 'good'}
        />
      </div>

      <div className="flex items-start gap-2 px-3 py-2 rounded-md bg-slate-900/50 border border-slate-800/60">
        <Clock className="w-3.5 h-3.5 mt-0.5 text-slate-500 shrink-0" />
        <div className="flex flex-col min-w-0 flex-1">
          <span className="text-[10px] uppercase tracking-wider text-slate-500">
            Top clamp/block reason (1h)
          </span>
          <span
            data-testid={`heartbeat-top-reason-${lane}`}
            className="text-xs font-mono text-slate-300 truncate"
            title={info.top_clamp_block_reason || '—'}
          >
            {info.top_clamp_block_reason ? (
              <>
                {info.top_clamp_block_reason}
                <span className="text-slate-500"> ×{info.top_clamp_block_count}</span>
              </>
            ) : '—'}
          </span>
        </div>
      </div>
    </div>
  );
};

const MLHeartbeatTile = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastRefreshed, setLastRefreshed] = useState(null);
  const intervalRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const resp = await authFetch(`${API}/admin/ml/heartbeat`);
      if (!resp.ok) {
        const text = await resp.text();
        throw new Error(`HTTP ${resp.status}: ${text.slice(0, 160)}`);
      }
      setData(await resp.json());
      setError(null);
      setLastRefreshed(new Date());
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    intervalRef.current = setInterval(load, REFRESH_INTERVAL_MS);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [load]);

  const lanes = data?.lanes || {};
  const anyFrozen = !!data?.any_frozen;

  return (
    <div
      className="bg-slate-900/60 border border-slate-800 rounded-xl overflow-hidden"
      data-testid="ml-heartbeat-tile"
    >
      <div className="px-5 py-3.5 border-b border-slate-800 flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-md bg-slate-900 border border-slate-800 flex items-center justify-center">
            <Heart className={`w-4 h-4 ${anyFrozen ? 'text-rose-400' : 'text-emerald-400'}`} />
          </div>
          <div>
            <h3 className="text-base font-semibold text-slate-100 leading-tight">
              ML Heartbeat
            </h3>
            <p className="text-[11px] text-slate-500 leading-tight mt-0.5">
              Read-only · GET /api/admin/ml/heartbeat · refresh 30s
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {anyFrozen ? (
            <span
              data-testid="heartbeat-banner-frozen"
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-rose-950/60 border border-rose-800 text-rose-200 text-[10px] uppercase tracking-wide"
            >
              <Snowflake className="w-3 h-3" /> Frozen lane
            </span>
          ) : (
            <span
              data-testid="heartbeat-banner-live"
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-950/60 border border-emerald-800 text-emerald-200 text-[10px] uppercase tracking-wide"
            >
              <Activity className="w-3 h-3" /> All lanes alive
            </span>
          )}
          <button
            onClick={load}
            data-testid="heartbeat-refresh-btn"
            className="p-1.5 rounded-md bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-400 transition-colors"
            title="Refresh now"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      <div className="p-5">
        {error ? (
          <div
            className="bg-rose-950/40 border border-rose-900 text-rose-200 rounded-md p-3 text-xs"
            data-testid="heartbeat-error"
          >
            Failed to load heartbeat: {error}
          </div>
        ) : null}

        {loading && !data ? (
          <div className="text-sm text-slate-400 px-4 py-6 text-center" data-testid="heartbeat-loading">
            Loading…
          </div>
        ) : null}

        {data ? (
          <div
            className="grid grid-cols-1 md:grid-cols-2 gap-3"
            data-testid="heartbeat-grid"
          >
            <HeartbeatLane info={lanes.equity} />
            <HeartbeatLane info={lanes.crypto} />
          </div>
        ) : null}

        <div className="mt-3 flex items-center justify-between text-[10px] text-slate-600 font-mono">
          <span data-testid="heartbeat-as-of">
            as_of: {data?.as_of || '—'}
          </span>
          <span>
            {lastRefreshed ? `refreshed ${lastRefreshed.toLocaleTimeString()}` : ''}
          </span>
        </div>
      </div>
    </div>
  );
};

export default MLHeartbeatTile;
