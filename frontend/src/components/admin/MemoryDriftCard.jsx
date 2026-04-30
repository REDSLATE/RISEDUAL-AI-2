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
  const [history, setHistory] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [days, setDays] = useState(30);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [driftRes, historyRes] = await Promise.all([
        authFetch(`${API}/api/admin/memory/drift?days=${days}&top_skew=8`),
        authFetch(`${API}/api/admin/memory/drift/history?hours=24`),
      ]);
      if (!driftRes.ok) throw new Error(`HTTP ${driftRes.status}`);
      const json = await driftRes.json();
      setData(json);
      // History is best-effort — sparkline degrades gracefully when
      // the endpoint isn't available yet (fresh deploy with no
      // recorded ticks) or the response shape is unexpected.
      if (historyRes.ok) {
        const h = await historyRes.json();
        setHistory(h);
      } else {
        setHistory(null);
      }
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

      {/* Drift narrative — translates the raw `drift` number into
          plain English. Negative drift is the common confusing case
          (Chroma > Mongo), surfaced inline so the operator doesn't
          have to remember the sign convention from the footer note. */}
      <DriftNarrative
        drift={data.drift}
        driftPct={data.drift_pct}
        mongo={data.mongo_verified_count}
        chroma={data.chroma_episode_count}
        recommendation={data.recommendation}
      />

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

      {/* Drift trend sparkline (24h) */}
      <DriftSparkline history={history} thresholds={data.thresholds} />

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

      {/* Why this watcher exists — retroactive case study so an
          operator scrolling through a quiet dashboard understands
          what the alert envelope actually catches. The
          2026-04-21 toxic-spike incident is the canonical
          motivating bug; documenting it here keeps the
          institutional memory inside the tool itself. */}
      <details
        className="mt-3 pt-3 border-t border-slate-700/40 group"
        data-testid="memory-drift-retroactive"
      >
        <summary className="text-[9px] text-slate-500 uppercase tracking-wider cursor-pointer hover:text-slate-300 select-none">
          Why this watcher exists
          <span className="ml-1 text-slate-600 group-open:hidden">▸</span>
          <span className="ml-1 text-slate-600 hidden group-open:inline">▾</span>
        </summary>
        <div className="mt-2 px-3 py-2 bg-slate-900/40 border border-slate-700/40 rounded-md text-[10px] text-slate-300/90 leading-relaxed">
          <p className="mb-1.5">
            <span className="font-semibold text-slate-200">
              Retroactive case study — 2026-04-21 toxic-spike incident.
            </span>
          </p>
          <p>
            With the watcher running during that day, the per-date
            skew table would have shown{' '}
            <code className="text-amber-300 font-mono">
              mongo:&nbsp;412
            </code>{' '}
            /{' '}
            <code className="text-amber-300 font-mono">
              chroma:&nbsp;12
            </code>{' '}
            for that date, and the recommendation would have
            flipped to{' '}
            <span className="font-semibold text-red-300">
              rebuild
            </span>{' '}
            within 5 minutes of the first bad sync. The
            corruption that took weeks to surface visibly in
            downstream toxic-pattern reports would have been
            actionable on the same shift.
          </p>
        </div>
      </details>
    </Card>
  );
}

