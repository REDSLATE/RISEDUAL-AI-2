import React from 'react';
import { Newspaper, Activity } from 'lucide-react';

/**
 * Inline-SVG sparkline for the burn-in admin panel.
 *
 * Renders two stacked sparklines (Benzinga + Alpha Vantage) over the
 * last N hours of catalyst_events ingestion. Pure SVG — no chart
 * library — so the bundle stays small and the chip renders even when
 * the rest of the burn-in card is loading.
 *
 * Uses an explicit max-of-both for the y-axis so an idle source
 * doesn't visually dwarf the active one.
 */
const Sparkline = ({ values, color, ariaLabel }) => {
  const w = 220;
  const h = 32;
  const max = Math.max(1, ...values);
  const step = values.length > 1 ? w / (values.length - 1) : w;

  const path = values
    .map((v, i) => {
      const x = i * step;
      const y = h - (v / max) * h;
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(' ');

  // Area fill — same shape but closed back to the baseline.
  const area = `${path} L${(values.length - 1) * step} ${h} L0 ${h} Z`;

  return (
    <svg
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-label={ariaLabel}
      data-testid={`sparkline-${ariaLabel.toLowerCase().replace(/\s+/g, '-')}`}
    >
      <path d={area} fill={color} fillOpacity={0.15} stroke="none" />
      <path d={path} fill="none" stroke={color} strokeWidth={1.5} />
    </svg>
  );
};

const Row = ({ label, total, current, values, color, Icon }) => (
  <div
    className="flex items-center gap-3 rounded-lg border border-slate-700/60 bg-slate-900/40 px-3 py-2"
    data-testid={`ingestion-row-${label.toLowerCase().replace(/\s+/g, '-')}`}
  >
    <div className="flex min-w-[140px] items-center gap-2">
      <Icon className="h-3.5 w-3.5 text-slate-400" />
      <div>
        <div className="text-xs uppercase tracking-wider text-slate-300">{label}</div>
        <div className="text-[10px] text-slate-500">
          24 h: <span className="tabular-nums text-slate-300">{total}</span>
          {' · now: '}
          <span className="tabular-nums text-slate-300">{current}</span>
        </div>
      </div>
    </div>
    <div className="flex-1">
      <Sparkline values={values} color={color} ariaLabel={label} />
    </div>
  </div>
);

const IngestionSparkline = ({ data }) => {
  if (!data || !Array.isArray(data.benzinga) || !Array.isArray(data.alpha_vantage)) {
    return null;
  }

  const benz = data.benzinga;
  const av = data.alpha_vantage;
  const totals = data.totals || {};
  const current = data.current_hour || {};

  // Empty-state safeguard — if both arrays are all-zero, render a
  // muted "no ingestion in window" line rather than two flat zeros
  // that look identical to a render bug.
  const allZero =
    (benz.every((v) => !v) && av.every((v) => !v));

  return (
    <div
      data-testid="ingestion-sparkline"
      className="rounded-xl border border-slate-700/60 bg-slate-800/40 p-3"
    >
      <div className="mb-2 flex items-center justify-between text-xs uppercase tracking-wider text-slate-400">
        <div className="flex items-center gap-2">
          <Activity className="h-3.5 w-3.5" /> Ingestion rate · last {data.hours ?? 24}h
        </div>
        <span className="text-[10px] text-slate-500">articles / hour</span>
      </div>

      {allZero ? (
        <div
          className="rounded-md border border-slate-700/60 bg-slate-900/30 px-3 py-4 text-center text-xs text-slate-500"
          data-testid="ingestion-empty"
        >
          No ingestion in the last {data.hours ?? 24}h. Feeders may be paused or
          rate-limited.
        </div>
      ) : (
        <div className="space-y-2">
          <Row
            label="Benzinga"
            total={totals.benzinga ?? 0}
            current={current.benzinga ?? 0}
            values={benz}
            color="#34d399"
            Icon={Newspaper}
          />
          <Row
            label="Alpha Vantage"
            total={totals.alpha_vantage ?? 0}
            current={current.alpha_vantage ?? 0}
            values={av}
            color="#60a5fa"
            Icon={Newspaper}
          />
        </div>
      )}
    </div>
  );
};

export default IngestionSparkline;
