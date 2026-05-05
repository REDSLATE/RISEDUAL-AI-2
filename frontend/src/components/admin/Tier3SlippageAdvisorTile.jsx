import React, { useEffect, useState, useCallback } from 'react';
import { Brain, AlertCircle, AlertTriangle, Info, Check, X } from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * Tier-3 Slippage Advisor · proposed env-tweak audit trail.
 *
 * Polls /api/admin/tier3-slippage-advisor/proposals?status=pending
 * every 5 min. Each proposal carries a severity (info/warn/critical),
 * a segment description, the metrics that triggered it, and a
 * human-readable rationale. Operator can mark accepted or dismissed
 * — but applying the actual env change is still a manual edit
 * + restart. This UI is a queue, not an auto-apply surface.
 */
const Tier3SlippageAdvisorTile = () => {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [actionInflight, setActionInflight] = useState(null);

  const fetchProposals = useCallback(async () => {
    try {
      const r = await authFetch(`${API}/admin/tier3-slippage-advisor/proposals?status=pending&limit=20`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
      setError(null);
    } catch (e) {
      setError(e.message || 'fetch failed');
    }
  }, []);

  useEffect(() => {
    fetchProposals();
    const id = setInterval(fetchProposals, 5 * 60_000);
    return () => clearInterval(id);
  }, [fetchProposals]);

  const setStatus = async (p, newStatus) => {
    setActionInflight(p.segment_key);
    try {
      await authFetch(`${API}/admin/tier3-slippage-advisor/proposals/status`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          segment_key: p.segment_key,
          generated_week: p.generated_week,
          status: newStatus,
        }),
      });
      await fetchProposals();
    } catch (e) {
      setError(e.message || 'status update failed');
    } finally {
      setActionInflight(null);
    }
  };

  if (error) {
    return (
      <div
        className="rounded-xl border border-rose-500/30 bg-rose-500/5 p-3 text-xs text-rose-300"
        data-testid="tier3-advisor-error"
      >
        Tier-3 Advisor error: {error}
      </div>
    );
  }
  if (!data) return null;

  const rows = data.rows || [];
  const pendingCount = rows.length;

  let headerColor = 'text-slate-300';
  let headerBg = 'bg-slate-800/30';
  let headerBorder = 'border-slate-700/60';
  let HeaderIcon = Brain;
  let pillLabel = pendingCount === 0 ? 'No pending proposals' : `${pendingCount} pending`;
  if (rows.some(r => r.severity === 'critical')) {
    headerColor = 'text-rose-300';
    headerBg = 'bg-rose-500/5';
    headerBorder = 'border-rose-500/40';
    HeaderIcon = AlertCircle;
    pillLabel = `${pendingCount} pending · critical`;
  } else if (rows.some(r => r.severity === 'warn')) {
    headerColor = 'text-amber-300';
    headerBg = 'bg-amber-500/5';
    headerBorder = 'border-amber-500/40';
    HeaderIcon = AlertTriangle;
    pillLabel = `${pendingCount} pending · warn`;
  }

  const sevPill = (sev) => {
    if (sev === 'critical') return 'bg-rose-500/15 text-rose-300';
    if (sev === 'warn') return 'bg-amber-500/15 text-amber-300';
    return 'bg-slate-500/15 text-slate-300';
  };

  return (
    <div
      data-testid="tier3-advisor-tile"
      className={`rounded-xl border ${headerBorder} ${headerBg} p-3`}
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs uppercase tracking-wider">
        <div className={`flex items-center gap-2 ${headerColor}`}>
          <Brain className="h-3.5 w-3.5" /> Tier-3 Slippage Advisor
        </div>
        <div className="flex items-center gap-2">
          <span
            data-testid="tier3-advisor-pending-pill"
            className={`flex items-center gap-1 rounded-full border ${headerBorder} bg-slate-900/40 px-2 py-0.5 text-[10px] font-semibold normal-case ${headerColor}`}
          >
            <HeaderIcon className="h-3 w-3" />
            {pillLabel}
          </span>
        </div>
      </div>

      {pendingCount === 0 && (
        <div
          data-testid="tier3-advisor-empty"
          className="rounded-md border border-slate-700/40 bg-slate-900/30 px-3 py-2 text-[11px] text-slate-400"
        >
          <Info className="inline h-3 w-3 mr-1" />
          No actionable slippage outliers detected. The advisor runs weekly
          (Mondays 13:15 UTC) and only flags segments with ≥ 20 trades.
        </div>
      )}

      <div className="space-y-2">
        {rows.map((p) => (
          <div
            key={`${p.segment_key}:${p.generated_week}`}
            data-testid={`tier3-advisor-proposal-${p.segment_key}`}
            className="rounded-md border border-slate-700/50 bg-slate-900/40 p-2"
          >
            <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-[11px]">
                <span
                  data-testid={`tier3-advisor-severity-${p.segment_key}`}
                  className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${sevPill(p.severity)}`}
                >
                  {p.severity}
                </span>
                <span className="font-mono text-[10px] text-slate-400">
                  {p.axis}={p.value} · {p.ratio}× baseline
                </span>
                <span className="rounded bg-slate-800/60 px-1.5 py-0.5 font-mono text-[9px] text-slate-400">
                  {p.env_knob}
                </span>
              </div>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  data-testid={`tier3-advisor-accept-${p.segment_key}`}
                  className="rounded bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-300 hover:bg-emerald-500/25 disabled:opacity-50"
                  onClick={() => setStatus(p, 'accepted')}
                  disabled={actionInflight === p.segment_key}
                >
                  <Check className="inline h-3 w-3 mr-0.5" />
                  Accept
                </button>
                <button
                  type="button"
                  data-testid={`tier3-advisor-dismiss-${p.segment_key}`}
                  className="rounded bg-slate-800/60 px-2 py-0.5 text-[10px] font-semibold text-slate-300 hover:bg-slate-800 disabled:opacity-50"
                  onClick={() => setStatus(p, 'dismissed')}
                  disabled={actionInflight === p.segment_key}
                >
                  <X className="inline h-3 w-3 mr-0.5" />
                  Dismiss
                </button>
              </div>
            </div>
            <div className="text-[11px] text-slate-200">{p.rationale}</div>
            <div className="mt-1 flex flex-wrap gap-2 text-[10px] text-slate-500">
              <span>n={p.metrics?.count}</span>
              <span>avg={p.metrics?.avg_bps}bps</span>
              <span>${p.metrics?.total_dollar_cost?.toFixed(2)}</span>
              <span>drag {p.metrics?.drag_pct_of_pnl}%</span>
              <span>· {p.generated_week}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default Tier3SlippageAdvisorTile;
