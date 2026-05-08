import React from 'react';
import { Coins, AlertTriangle, ShieldOff } from 'lucide-react';

/**
 * Compact summary card for the Kraken xStock shadow-compare lane on
 * the burn-in admin page. Renders inline with the rest of the
 * NEWS_SHOCK chips — same visual grammar (rounded-xl, traffic-light
 * border colour, label/value/sub stack).
 *
 * Status logic (mirrors the operator spec):
 *   - disabled (gray):  KRAKEN_SHADOW_ENABLED is unset / off.
 *   - empty   (gray):   feature on but no rows today.
 *   - clean   (green):  rows present, no divergent symbols.
 *   - warn    (yellow): some divergent symbols, none breached threshold
 *                       OR some breaches happened off-hours (no alert fires).
 *   - alert   (red):    one or more alerts fired today.
 */
const KrakenShadowChip = ({ data }) => {
  if (!data) return null;

  const enabled        = data.enabled ?? false;
  const rows           = data.rows ?? 0;
  const maxBps         = data.max_bps ?? 0;
  const p95Bps         = data.p95_bps ?? 0;
  const divergent      = data.divergent_count ?? 0;
  const alerts         = data.alerts_fired ?? 0;
  const threshold      = data.threshold_bps ?? 50;
  const lastRun        = data.last_run_at;
  const sessions       = data.session_counts || {};

  const lastRunLabel = lastRun
    ? `${Math.max(0, Math.floor((Date.now() - new Date(lastRun).getTime()) / 60_000))}m ago`
    : 'never';

  let status   = 'gray';
  let summary  = 'disabled';
  let Icon     = ShieldOff;
  if (!enabled) {
    status  = 'gray';
    summary = 'off — opt in via KRAKEN_SHADOW_ENABLED=1';
  } else if (rows === 0) {
    status  = 'gray';
    summary = 'no rows yet today';
    Icon    = Coins;
  } else if (alerts > 0) {
    status  = 'red';
    summary = `${alerts} alert${alerts === 1 ? '' : 's'} fired`;
    Icon    = AlertTriangle;
  } else if (divergent > 0) {
    status  = 'yellow';
    summary = `${divergent} symbol${divergent === 1 ? '' : 's'} > ${threshold} bps`;
    Icon    = AlertTriangle;
  } else {
    status  = 'green';
    summary = 'clean';
    Icon    = Coins;
  }

  const colours = {
    green:  'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
    yellow: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
    red:    'border-rose-500/40 bg-rose-500/10 text-rose-300',
    gray:   'border-slate-600/60 bg-slate-700/40 text-slate-300',
  };

  return (
    <div
      data-testid="kraken-shadow-chip"
      className={`rounded-xl border p-3 ${colours[status]}`}
    >
      <div className="mb-2 flex items-center justify-between text-xs uppercase tracking-wider">
        <div className="flex items-center gap-2">
          <Icon className="h-3.5 w-3.5" /> Kraken xStock shadow
        </div>
        <span className="opacity-60">{lastRunLabel}</span>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <div data-testid="kraken-shadow-rows">
          <div className="text-2xl font-semibold tabular-nums">{rows}</div>
          <div className="text-xs opacity-60">rows today</div>
        </div>
        <div data-testid="kraken-shadow-max-bps">
          <div className="text-2xl font-semibold tabular-nums">{maxBps.toFixed(1)}</div>
          <div className="text-xs opacity-60">max bps</div>
        </div>
        <div data-testid="kraken-shadow-p95-bps">
          <div className="text-2xl font-semibold tabular-nums">{p95Bps.toFixed(1)}</div>
          <div className="text-xs opacity-60">p95 bps</div>
        </div>
        <div data-testid="kraken-shadow-divergent">
          <div className="text-2xl font-semibold tabular-nums">{divergent}</div>
          <div className="text-xs opacity-60">{`> ${threshold} bps`}</div>
        </div>
      </div>

      <div className="mt-2 flex items-center justify-between text-xs opacity-70">
        <span data-testid="kraken-shadow-summary">{summary}</span>
        <span>
          {Object.entries(sessions)
            .map(([k, v]) => `${k}:${v}`)
            .join(' · ') || ''}
        </span>
      </div>
    </div>
  );
};

export default KrakenShadowChip;
