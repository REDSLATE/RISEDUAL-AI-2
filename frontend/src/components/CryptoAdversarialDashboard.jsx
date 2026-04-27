import React, { useEffect, useState } from 'react';

const API = process.env.REACT_APP_BACKEND_URL;

/**
 * Crypto Adversarial Decision Dashboard
 *
 * Live tile for the Bull / Bear / Commander adversarial layer.
 * Polls /api/crypto/adversarial-stats every 60s and renders a single
 * banner answering: "is the layer learning?"
 *
 * Architecture:
 *  - Pure read-only — no admin actions, no buttons. Looking at it
 *    is the only valid mode until `actionable=true`.
 *  - Mirrors CryptoPaperDashboard.jsx auth + polling pattern.
 *  - When both gates closed (default state), shows a "DORMANT" status
 *    so admins can confirm the layer is silent on purpose.
 *
 * Promotion gates (the operator's only job here):
 *  shadow → risk_only       — when interpretation flips to bull/bear
 *                              dominance with N >= 15 per bucket.
 *  risk_only → veto         — when no_trade_avoided.avg_r_avoided
 *                              is solidly negative.
 *  veto → full              — same statistical bar as Tier 3 itself.
 */
export default function CryptoAdversarialDashboard() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [phaseFilter, setPhaseFilter] = useState('');   // '' = all phases
  const [hoursFilter, setHoursFilter] = useState('');   // '' = all-time

  async function load() {
    try {
      const params = new URLSearchParams();
      if (phaseFilter) params.set('phase', phaseFilter);
      if (hoursFilter) params.set('hours', hoursFilter);
      const qs = params.toString() ? `?${params}` : '';

      const res = await fetch(`${API}/api/crypto/adversarial-stats${qs}`, {
        credentials: 'include',
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setData(json);
      setError(null);
    } catch (e) {
      setError(String(e.message || e));
    }
  }

  useEffect(() => {
    load();
    const id = setInterval(load, 60000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phaseFilter, hoursFilter]);

  if (error) {
    return (
      <div
        className="rounded-xl border border-red-700/40 bg-red-950/30 p-5 text-red-300 text-sm"
        data-testid="adversarial-dashboard-error"
      >
        Adversarial stats unavailable: {error}
      </div>
    );
  }

  if (!data) {
    return (
      <div
        className="rounded-xl border border-slate-700 bg-slate-900/40 p-6 text-slate-500 text-sm"
        data-testid="adversarial-dashboard-loading"
      >
        Loading adversarial stats...
      </div>
    );
  }

  const isDormant = data.total_with_outcome === 0 && data.open_count === 0;

  return (
    <div className="space-y-4" data-testid="adversarial-dashboard">
      {/* Header banner — single source of truth on layer state. */}
      <StatusBanner data={data} isDormant={isDormant} />

      {/* Filter row */}
      <div className="flex items-center gap-3 text-xs">
        <span className="text-slate-500">Filter:</span>
        <select
          value={phaseFilter}
          onChange={(e) => setPhaseFilter(e.target.value)}
          className="bg-slate-900 border border-slate-700 text-slate-300 rounded px-2 py-1"
          data-testid="adversarial-phase-filter"
        >
          <option value="">All phases</option>
          <option value="shadow">Shadow only</option>
          <option value="risk_only">Risk-only</option>
          <option value="veto">Veto</option>
          <option value="full">Full</option>
        </select>
        <select
          value={hoursFilter}
          onChange={(e) => setHoursFilter(e.target.value)}
          className="bg-slate-900 border border-slate-700 text-slate-300 rounded px-2 py-1"
          data-testid="adversarial-hours-filter"
        >
          <option value="">All-time</option>
          <option value="24">Last 24h</option>
          <option value="168">Last 7 days</option>
          <option value="720">Last 30 days</option>
        </select>
        {data.window_hours ? (
          <span className="text-slate-600">window: {data.window_hours}h</span>
        ) : null}
      </div>

      {!isDormant && (
        <>
          {/* Win-rate spread + open-decision counter */}
          <WinRateGrid data={data} />

          {/* Per-decision-type breakdown */}
          <DecisionBreakdown by={data.by_decision} />

          {/* No-trade attribution + edge-gap distribution */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <NoTradeCard noTradeAvoided={data.no_trade_avoided} />
            <EdgeGapCard edgeGap={data.edge_gap} />
          </div>
        </>
      )}
    </div>
  );
}


// ── Sub-components ────────────────────────────────────────────────────────────


const StatusBanner = ({ data, isDormant }) => {
  if (isDormant) {
    return (
      <div
        className="rounded-xl border border-slate-700 bg-slate-900/60 p-5"
        data-testid="adversarial-status-dormant"
      >
        <div className="flex items-center gap-3">
          <span className="w-2.5 h-2.5 rounded-full bg-slate-500" />
          <span className="text-slate-300 font-semibold text-sm">
            DORMANT — Adversarial layer not yet active
          </span>
        </div>
        <p className="text-slate-500 text-xs mt-2 leading-relaxed">
          Both gates are closed by design. The layer activates when{' '}
          <code className="text-slate-400 bg-slate-800 px-1 rounded">CRYPTO_ADVERSARIAL_ENABLED=1</code>{' '}
          AND ML Tier 3 is unlocked. Until then, no decisions are logged
          and no compute is spent.
        </p>
      </div>
    );
  }

  // Active — pick a banner colour based on interpretation key.
  const interp = data.interpretation || 'insufficient_data_keep_observing';
  const variants = {
    insufficient_data_keep_observing: {
      tone: 'border-amber-700/40 bg-amber-950/30 text-amber-300',
      dot: 'bg-amber-400',
      head: 'OBSERVING — accumulating decisions',
    },
    balanced_keep_observing_or_tune_threshold: {
      tone: 'border-slate-600 bg-slate-900/60 text-slate-300',
      dot: 'bg-slate-400',
      head: 'BALANCED — Bull/Bear within ±0.10',
    },
    bull_dominates_check_for_long_bias_overfit: {
      tone: 'border-emerald-700/40 bg-emerald-950/30 text-emerald-300',
      dot: 'bg-emerald-400',
      head: 'BULL DOMINATES — verify not just a long-bias overfit',
    },
    bear_dominates_strong_signal_to_promote_to_risk_only: {
      tone: 'border-cyan-700/40 bg-cyan-950/30 text-cyan-300',
      dot: 'bg-cyan-400',
      head: 'BEAR DOMINATES — promotion candidate',
    },
    no_data: {
      tone: 'border-slate-700 bg-slate-900/40 text-slate-400',
      dot: 'bg-slate-500',
      head: 'NO DATA — winner field empty',
    },
  };
  const v = variants[interp] || variants.insufficient_data_keep_observing;

  return (
    <div
      className={`rounded-xl border p-5 ${v.tone}`}
      data-testid={`adversarial-status-${interp}`}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className={`w-2.5 h-2.5 rounded-full ${v.dot}`} />
          <span className="font-semibold text-sm">{v.head}</span>
        </div>
        <span
          className={`text-[10px] font-mono px-2 py-0.5 rounded ${
            data.actionable ? 'bg-emerald-900/40 text-emerald-300' : 'bg-slate-800 text-slate-400'
          }`}
        >
          {data.actionable ? 'ACTIONABLE' : 'NOT YET ACTIONABLE'}
        </span>
      </div>
      <p className="text-xs mt-2 opacity-75">
        {data.total_with_outcome} closed · {data.open_count} open ·
        min-bucket {data.min_bucket_count}/{data.min_bucket_samples_required}
      </p>
    </div>
  );
};


const WinRateGrid = ({ data }) => {
  const fmtRate = (r) => (r === null || r === undefined ? '—' : `${(r * 100).toFixed(1)}%`);
  const spread =
    data.bull_win_rate !== null && data.bear_win_rate !== null
      ? data.bull_win_rate - data.bear_win_rate
      : null;

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      <Stat
        label="Bull win rate"
        value={fmtRate(data.bull_win_rate)}
        subline={`n=${data.bull_total}`}
        accent="emerald"
        testid="bull-win-rate"
      />
      <Stat
        label="Bear win rate"
        value={fmtRate(data.bear_win_rate)}
        subline={`n=${data.bear_total}`}
        accent="cyan"
        testid="bear-win-rate"
      />
      <Stat
        label="Spread (bull − bear)"
        value={spread === null ? '—' : `${(spread * 100).toFixed(1)}%`}
        subline={
          spread === null
            ? 'awaiting data'
            : Math.abs(spread) >= 0.10
              ? 'meaningful — promotion signal'
              : 'within noise band'
        }
        accent={spread !== null && Math.abs(spread) >= 0.10 ? 'amber' : 'slate'}
        testid="bull-bear-spread"
      />
    </div>
  );
};


const DecisionBreakdown = ({ by }) => {
  const decisions = ['LONG', 'SHORT_OR_AVOID', 'NO_TRADE'];
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-900/40 overflow-hidden">
      <div className="px-4 py-2.5 bg-slate-800/40 border-b border-slate-700">
        <span className="text-slate-300 text-xs font-semibold uppercase tracking-wider">
          Per-decision breakdown
        </span>
      </div>
      <table className="w-full text-sm" data-testid="adversarial-decision-table">
        <thead>
          <tr className="text-slate-500 text-xs uppercase">
            <th className="text-left px-4 py-2">Decision</th>
            <th className="text-right px-4 py-2">Count</th>
            <th className="text-right px-4 py-2">Avg R</th>
            <th className="text-right px-4 py-2">Median R</th>
            <th className="text-right px-4 py-2">Win rate</th>
          </tr>
        </thead>
        <tbody>
          {decisions.map((d) => {
            const row = by[d] || { count: 0 };
            const fmt = (v, suffix = '') =>
              v === null || v === undefined ? '—' : `${v.toFixed ? v.toFixed(2) : v}${suffix}`;
            return (
              <tr
                key={d}
                className="border-t border-slate-800 text-slate-300"
                data-testid={`adversarial-row-${d.toLowerCase()}`}
              >
                <td className="px-4 py-2 font-mono text-xs">{d}</td>
                <td className="px-4 py-2 text-right">{row.count}</td>
                <td className="px-4 py-2 text-right">{fmt(row.avg_r)}</td>
                <td className="px-4 py-2 text-right">{fmt(row.median_r)}</td>
                <td className="px-4 py-2 text-right">
                  {row.win_rate === null || row.win_rate === undefined
                    ? '—'
                    : `${(row.win_rate * 100).toFixed(1)}%`}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};


const NoTradeCard = ({ noTradeAvoided }) => {
  const avg = noTradeAvoided?.avg_r_avoided;
  const count = noTradeAvoided?.count || 0;
  const verdict =
    avg === null || avg === undefined
      ? 'awaiting NO_TRADE decisions'
      : avg < 0
        ? 'Commander correctly avoiding losing trades'
        : avg > 0
          ? 'Commander would cost money in veto phase'
          : 'flat — no edge';
  const tone =
    avg === null || avg === undefined
      ? 'text-slate-400'
      : avg < 0
        ? 'text-emerald-400'
        : avg > 0
          ? 'text-red-400'
          : 'text-slate-400';

  return (
    <div
      className="rounded-xl border border-slate-700 bg-slate-900/40 p-4"
      data-testid="adversarial-no-trade-card"
    >
      <div className="text-slate-500 text-xs uppercase tracking-wider mb-2">
        NO_TRADE attribution
      </div>
      <div className="flex items-baseline gap-2">
        <span className={`text-3xl font-bold ${tone}`}>
          {avg === null || avg === undefined ? '—' : avg.toFixed(2) + 'R'}
        </span>
        <span className="text-slate-500 text-xs">avg avoided · n={count}</span>
      </div>
      <p className="text-slate-400 text-xs mt-2">{verdict}</p>
    </div>
  );
};


const EdgeGapCard = ({ edgeGap }) => {
  const fmt = (v) => (v === null || v === undefined ? '—' : v.toFixed(3));
  return (
    <div
      className="rounded-xl border border-slate-700 bg-slate-900/40 p-4"
      data-testid="adversarial-edge-gap-card"
    >
      <div className="text-slate-500 text-xs uppercase tracking-wider mb-2">
        Edge-gap distribution
      </div>
      <div className="grid grid-cols-3 gap-3 text-sm">
        <div>
          <div className="text-slate-300 font-mono">{fmt(edgeGap?.mean)}</div>
          <div className="text-slate-500 text-[10px]">mean</div>
        </div>
        <div>
          <div className="text-slate-300 font-mono">{fmt(edgeGap?.min)}</div>
          <div className="text-slate-500 text-[10px]">min</div>
        </div>
        <div>
          <div className="text-slate-300 font-mono">{fmt(edgeGap?.max)}</div>
          <div className="text-slate-500 text-[10px]">max</div>
        </div>
      </div>
      <p className="text-slate-400 text-[10px] mt-2 leading-relaxed">
        Threshold today: ±0.35. If most gaps cluster near 0, the resolver
        rarely fires — tune EDGE_GAP_THRESHOLD down.
      </p>
    </div>
  );
};


const Stat = ({ label, value, subline, accent = 'slate', testid }) => {
  const tones = {
    emerald: 'text-emerald-400',
    cyan: 'text-cyan-400',
    amber: 'text-amber-400',
    slate: 'text-slate-200',
  };
  return (
    <div
      className="rounded-xl border border-slate-700 bg-slate-900/40 p-4"
      data-testid={`adversarial-stat-${testid}`}
    >
      <div className="text-slate-500 text-xs uppercase tracking-wider">{label}</div>
      <div className={`text-3xl font-bold mt-2 ${tones[accent] || tones.slate}`}>{value}</div>
      <div className="text-slate-500 text-[10px] mt-1">{subline}</div>
    </div>
  );
};
