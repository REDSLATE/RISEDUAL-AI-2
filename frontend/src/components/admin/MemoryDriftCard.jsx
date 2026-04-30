import React, { useEffect, useState, useCallback } from 'react';
import { authFetch } from '../../contexts/AuthContext';
import { Card } from '../ui/card';

const API = process.env.REACT_APP_BACKEND_URL;

/**
 * MemoryDriftCard — Mongo→Chroma sync observability for the
 * Patent J / Proof Chain admin panel.
 *
 * Surfaces three things the operator needs to know:
 *  1. Drift count + percentage with a recommendation badge
 *     (ok / investigate / rebuild).
 *  2. Per-date skew breakdown so the regression window is
 *     immediately visible.
 *  3. Sync skip counters + last-rebuild timestamp so 8% drift
 *     right after a rebuild reads as "expected" while the same
 *     8% an hour later reads as "actively broken".
 *
 * Aesthetic mirrors `BlocksPreventedCard.jsx` — dark slate panel,
 * cyan ``#3DE8D9`` accent, monospace tabular numerals.
 *
 * Polls every 60s; manual refresh hits the same cheap counts
 * endpoint.
 */
export default function MemoryDriftCard() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [days, setDays] = useState(30);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await authFetch(
        `${API}/api/admin/memory/drift?days=${days}&top_skew=8`,
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setData(json);
    } catch (e) {
      setError(e.message || 'Failed to load drift data');
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, [load]);

  if (error) {
    return (
      <Card className="p-4 bg-slate-800/40 border-red-500/40" data-testid="memory-drift-card">
        <div className="text-sm text-red-300 font-semibold mb-1">
          Memory drift detector unavailable
        </div>
        <div className="text-xs text-red-300/80">{error}</div>
      </Card>
    );
  }

  if (!data) {
    return (
      <Card className="p-4 bg-slate-800/40 border-slate-700/40" data-testid="memory-drift-card">
        <div className="text-xs text-slate-400">Loading memory drift…</div>
      </Card>
    );
  }

  if (!data.available) {
    return (
      <Card className="p-4 bg-slate-800/40 border-slate-700/40" data-testid="memory-drift-card">
        <div className="text-sm text-slate-300 font-semibold mb-1">Memory drift</div>
        <div className="text-xs text-slate-400">Data unavailable: {data.reason || 'unknown'}</div>
      </Card>
    );
  }

  const recBadge = badgeStyle(data.recommendation);
  const lastRebuildAgo = data.last_rebuild_at ? humanAgo(data.last_rebuild_at) : null;
  const skipEntries = Object.entries(data.sync_skipped_total || {})
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);

  return (
    <Card
      className="p-4 bg-slate-800/40 border-slate-700/40"
      data-testid="memory-drift-card"
    >
      {/* Header */}
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3 className="text-white text-sm font-semibold uppercase tracking-wider">
            Memory Drift — Mongo → Chroma
          </h3>
          <p className="text-[10px] text-slate-400 mt-0.5">
            Verified prediction count vs ChromaDB episodes ·{' '}
            {data.window_days}d window
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            data-testid="memory-drift-days"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="text-[10px] bg-slate-900/60 border border-slate-700/60 text-slate-300 rounded px-1.5 py-0.5"
          >
            <option value={7}>7d</option>
            <option value={30}>30d</option>
            <option value={90}>90d</option>
          </select>
          <button
            data-testid="memory-drift-refresh"
            onClick={load}
            disabled={loading}
            className="text-[10px] px-2 py-0.5 bg-slate-900/60 hover:bg-slate-900/80 border border-slate-700/60 rounded text-slate-300 disabled:opacity-50"
          >
            {loading ? '…' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Recommendation badge */}
      <div
        data-testid="memory-drift-recommendation"
        className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-medium mb-3 ${recBadge.classes}`}
      >
        <span className={`w-1.5 h-1.5 rounded-full ${recBadge.dot}`} />
        {recBadge.label}
      </div>

      {/* Metric tiles */}
      <div className="grid grid-cols-3 gap-2 mb-3">
        <Tile testid="memory-drift-mongo" label="Mongo verified" value={data.mongo_verified_count.toLocaleString()} />
        <Tile testid="memory-drift-chroma" label="Chroma episodes" value={data.chroma_episode_count.toLocaleString()} />
        <Tile
          testid="memory-drift-pct"
          label="Drift %"
          value={`${data.drift_pct}%`}
          accent={recBadge.tileAccent}
        />
      </div>

      {/* Per-date skew table */}
      {data.by_date_top_skew && data.by_date_top_skew.length > 0 && (
        <div className="mb-3">
          <div className="text-[9px] text-slate-500 uppercase tracking-wider mb-1.5">
            Per-Date Skew
          </div>
          <div
            className="rounded-md border border-slate-700/40 bg-slate-900/40 overflow-hidden"
            data-testid="memory-drift-skew-table"
          >
            <div className="grid grid-cols-[1fr,auto,auto,auto] gap-x-3 px-2 py-1 text-[9px] text-slate-500 uppercase tracking-wider border-b border-slate-700/40">
              <div>Date</div>
              <div className="text-right">Mongo</div>
              <div className="text-right">Chroma</div>
              <div className="text-right">Skew</div>
            </div>
            {data.by_date_top_skew.map((row) => (
              <div
                key={row.date}
                data-testid={`memory-drift-skew-row-${row.date}`}
                className="grid grid-cols-[1fr,auto,auto,auto] gap-x-3 px-2 py-1 text-[11px] border-t border-slate-800/60 first:border-t-0"
              >
                <div className="text-slate-300 font-mono">{row.date}</div>
                <div className="text-slate-400 font-mono tabular-nums text-right">{row.mongo}</div>
                <div className="text-slate-400 font-mono tabular-nums text-right">{row.chroma}</div>
                <div
                  className={`font-mono tabular-nums text-right font-medium ${
                    row.skew > 0 ? 'text-amber-300' : 'text-slate-500'
                  }`}
                >
                  {row.skew > 0 ? `+${row.skew}` : row.skew}
                </div>
              </div>
            ))}
          </div>
          <p className="text-[9px] text-slate-500 mt-1">
            Positive skew = Mongo ahead (sync regression) · Negative = training over-supply (benign)
          </p>
        </div>
      )}

      {/* Skip counters */}
      {skipEntries.length > 0 && (
        <div className="mb-3">
          <div className="text-[9px] text-slate-500 uppercase tracking-wider mb-1.5">
            Sync Skip Counters
          </div>
          <div className="space-y-1" data-testid="memory-drift-skip-counters">
            {skipEntries.map(([reason, count]) => (
              <div
                key={reason}
                className="flex justify-between text-[10px] px-1.5 py-1 bg-amber-500/10 border border-amber-500/30 rounded"
              >
                <code className="text-amber-300 font-mono">{reason}</code>
                <span className="font-medium text-amber-200 tabular-nums">{count}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Last-rebuild context */}
      <div className="pt-3 border-t border-slate-700/40 text-[9px] text-slate-500 flex items-baseline justify-between">
        <span>
          {data.last_rebuild_at ? (
            <>
              Last rebuild{' '}
              <span className="text-slate-300 font-medium">{lastRebuildAgo}</span>
              {data.last_rebuild_summary && (
                <>
                  {' · '}
                  rebuilt {data.last_rebuild_summary.rebuilt}, skipped{' '}
                  {data.last_rebuild_summary.skipped}
                </>
              )}
            </>
          ) : (
            <span className="italic">No rebuild recorded since last restart</span>
          )}
        </span>
        <span className="font-mono">risedual.ai</span>
      </div>
    </Card>
  );
}

function Tile({ testid, label, value, accent }) {
  return (
    <div
      data-testid={testid}
      className={`rounded-md border p-2 ${
        accent ? `${accent.bg} ${accent.border}` : 'bg-slate-900/40 border-slate-700/40'
      }`}
    >
      <div className="text-[9px] uppercase tracking-wider text-slate-500 mb-0.5">{label}</div>
      <div
        className={`text-base font-semibold font-mono tabular-nums ${
          accent?.text || 'text-slate-100'
        }`}
      >
        {value}
      </div>
    </div>
  );
}

function badgeStyle(rec) {
  switch (rec) {
    case 'ok':
      return {
        label: 'In sync',
        classes: 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/30',
        dot: 'bg-emerald-400',
        tileAccent: null,
      };
    case 'investigate':
      return {
        label: 'Investigate drift',
        classes: 'bg-amber-500/10 text-amber-300 border border-amber-500/30',
        dot: 'bg-amber-400',
        tileAccent: { bg: 'bg-amber-500/10', border: 'border-amber-500/30', text: 'text-amber-200' },
      };
    case 'rebuild':
      return {
        label: 'Rebuild recommended',
        classes: 'bg-red-500/10 text-red-300 border border-red-500/30',
        dot: 'bg-red-400',
        tileAccent: { bg: 'bg-red-500/10', border: 'border-red-500/30', text: 'text-red-200' },
      };
    default:
      return {
        label: rec || 'unknown',
        classes: 'bg-slate-700/30 text-slate-300 border border-slate-700/40',
        dot: 'bg-slate-500',
        tileAccent: null,
      };
  }
}

function humanAgo(iso) {
  try {
    const then = new Date(iso).getTime();
    const diffSec = Math.max(0, Math.floor((Date.now() - then) / 1000));
    if (diffSec < 60) return `${diffSec}s ago`;
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
    return `${Math.floor(diffSec / 86400)}d ago`;
  } catch {
    return iso;
  }
}
