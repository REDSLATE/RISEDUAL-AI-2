import React, { useCallback, useEffect, useState } from 'react';
import { Filter, RefreshCw, AlertTriangle, CheckCircle2 } from 'lucide-react';

const API = process.env.REACT_APP_BACKEND_URL || '';

async function apiGet(path) {
  const token = localStorage.getItem('token') || '';
  const res = await fetch(`${API}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  return res.json();
}

// The five terminal buckets a scanned candidate can fall into,
// ordered as they occur in the pipeline. Each maps to a counter key
// on the funnel object and (for symbols/sub-reasons) a gate key on
// the why-not-trade rollup.
const GATES = [
  { key: 'market_data_degraded', gate: 'market_data_degraded', label: 'Data Degraded', tone: 'infra',
    help: 'Feed stale / no live book / no volume — failed closed, not a real signal.' },
  { key: 'rank_culled', gate: null, label: 'Rank Culled', tone: 'neutral',
    help: 'Built fine but ranked below the per-tick detection cap.' },
  { key: 'opportunity_score_rejected', gate: 'opportunity_score_rejected', label: 'Below Floor', tone: 'protect',
    help: 'Opportunity score under the family floor — never reached pattern engine.' },
  { key: 'wave_danger_pause', gate: 'wave_danger_pause', label: 'Wave Danger', tone: 'protect',
    help: 'Per-symbol Wave Intelligence flagged DANGER_PAUSE.' },
  { key: 'no_pattern_match', gate: 'no_pattern_match', label: 'No Pattern', tone: 'protect',
    help: 'Cleared floor + wave but no setup shape matched.' },
  { key: 'setups_created', gate: null, label: 'Setups', tone: 'good',
    help: 'Candidate became a tracked setup.' },
];

const TONE = {
  infra: 'border-red-700/50 bg-red-950/30 text-red-200',
  protect: 'border-amber-700/40 bg-amber-950/20 text-amber-200',
  neutral: 'border-zinc-700/50 bg-zinc-900/40 text-zinc-300',
  good: 'border-emerald-700/50 bg-emerald-950/25 text-emerald-200',
};

export default function WhyNotTradeCard({ funnel }) {
  const [detail, setDetail] = useState(null);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    setErr('');
    try {
      const d = await apiGet('/api/admin/alpha-daytrader/why-not-trade?since_seconds=86400&sample_per_gate=3');
      setDetail(d);
    } catch (e) {
      setErr(String(e.message || e));
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const t = setInterval(load, 45_000);
    return () => clearInterval(t);
  }, [load]);

  const f = funnel || {};
  const rej = detail?.rejections || {};
  const scanned = f.symbols_scanned ?? 0;
  const seen = f.candidates_seen ?? 0;
  const unaccounted = f.candidates_unaccounted ?? 0;

  // Dominant blocker across the terminal reject buckets (exclude setups).
  const blockers = GATES.filter((g) => g.key !== 'setups_created')
    .map((g) => ({ ...g, count: f[g.key] ?? 0 }))
    .sort((a, b) => b.count - a.count);
  const worst = blockers[0];

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-950/40 p-4" data-testid="why-not-trade-card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium text-zinc-200 flex items-center gap-2">
          <Filter className="w-4 h-4 text-cyan-400" />
          Why Alpha didn&apos;t trade
        </h3>
        <button
          type="button"
          onClick={load}
          data-testid="why-not-trade-refresh"
          className="text-zinc-400 hover:text-zinc-200"
          title="Refresh"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      {/* Reconciliation banner */}
      <div
        className={`mb-3 flex items-center gap-2 rounded-lg border px-3 py-2 text-xs ${
          unaccounted > 0 ? 'border-amber-700/50 bg-amber-950/20 text-amber-200'
            : 'border-emerald-800/40 bg-emerald-950/20 text-emerald-300'
        }`}
        data-testid="why-not-trade-reconcile"
      >
        {unaccounted > 0
          ? <AlertTriangle className="w-4 h-4 shrink-0" />
          : <CheckCircle2 className="w-4 h-4 shrink-0" />}
        <span>
          {scanned.toLocaleString()} scanned → {seen.toLocaleString()} candidates.{' '}
          {unaccounted > 0
            ? `${unaccounted.toLocaleString()} unaccounted — a candidate slipped through without a terminal reason.`
            : 'Every candidate accounted for — no silent disappearances.'}
        </span>
      </div>

      {worst && worst.count > 0 && (
        <div className="mb-3 text-xs text-zinc-400" data-testid="why-not-trade-headline">
          Top blocker:{' '}
          <span className="font-semibold text-zinc-200">{worst.label}</span>{' '}
          ({worst.count.toLocaleString()})
          {worst.tone === 'infra'
            ? ' — this is a DATA problem, fix the feed, not the trading policy.'
            : ''}
        </div>
      )}

      {/* Terminal buckets */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
        {GATES.map((g) => {
          const count = f[g.key] ?? 0;
          const slot = g.gate ? rej[g.gate] : null;
          const symbols = slot?.symbols || [];
          const subReasons = slot ? Object.entries(slot.sub_reasons || {}) : [];
          return (
            <div
              key={g.key}
              className={`rounded-lg border p-2 ${TONE[g.tone]}`}
              data-testid={`why-not-trade-gate-${g.key}`}
              title={g.help}
            >
              <div className="text-[10px] uppercase tracking-wide opacity-80">{g.label}</div>
              <div className="mt-0.5 text-xl font-semibold">{count.toLocaleString()}</div>
              {subReasons.length > 0 && (
                <div className="mt-1 space-y-0.5">
                  {subReasons.slice(0, 2).map(([r, n]) => (
                    <div key={r} className="text-[10px] opacity-75 truncate">{r}: {n}</div>
                  ))}
                </div>
              )}
              {symbols.length > 0 && (
                <div className="mt-1 text-[10px] opacity-60 truncate">
                  {symbols.slice(0, 4).join(', ')}{symbols.length > 4 ? '…' : ''}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {err && (
        <div className="mt-2 text-[11px] text-red-400" data-testid="why-not-trade-error">
          Detail feed unavailable: {err} (counts above are still authoritative).
        </div>
      )}
      <p className="mt-2 text-[11px] text-zinc-500">
        Counts are today&apos;s totals. Symbols &amp; sub-reasons are from the last 24h lifecycle stream.
        Restore valid data first; only tune the floor once <span className="text-zinc-300">Below Floor</span> is
        proven to dominate on a healthy feed.
      </p>
    </div>
  );
}