function DriftNarrative({ drift, driftPct, mongo, chroma, recommendation }) {
  // Build a one-sentence explainer keyed on the sign of `drift`.
  // The card already shows the raw numbers; this translates them
  // into the operator's mental model.
  let title;
  let body;
  let tone; // controls the left border color

  if (drift === 0) {
    tone = 'emerald';
    title = 'Mongo and Chroma are perfectly aligned';
    body = 'Every verified prediction has a matching ChromaDB episode.';
  } else if (drift > 0) {
    // POSITIVE drift = Mongo verified rows that didn't make it into
    // Chroma. This is the actionable case — sync silently dropped
    // rows.
    tone = recommendation === 'rebuild' ? 'red'
         : recommendation === 'investigate' ? 'amber'
         : 'amber';
    title = `Mongo has ${drift.toLocaleString()} more verified prediction${drift === 1 ? '' : 's'} than Chroma episodes`;
    body = recommendation === 'rebuild'
      ? `${driftPct}% of recent verified rows are missing from Chroma — sync has materially regressed. Run /api/accuracy/memory/rebuild-from-mongo and re-check.`
      : `${driftPct}% of recent verified rows aren't represented in Chroma. Review the per-date skew table below to localize the regression window.`;
  } else {
    // NEGATIVE drift = Chroma has more episodes than Mongo's verified
    // set. This is the COMMON case and historically caused operator
    // confusion ("why is the number negative? is something broken?").
    // The detector deliberately clamps drift_pct to 0% on this branch
    // because over-supply is not a corruption signal — it's
    // architectural by design.
    tone = 'emerald';
    const surplus = Math.abs(drift).toLocaleString();
    title = `Chroma has ${surplus} more episodes than Mongo's verified set`;
    body = "Expected — Chroma includes unverified training episodes from `memory_training_service`'s yfinance bulk-training that never had a matching Mongo prediction. The detector clamps negative drift to 0% because over-supply isn't a corruption signal.";
  }

  const toneClasses = {
    emerald: 'border-emerald-500/40 bg-emerald-500/5 text-emerald-200',
    amber: 'border-amber-500/40 bg-amber-500/5 text-amber-200',
    red: 'border-red-500/40 bg-red-500/5 text-red-200',
  }[tone];

  return (
    <div
      data-testid="memory-drift-narrative"
      className={`mb-3 px-3 py-2 border-l-2 rounded-r-md ${toneClasses}`}
    >
      <div className="text-[11px] font-semibold leading-tight mb-1">
        {title}
      </div>
      <div className="text-[10px] leading-relaxed text-slate-300/90">
        {body}
      </div>
    </div>
  );
}


