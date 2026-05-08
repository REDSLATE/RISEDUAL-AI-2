import React, { useEffect, useState, useCallback } from 'react';
import { Shield, Hourglass, Rocket, Pause } from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * Equity Commander Shadow · promotion pill.
 *
 * Polls GET /api/admin/commander-shadow/promotion-status every 30s and
 * renders a chip mirroring the Adversarial Cores treatment: a status
 * traffic-light, a promotion pill showing "rows to go" / "Ready" /
 * "TERMINAL", and a warm-up notice until enough scored rows accumulate.
 *
 * Shape of the upstream payload (see equity_shadow_promotion.py):
 *   {
 *     phase:                  "phase_1_logging_only" | "phase_2_brake_eligible"
 *     rows_total, rows_scored, rows_to_go, min_rows_required,
 *     win_rate, min_win_rate_required,
 *     brake_eligible: bool,
 *     blocker: str | null,
 *   }
 */
const EquityCommanderShadowChip = () => {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const fetchStatus = useCallback(async () => {
    try {
      const r = await authFetch(`${API}/admin/commander-shadow/promotion-status`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
      setError(null);
    } catch (e) {
      setError(e.message || 'fetch failed');
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    const id = setInterval(fetchStatus, 30_000);
    return () => clearInterval(id);
  }, [fetchStatus]);

  if (error) {
    return (
      <div
        className="rounded-xl border border-rose-500/30 bg-rose-500/5 p-3 text-xs text-rose-300"
        data-testid="equity-commander-shadow-error"
      >
        Equity Commander Shadow error: {error}
      </div>
    );
  }

  if (!data) return null;

  const {
    phase, rows_total, rows_scored, rows_to_go, min_rows_required,
    win_rate, min_win_rate_required, brake_eligible, blocker,
  } = data;

  // Status traffic-light — matches the Adversarial chip language.
  let statusBorder = 'border-slate-700/60';
  let statusBg = 'bg-slate-800/30';
  let statusText = 'text-slate-300';
  let statusLabel = 'LOGGING';
  let StatusIcon = Pause;

  if (brake_eligible) {
    statusBorder = 'border-emerald-500/40';
    statusBg = 'bg-emerald-500/5';
    statusText = 'text-emerald-300';
    statusLabel = 'BRAKE ELIGIBLE';
    StatusIcon = Shield;
  } else if (rows_total > 0) {
    statusBorder = 'border-amber-500/30';
    statusBg = 'bg-amber-500/5';
    statusText = 'text-amber-300';
    statusLabel = 'LEARNING';
    StatusIcon = Shield;
  }

  // Promotion pill state — brake_eligible = Ready, else rows_to_go.
  let pillBg = 'bg-slate-800/60';
  let pillText = 'text-slate-400';
  let pillBorder = 'border-slate-700/60';
  let PillIcon = Hourglass;
  let pillLabel = `${rows_to_go ?? min_rows_required} rows to Phase 2`;

  if (brake_eligible) {
    pillBg = 'bg-emerald-500/10';
    pillText = 'text-emerald-300';
    pillBorder = 'border-emerald-500/40';
    PillIcon = Rocket;
    pillLabel = 'Ready → Phase 2 brake';
  } else if (rows_to_go === 0 && !brake_eligible) {
    pillLabel = `Win-rate ${(win_rate != null ? (win_rate * 100).toFixed(1) : '—')}% · need ${(min_win_rate_required * 100).toFixed(0)}%`;
  }

  const winRateStr = win_rate != null ? `${(win_rate * 100).toFixed(1)}%` : '—';
  const requiredPctStr = `${(min_win_rate_required * 100).toFixed(0)}%`;

  return (
    <div
      data-testid="equity-commander-shadow-chip"
      className={`rounded-xl border ${statusBorder} ${statusBg} p-3`}
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs uppercase tracking-wider">
        <div className={`flex items-center gap-2 ${statusText}`}>
          <Shield className="h-3.5 w-3.5" /> Equity Commander Shadow
        </div>
        <div className="flex items-center gap-2">
          <span
            data-testid="equity-commander-shadow-promotion-pill"
            title={blocker || pillLabel}
            className={`flex items-center gap-1 rounded-full border ${pillBorder} ${pillBg} px-2 py-0.5 text-[10px] font-semibold normal-case ${pillText}`}
          >
            <PillIcon className="h-3 w-3" />
            <span>{pillLabel}</span>
          </span>
          <span className={`${statusText} font-semibold`} data-testid="equity-commander-shadow-status">
            {statusLabel}
          </span>
          <span className="text-slate-500 text-[10px]">· {phase}</span>
          <StatusIcon className={`h-3.5 w-3.5 ${statusText}`} />
        </div>
      </div>

      {/* Warm-up notice — shown until scored rows hit the min floor. */}
      {rows_scored < min_rows_required && (
        <div
          data-testid="equity-commander-shadow-warmup"
          className="mb-2 rounded-md border border-slate-700/50 bg-slate-900/40 px-2 py-1.5 text-[11px] text-slate-400"
        >
          Warm-up: <span className="tabular-nums text-slate-200">{rows_scored}</span>
          {' / '}
          <span className="tabular-nums">{min_rows_required}</span>
          {' scored rows. '}
          <span className="text-slate-500">
            Phase 2 unlocks at {min_rows_required} scored rows
            {' '}AND win-rate ≥ {requiredPctStr}.
          </span>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <div data-testid="equity-commander-shadow-rows-total">
          <div className="text-2xl font-semibold tabular-nums text-white">
            {rows_total}
          </div>
          <div className="text-xs text-slate-500">rows total</div>
        </div>
        <div data-testid="equity-commander-shadow-rows-scored">
          <div className="text-2xl font-semibold tabular-nums text-white">
            {rows_scored}
          </div>
          <div className="text-xs text-slate-500">rows scored</div>
        </div>
        <div data-testid="equity-commander-shadow-win-rate">
          <div className="text-2xl font-semibold tabular-nums text-white">
            {winRateStr}
          </div>
          <div className="text-xs text-slate-500">
            win rate (≥ {requiredPctStr})
          </div>
        </div>
        <div data-testid="equity-commander-shadow-rows-to-go">
          <div className="text-2xl font-semibold tabular-nums text-white">
            {rows_to_go}
          </div>
          <div className="text-xs text-slate-500">rows to Phase 2</div>
        </div>
      </div>

      {blocker && (
        <div
          data-testid="equity-commander-shadow-blocker"
          className="mt-2 text-[11px] text-slate-400"
        >
          Blocker: <span className="text-slate-200">{blocker}</span>
        </div>
      )}
    </div>
  );
};

export default EquityCommanderShadowChip;
