import React, { useEffect, useState, useCallback } from 'react';
import { authFetch } from '../../contexts/AuthContext';
import { Card } from '../ui/card';

const API = process.env.REACT_APP_BACKEND_URL;

/**
 * Tier3ProgressDetailCard — expanded view of the composite
 * `tier3_progress_pct` shown in CouncilTierStatusPill, decomposed
 * into the six gates that drive it.
 *
 * Each row renders a gate with:
 *   - Label + current value (e.g. "Live exposure (days): 12 / 30")
 *   - Progress bar coloured by met/unmet state
 *   - Earned / weight-points badge ("3.5 / 20 pts")
 *   - Hint string surfacing the most-blocking next step
 *
 * The sum of earned-points across all rows equals the headline
 * composite score — pinned by `test_breakdown_earned_pts_match
 * _composite_score` so bars and headline never disagree.
 *
 * Aesthetic mirrors `BlocksPreventedCard.jsx` — dark slate panel,
 * cyan ``#3DE8D9`` accent for the headline progress bar.
 */
export default function Tier3ProgressDetailCard() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authFetch(`${API}/api/admin/shadow/tier-readiness`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setData(json);
      setError(null);
    } catch (e) {
      setError(e.message || 'Failed to load tier readiness');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, [load]);

  if (error) {
    return (
      <Card className="p-4 bg-slate-800/40 border-red-500/40" data-testid="tier3-progress-card">
        <div className="text-sm text-red-300 font-semibold mb-1">
          Tier 3 progress unavailable
        </div>
        <div className="text-xs text-red-300/80">{error}</div>
      </Card>
    );
  }

  if (!data) {
    return (
      <Card className="p-4 bg-slate-800/40 border-slate-700/40" data-testid="tier3-progress-card">
        <div className="text-xs text-slate-400">Loading Tier 3 readiness…</div>
      </Card>
    );
  }

  const breakdown = data.tier3_breakdown || [];
  const composite = data.tier3_progress_pct ?? 0;
  const unlocked = !!data.tier3_unlocked;

  return (
    <Card
      className="p-4 bg-slate-800/40 border-slate-700/40"
      data-testid="tier3-progress-card"
    >
      {/* Header */}
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3 className="text-white text-sm font-semibold uppercase tracking-wider">
            Tier 3 Readiness
          </h3>
          <p className="text-[10px] text-slate-400 mt-0.5">
            Six gates that decompose the composite score · Council
            unlocks only when all hard gates clear
          </p>
        </div>
        <button
          data-testid="tier3-progress-refresh"
          onClick={load}
          disabled={loading}
          className="text-[10px] px-2 py-0.5 bg-slate-900/60 hover:bg-slate-900/80 border border-slate-700/60 rounded text-slate-300 disabled:opacity-50"
        >
          {loading ? '…' : 'Refresh'}
        </button>
      </div>

      {/* Headline composite */}
      <div className="mb-4">
        <div className="flex items-baseline justify-between mb-1">
          <span className="text-[9px] uppercase tracking-wider text-slate-500">
            Composite score
          </span>
          <span
            data-testid="tier3-progress-status"
            className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
              unlocked
                ? 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/30'
                : 'bg-slate-700/30 text-slate-400 border border-slate-700/40'
            }`}
          >
            {unlocked ? 'Unlocked' : 'Locked'}
          </span>
        </div>
        <div className="flex items-baseline gap-2">
          <span
            data-testid="tier3-progress-composite"
            className="text-2xl font-semibold text-slate-100 tabular-nums font-mono"
          >
            {composite.toFixed(1)}
          </span>
          <span className="text-xs text-slate-500">/ 100</span>
        </div>
        <div className="w-full h-1.5 bg-slate-900/60 rounded-full mt-1.5 overflow-hidden">
          <div
            data-testid="tier3-progress-composite-bar"
            className={`h-full transition-all rounded-full ${
              unlocked ? 'bg-emerald-400' : 'bg-[#3DE8D9]/80'
            }`}
            style={{ width: `${Math.min(composite, 100)}%` }}
          />
        </div>
      </div>

      {/* Per-gate breakdown */}
      <div className="space-y-2.5" data-testid="tier3-progress-breakdown">
        {breakdown.length === 0 ? (
          <div className="text-[10px] text-slate-500 italic">
            No tier3 breakdown returned by the readiness endpoint.
          </div>
        ) : (
          breakdown.map((row) => <GateRow key={row.key} row={row} />)
        )}
      </div>

      {/* Operator next steps */}
      {data.next_steps && data.next_steps.length > 0 && (
        <div
          className="mt-4 pt-3 border-t border-slate-700/40"
          data-testid="tier3-progress-next-steps"
        >
          <div className="text-[9px] uppercase tracking-wider text-slate-500 mb-1.5">
            Next Steps
          </div>
          <ul className="space-y-1">
            {data.next_steps.map((step, idx) => (
              <li
                key={idx}
                className="text-[11px] text-slate-300 flex gap-1.5"
                data-testid={`tier3-next-step-${idx}`}
              >
                <span className="text-slate-500 shrink-0 font-mono">{idx + 1}.</span>
                <span>{step}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

function GateRow({ row }) {
  const pct = Math.min(Math.max(row.progress_pct ?? 0, 0), 100);
  const barColor = row.met
    ? 'bg-emerald-400'
    : pct >= 75
      ? 'bg-[#3DE8D9]/80'
      : pct >= 25
        ? 'bg-amber-400'
        : 'bg-slate-600';
  const checkmark = row.met ? '✓' : '·';

  return (
    <div data-testid={`tier3-gate-${row.key}`}>
      <div className="flex items-baseline justify-between text-[11px] mb-0.5">
        <div className="flex items-center gap-1.5 text-slate-200">
          <span
            className={`inline-block w-3 text-center font-bold ${
              row.met ? 'text-emerald-400' : 'text-slate-500'
            }`}
          >
            {checkmark}
          </span>
          <span className="font-medium">{row.label}</span>
        </div>
        <div className="flex items-baseline gap-1">
          <span className="text-slate-400 tabular-nums font-mono text-[10px]">
            {row.earned_pts.toFixed(1)} / {row.weight_pct}
          </span>
        </div>
      </div>
      <div className="w-full h-1 bg-slate-900/60 rounded-full overflow-hidden">
        <div
          data-testid={`tier3-gate-${row.key}-bar`}
          className={`h-full transition-all rounded-full ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="text-[10px] text-slate-500 mt-0.5">{row.hint}</div>
    </div>
  );
}