function DriftSparkline({ history, thresholds }) {
  // Two empty states to handle separately:
  //
  // 1. NO data yet — fresh deploy with the watcher not yet ticked
  //    (or an unauthorized response). Render a tiny "warming up"
  //    note so the operator knows it's bootstrapping, not broken.
  //
  // 2. ENOUGH data — render the full sparkline.
  //
  // The 5-min watcher produces 12 samples/hour, 288/day. We
  // consider the chart "warm" at 24 samples (~2h) — enough to
  // see real trend rather than the seed-tick artifact.
  const points = history?.points;
  const SAMPLES_PER_HOUR = 12;
  const FULL_24H_SAMPLES = SAMPLES_PER_HOUR * 24; // 288
  const WARM_THRESHOLD = 24; // 2h of data

  if (!Array.isArray(points) || points.length === 0) {
    return (
      <div className="mb-3" data-testid="memory-drift-sparkline-warmup">
        <div className="text-[9px] text-slate-500 uppercase tracking-wider mb-1">
          Drift Trend · 24h
        </div>
        <div className="rounded-md border border-slate-700/40 bg-slate-900/40 p-2 text-[10px] text-slate-400 italic">
          Warming up — the 5-minute watcher hasn't ticked yet.
          Sparkline will populate at 12 samples/hour
          (288/day) within a few minutes.
        </div>
      </div>
    );
  }

  // Single-sample case — can't draw a meaningful line, but the
  // operator should still see *something* to confirm the watcher
  // ticked. Render a single dot + the warmup explainer.
  if (points.length === 1) {
    return (
      <div className="mb-3" data-testid="memory-drift-sparkline-warmup">
        <div className="flex items-baseline justify-between mb-1">
          <div className="text-[9px] text-slate-500 uppercase tracking-wider">
            Drift Trend · 24h
          </div>
          <span className="text-[9px] text-slate-500">
            1 sample · warming up
          </span>
        </div>
        <div className="rounded-md border border-slate-700/40 bg-slate-900/40 p-2 text-[10px] text-slate-400 italic">
          First sample recorded at{' '}
          <span className="font-mono tabular-nums text-slate-300">
            {Number(points[0].drift_pct ?? 0).toFixed(2)}%
          </span>
          . Need a second tick to start the trend curve — should
          arrive within 5 minutes.
        </div>
      </div>
    );
  }

  const values = points.map((p) => Number(p.drift_pct) || 0);
  const max = Math.max(...values, 1); // floor at 1% so a flat-zero
                                       // series still shows a baseline
  const min = 0;
  const width = 240;
  const height = 40;
  const stepX = points.length > 1 ? width / (points.length - 1) : 0;

  // Color tier from latest sample — matches the recommendation
  // classifier (ok/investigate/rebuild) so the sparkline reads
  // semantically without a legend.
  const latest = values[values.length - 1];
  const investigatePct = thresholds?.investigate_pct ?? 10;
  const okPct = thresholds?.ok_pct ?? 1;
  let stroke = '#34d399'; // emerald
  let fill = 'rgba(52, 211, 153, 0.15)';
  if (latest >= investigatePct) {
    stroke = '#f87171'; // red
    fill = 'rgba(248, 113, 113, 0.15)';
  } else if (latest >= okPct) {
    stroke = '#fbbf24'; // amber
    fill = 'rgba(251, 191, 36, 0.15)';
  }

  const norm = (v) => height - ((v - min) / (max - min || 1)) * height;
  const polyPoints = values
    .map((v, i) => `${(i * stepX).toFixed(2)},${norm(v).toFixed(2)}`)
    .join(' ');
  const areaPoints = `0,${height} ${polyPoints} ${width},${height}`;

  // Reference line at the rebuild threshold (only when within
  // chart range; otherwise hide so we don't waste pixels on it).
  const refY = investigatePct <= max ? norm(investigatePct) : null;

  // Drift trend label — first vs last sample
  const first = values[0];
  const delta = latest - first;
  const trendLabel =
    Math.abs(delta) < 0.05
      ? 'flat'
      : delta > 0
        ? `+${delta.toFixed(2)}pt`
        : `${delta.toFixed(2)}pt`;
  const trendColor =
    Math.abs(delta) < 0.05
      ? 'text-slate-400'
      : delta > 0
        ? 'text-amber-300'
        : 'text-emerald-300';

  return (
    <div className="mb-3" data-testid="memory-drift-sparkline">
      <div className="flex items-baseline justify-between mb-1">
        <div className="text-[9px] text-slate-500 uppercase tracking-wider">
          Drift Trend · 24h
        </div>
        <div className="flex items-baseline gap-2">
          <span className="text-[9px] text-slate-500">
            {points.length} sample{points.length === 1 ? '' : 's'}
            {points.length < FULL_24H_SAMPLES && ' / 288'}
          </span>
          <span
            className={`text-[10px] font-mono tabular-nums ${trendColor}`}
            data-testid="memory-drift-trend-delta"
          >
            {trendLabel}
          </span>
        </div>
      </div>
      <div className="rounded-md border border-slate-700/40 bg-slate-900/40 p-2">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          width="100%"
          height="40"
          preserveAspectRatio="none"
        >
          {refY !== null && (
            <line
              x1="0"
              x2={width}
              y1={refY}
              y2={refY}
              stroke="#f87171"
              strokeWidth="0.5"
              strokeDasharray="2,2"
              opacity="0.5"
            />
          )}
          <polygon points={areaPoints} fill={fill} />
          <polyline
            points={polyPoints}
            fill="none"
            stroke={stroke}
            strokeWidth="1.25"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
          <circle
            cx={width}
            cy={norm(latest)}
            r="2"
            fill={stroke}
          />
        </svg>
        <div className="flex justify-between text-[9px] text-slate-500 mt-1 font-mono tabular-nums">
          <span>{values[0].toFixed(2)}%</span>
          <span>peak {max.toFixed(2)}%</span>
          <span className="text-slate-300">{latest.toFixed(2)}%</span>
        </div>
      </div>
      {points.length < WARM_THRESHOLD && (
        <p
          className="text-[9px] text-slate-500 italic mt-1"
          data-testid="memory-drift-sparkline-warmup-note"
        >
          Series still warming up · 5-min watcher produces 12
          samples/hour · 24h view fully populated by tomorrow ·
          7-day TTL means the rolling window stabilizes after a
          week.
        </p>
      )}
    </div>
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
