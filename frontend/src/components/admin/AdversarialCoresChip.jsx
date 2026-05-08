import React, { useEffect, useState, useCallback } from 'react';
import { Swords, Activity, Pause, Hourglass, Rocket } from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const API = `${getApiBase()}/api`;

/**
 * Adversarial Cores · 24h chip + promotion pill.
 *
 * Reads two endpoints every 30s:
 *   - GET /api/admin/adversarial-cores/24h         → activity rollup
 *   - GET /api/admin/adversarial-cores/promotion   → lifetime readiness
 *
 * Renders:
 *   - Phase + enabled traffic-light (LEARNING/IDLE/OFF)
 *   - Promotion pill (READY / N rows to go / TERMINAL) inside the header
 *   - Warm-up notice when closed_lifetime < trustworthy_min_closed
 *   - 24h decision counts, avg edge_gap, last decision age
 *   - Bull/Bear avg conf, win counts, lifetime win-rate spread (only
 *     once trustworthy)
 *   - Copy-pastable env line + reason when ready_to_promote=true
 */
const AdversarialCoresChip = () => {
  const [data, setData] = useState(null);
  const [promotion, setPromotion] = useState(null);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      const [r1, r2] = await Promise.all([
        authFetch(`${API}/admin/adversarial-cores/24h`),
        authFetch(`${API}/admin/adversarial-cores/promotion`),
      ]);
      if (!r1.ok) throw new Error(`24h HTTP ${r1.status}`);
      if (!r2.ok) throw new Error(`promotion HTTP ${r2.status}`);
      setData(await r1.json());
      setPromotion(await r2.json());
      setError(null);
    } catch (e) {
      setError(e.message || 'fetch failed');
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, 30_000);
    return () => clearInterval(id);
  }, [fetchAll]);

  const onCopyEnv = async () => {
    if (!promotion?.promote_env_line) return;
    try {
      await navigator.clipboard.writeText(promotion.promote_env_line);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked — silent no-op, the line is still visible */
    }
  };

  if (error) {
    return (
      <div
        className="rounded-xl border border-rose-500/30 bg-rose-500/5 p-3 text-xs text-rose-300"
        data-testid="adversarial-cores-error"
      >
        Adversarial Cores error: {error}
      </div>
    );
  }

  if (!data) return null;

  const {
    enabled, phase, decisions_24h, closed_24h, decision_counts, wins,
    avg_edge_gap, avg_bull_confidence, avg_bear_confidence,
    bull_win_rate, bear_win_rate, last_decision_at,
  } = data;

  // Status traffic-light
  let statusBorder = 'border-slate-700/60';
  let statusBg = 'bg-slate-800/30';
  let statusText = 'text-slate-300';
  let StatusIcon = Pause;
  let statusLabel = 'OFF';

  if (enabled && decisions_24h > 0) {
    statusBorder = 'border-emerald-500/40';
    statusBg = 'bg-emerald-500/5';
    statusText = 'text-emerald-300';
    StatusIcon = Activity;
    statusLabel = 'LEARNING';
  } else if (enabled) {
    statusBorder = 'border-amber-500/30';
    statusBg = 'bg-amber-500/5';
    statusText = 'text-amber-300';
    StatusIcon = Activity;
    statusLabel = 'IDLE';
  }

  const lastRunLabel = last_decision_at
    ? `${Math.max(0, Math.floor((Date.now() - new Date(last_decision_at).getTime()) / 60_000))}m ago`
    : '—';

  // Lifetime spread (preferred) falls back to 24h spread when promotion
  // payload missing — keeps the chip backwards-compatible.
  const spreadValue =
    promotion?.spread_lifetime_pp != null
      ? promotion.spread_lifetime_pp
      : bull_win_rate != null && bear_win_rate != null
        ? Number(((bull_win_rate - bear_win_rate) * 100).toFixed(1))
        : null;
  const trustworthy = promotion?.trustworthy === true;
  const closedLifetime = promotion?.closed_lifetime ?? 0;
  const trustworthyMin = promotion?.trustworthy_min_closed ?? 20;
  const rowsToTrustworthy = promotion?.rows_to_trustworthy ?? trustworthyMin;
  const showSpread = spreadValue !== null && trustworthy;

  // Promotion pill state
  const ready = promotion?.ready_to_promote === true;
  const terminal = promotion?.next_transition === null;

  let pillBg = 'bg-slate-800/60';
  let pillText = 'text-slate-400';
  let pillBorder = 'border-slate-700/60';
  let PillIcon = Hourglass;
  let pillLabel = 'Accumulating';

  if (ready) {
    pillBg = 'bg-emerald-500/10';
    pillText = 'text-emerald-300';
    pillBorder = 'border-emerald-500/40';
    PillIcon = Rocket;
    pillLabel = `Ready → ${promotion?.next_phase}`;
  } else if (terminal) {
    pillBg = 'bg-violet-500/10';
    pillText = 'text-violet-300';
    pillBorder = 'border-violet-500/40';
    PillIcon = Rocket;
    pillLabel = 'TERMINAL · full';
  } else if (promotion) {
    // Show progress toward the next transition's row floor.
    const nxt = promotion.thresholds?.[promotion.next_transition];
    const minRows = nxt?.min_closed ?? 20;
    const remaining = Math.max(0, minRows - closedLifetime);
    pillLabel = remaining > 0
      ? `${remaining} rows to ${promotion.next_phase}`
      : 'Awaiting rate floor';
  }

  return (
    <div
      data-testid="adversarial-cores-chip"
      className={`rounded-xl border ${statusBorder} ${statusBg} p-3`}
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs uppercase tracking-wider">
        <div className={`flex items-center gap-2 ${statusText}`}>
          <Swords className="h-3.5 w-3.5" /> Adversarial Cores · 24h
        </div>
        <div className="flex items-center gap-2">
          <span
            data-testid="adversarial-cores-promotion-pill"
            title={promotion?.blocker || pillLabel}
            className={`flex items-center gap-1 rounded-full border ${pillBorder} ${pillBg} px-2 py-0.5 text-[10px] font-semibold normal-case ${pillText}`}
          >
            <PillIcon className="h-3 w-3" />
            <span>{pillLabel}</span>
          </span>
          <span className={`${statusText} font-semibold`} data-testid="adversarial-cores-status">
            {statusLabel}
          </span>
          <span className="text-slate-500 text-[10px]">· {phase}</span>
          <StatusIcon className={`h-3.5 w-3.5 ${statusText}`} />
        </div>
      </div>

      {/* Warm-up notice — shown until lifetime closed rows hit the
          trustworthy floor. Replaces the (otherwise-misleading)
          spread display below. */}
      {!trustworthy && closedLifetime < trustworthyMin && (
        <div
          data-testid="adversarial-cores-warmup"
          className="mb-2 rounded-md border border-slate-700/50 bg-slate-900/40 px-2 py-1.5 text-[11px] text-slate-400"
        >
          Warm-up: <span className="tabular-nums text-slate-200">{closedLifetime}</span>
          {' / '}
          <span className="tabular-nums">{trustworthyMin}</span>
          {' closed rows. '}
          <span className="text-slate-500">
            Bull−Bear spread will surface in {rowsToTrustworthy} more closed row
            {rowsToTrustworthy === 1 ? '' : 's'}.
          </span>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <div data-testid="adversarial-cores-decisions">
          <div className="text-2xl font-semibold tabular-nums text-white">
            {decisions_24h}
          </div>
          <div className="text-xs text-slate-500">decisions 24h</div>
        </div>
        <div data-testid="adversarial-cores-closed">
          <div className="text-2xl font-semibold tabular-nums text-white">
            {closed_24h}
          </div>
          <div className="text-xs text-slate-500">closed</div>
        </div>
        <div data-testid="adversarial-cores-edge-gap">
          <div className="text-2xl font-semibold tabular-nums text-white">
            {avg_edge_gap?.toFixed?.(3) ?? '—'}
          </div>
          <div className="text-xs text-slate-500">avg edge_gap</div>
        </div>
        <div data-testid="adversarial-cores-last-run">
          <div className="text-sm font-semibold text-white">{lastRunLabel}</div>
          <div className="text-xs text-slate-500">last decision</div>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 text-[11px]">
        <div className="rounded-md bg-slate-900/30 p-2">
          <div className="flex items-center justify-between text-slate-400">
            <span>Bull</span>
            <span className="tabular-nums text-emerald-300">
              {avg_bull_confidence?.toFixed?.(2) ?? '—'}
            </span>
          </div>
          <div className="text-slate-500">
            wins <span className="tabular-nums text-slate-300">{wins?.bull ?? 0}</span>
            {bull_win_rate != null && (
              <span className="ml-2">
                · wr <span className="tabular-nums text-slate-300">
                  {(bull_win_rate * 100).toFixed(0)}%
                </span>
              </span>
            )}
          </div>
        </div>
        <div className="rounded-md bg-slate-900/30 p-2">
          <div className="flex items-center justify-between text-slate-400">
            <span>Bear</span>
            <span className="tabular-nums text-rose-300">
              {avg_bear_confidence?.toFixed?.(2) ?? '—'}
            </span>
          </div>
          <div className="text-slate-500">
            wins <span className="tabular-nums text-slate-300">{wins?.bear ?? 0}</span>
            {bear_win_rate != null && (
              <span className="ml-2">
                · wr <span className="tabular-nums text-slate-300">
                  {(bear_win_rate * 100).toFixed(0)}%
                </span>
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-500">
        <div data-testid="adversarial-cores-counts">
          L <span className="text-slate-300 tabular-nums">{decision_counts?.LONG ?? 0}</span>
          {' · '}
          S/A <span className="text-slate-300 tabular-nums">
            {decision_counts?.SHORT_OR_AVOID ?? 0}
          </span>
          {' · '}
          NT <span className="text-slate-300 tabular-nums">
            {decision_counts?.NO_TRADE ?? 0}
          </span>
        </div>
        {showSpread && (
          <div data-testid="adversarial-cores-spread">
            Bull−Bear spread{' '}
            <span className={`tabular-nums ${Number(spreadValue) >= 0 ? 'text-emerald-300' : 'text-rose-300'}`}>
              {spreadValue}pp
            </span>
            <span className="ml-1 text-slate-600">· lifetime</span>
          </div>
        )}
      </div>

      {/* Ready-to-promote drawer — only when the gate flips green. */}
      {ready && promotion?.promote_env_line && (
        <div
          data-testid="adversarial-cores-promote-cta"
          className="mt-3 rounded-md border border-emerald-500/30 bg-emerald-500/5 p-2 text-[11px]"
        >
          <div className="mb-1 flex items-center justify-between text-emerald-300">
            <span className="font-semibold">Ready to promote</span>
            <span className="text-emerald-400/70">
              correct {promotion.commander_correct_rate != null
                ? `${(promotion.commander_correct_rate * 100).toFixed(0)}%`
                : '—'}
              {' · '}
              {promotion.closed_lifetime} rows
            </span>
          </div>
          <div className="flex items-center justify-between gap-2">
            <code
              className="flex-1 truncate rounded bg-slate-950/60 px-2 py-1 text-emerald-200"
              data-testid="adversarial-cores-promote-env"
            >
              {promotion.promote_env_line}
            </code>
            <button
              type="button"
              onClick={onCopyEnv}
              data-testid="adversarial-cores-promote-copy"
              className="rounded bg-emerald-500/20 px-2 py-1 font-medium text-emerald-300 hover:bg-emerald-500/30"
            >
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
          <div className="mt-1 text-slate-500">
            Paste into <code>backend/.env</code>, then{' '}
            <code>sudo supervisorctl restart backend</code>.
          </div>
        </div>
      )}
    </div>
  );
};

export default AdversarialCoresChip;
