import React, { useCallback, useEffect, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  PauseCircle,
  RefreshCw,
  XCircle,
} from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api/admin/bot-status`;

const SEVERITY_STYLES = {
  ok:      { icon: CheckCircle2, cls: 'bg-emerald-500/10 border-emerald-500/40 text-emerald-300' },
  holding: { icon: PauseCircle,  cls: 'bg-amber-500/10   border-amber-500/40   text-amber-300' },
  warn:    { icon: AlertTriangle, cls: 'bg-orange-500/10 border-orange-500/40 text-orange-300' },
  broken:  { icon: XCircle,       cls: 'bg-red-500/10    border-red-500/40    text-red-300' },
};

/**
 * BotStatusCard — one-page answer to "why isn't Alpha trading?".
 *
 * Backs onto ``GET /api/admin/bot-status`` which aggregates regime,
 * universe, recent scans, skip-reason buckets, last fill, and Atlas
 * health into a single verdict + sub-tiles. Operators no longer need
 * DevTools or curl to check bot state.
 */
const BotStatusCard = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await authFetch(API);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000); // refresh every 30s
    return () => clearInterval(t);
  }, [load]);

  if (loading && !data) {
    return (
      <div data-testid="bot-status-card-loading" className="rounded-lg border border-slate-700 bg-slate-900/50 p-4">
        <div className="flex items-center gap-2 text-slate-400 text-sm">
          <RefreshCw className="w-4 h-4 animate-spin" /> Loading bot status…
        </div>
      </div>
    );
  }
  if (error) {
    return (
      <div data-testid="bot-status-card-error" className="rounded-lg border border-red-500/40 bg-red-500/10 p-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-red-300 text-sm">
            <XCircle className="w-4 h-4" /> Bot status: {error}
          </div>
          <button
            data-testid="bot-status-retry-btn"
            onClick={load}
            className="text-xs px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-200"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }
  if (!data) return null;

  const sev = data.verdict?.severity || 'ok';
  const { icon: Icon, cls } = SEVERITY_STYLES[sev] || SEVERITY_STYLES.ok;

  return (
    <div data-testid="bot-status-card" className="rounded-lg border border-slate-700 bg-slate-900/50 p-4 space-y-4">
      {/* Verdict banner */}
      <div className={`rounded-md border p-3 flex items-start justify-between gap-4 ${cls}`}>
        <div className="flex items-start gap-3">
          <Icon className="w-6 h-6 mt-0.5 flex-shrink-0" />
          <div>
            <div data-testid="bot-status-verdict-headline" className="font-semibold text-base">
              {data.verdict?.headline}
            </div>
            <ul className="mt-1 text-sm text-slate-300 space-y-0.5">
              {(data.verdict?.details || []).map((d, i) => (
                <li key={i} data-testid={`bot-status-verdict-detail-${i}`}>· {d}</li>
              ))}
            </ul>
          </div>
        </div>
        <button
          data-testid="bot-status-refresh-btn"
          onClick={load}
          className="text-xs px-2 py-1 rounded bg-slate-800/80 hover:bg-slate-700 text-slate-200 flex items-center gap-1"
          title="Refresh"
        >
          <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} /> Refresh
        </button>
      </div>

      {/* Sub-tiles */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
        <Tile testid="bot-status-tile-regime" title="Regime">
          <Row label="Slow"     value={data.regime?.slow?.label || '—'} />
          <Row label="Prob"     value={fmtProb(data.regime?.slow?.probability)} />
          <Row label="Fast"     value={data.regime?.fast?.label || '—'} />
        </Tile>
        <Tile testid="bot-status-tile-universe" title="Universe">
          <Row label="Count"    value={data.universe?.count ?? '—'} />
          <Row label="Updated"  value={fmtMinutes(data.universe?.minutes_since_update, ' ago')} />
          <Row label="Sample"   value={(data.universe?.sample || []).slice(0, 3).map(s => s.symbol).join(', ') || '—'} />
        </Tile>
        <Tile testid="bot-status-tile-lastfill" title="Last Fill">
          <Row label="Symbol"   value={data.last_fill?.symbol || '—'} />
          <Row label="Kind"     value={data.last_fill?.kind   || '—'} />
          <Row label="When"     value={fmtMinutes(data.last_fill?.minutes_ago, ' ago')} />
        </Tile>
        <Tile testid="bot-status-tile-atlas" title="Atlas Ledger">
          <Row label="Enabled"  value={data.atlas?.enabled ? 'yes' : 'no'} />
          <Row label="Inflight" value={data.atlas?.inflight ?? '—'} />
        </Tile>
      </div>

      {/* Recent scans */}
      <div>
        <div className="text-xs uppercase tracking-wider text-slate-400 mb-1">
          Recent scans
        </div>
        <div data-testid="bot-status-recent-scans" className="text-xs font-mono">
          {(data.recent_scans || []).length === 0 && (
            <div className="text-slate-500">No scans logged.</div>
          )}
          {(data.recent_scans || []).map((s, i) => (
            <div
              key={s.scan_id || i}
              data-testid={`bot-status-recent-scan-${i}`}
              className="py-1 border-t border-slate-800 first:border-t-0 flex justify-between gap-2"
            >
              <span className="text-slate-400">
                {s.started_at?.slice(11, 19)} · {s.asset_class}
              </span>
              <span className="text-slate-300">
                scanned {s.total_scanned ?? 0} · blocked {s.blocked_count ?? 0}
                {s.chosen_symbol ? ` · chose ${s.chosen_symbol}` : ' · no pick'}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Skip reasons */}
      <div>
        <div className="text-xs uppercase tracking-wider text-slate-400 mb-1">
          Skip reasons (24h)
        </div>
        <div data-testid="bot-status-skip-reasons" className="flex flex-wrap gap-1.5">
          {(data.skip_reasons_24h || []).length === 0 && (
            <span className="text-xs text-slate-500">None recorded.</span>
          )}
          {(data.skip_reasons_24h || []).map((sr, i) => (
            <span
              key={sr.reason || i}
              data-testid={`bot-status-skip-reason-${i}`}
              className="text-xs bg-slate-800 border border-slate-700 rounded-full px-2 py-0.5 text-slate-300"
            >
              {sr.reason} <span className="text-slate-500">×{sr.count}</span>
            </span>
          ))}
        </div>
      </div>
    </div>
  );
};

const Tile = ({ title, testid, children }) => (
  <div data-testid={testid} className="rounded-md border border-slate-800 bg-slate-950/40 p-3 space-y-1">
    <div className="text-xs uppercase tracking-wider text-slate-400">{title}</div>
    {children}
  </div>
);

const Row = ({ label, value }) => (
  <div className="flex justify-between text-xs">
    <span className="text-slate-500">{label}</span>
    <span className="text-slate-200 font-mono truncate ml-2 max-w-[10rem]">{String(value)}</span>
  </div>
);

const fmtProb = (p) => (typeof p === 'number' ? p.toFixed(2) : '—');
const fmtMinutes = (m, suffix = '') => {
  if (m === null || m === undefined) return '—';
  if (m < 60) return `${m}m${suffix}`;
  if (m < 60 * 24) return `${Math.floor(m / 60)}h${suffix}`;
  return `${Math.floor(m / 60 / 24)}d${suffix}`;
};

export default BotStatusCard;
