import React, { useEffect, useState, useCallback } from 'react';

const API = process.env.REACT_APP_BACKEND_URL;

/**
 * Shadow Accuracy Panel — disagreement-conditional metrics for the
 * Research Shadow framework.
 *
 * Architecture
 * ------------
 * Pure read-only — no admin actions. Three numbers per (engine,
 * asset_type) bucket:
 *
 *   1. dissent_count      — how many times shadow disagreed with active
 *   2. win_rate           — of scored dissents, what fraction did
 *                           shadow's hypothetical fill BEAT active's
 *                           realised P&L (the only number that earns
 *                           an engine a Tier-3 promotion)
 *   3. total_$_delta      — cumulative $ value shadow added/subtracted
 *
 * Plus an "actionable" badge: green pill once `scored_dissent_count
 * >= 30` (the maturity guardrail). Below that threshold the win rate
 * is statistical noise and the operator should not make decisions
 * on it.
 *
 * Below the bucket grid, a cost-budget strip shows per-bot rolling
 * 24h LLM spend with full / degraded / paused tier classification.
 * Reuses the same poll cadence (30s) — banner colours flip in real
 * time as bots cross thresholds.
 *
 * Polls every 30s. Mirrors CryptoAdversarialDashboard's auth +
 * empty-state conventions.
 */
export default function ShadowAccuracyPanel() {
  const [stats, setStats] = useState(null);
  const [budget, setBudget] = useState(null);
  const [error, setError] = useState(null);
  const [hoursFilter, setHoursFilter] = useState('');

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (hoursFilter) params.set('hours', hoursFilter);
      const qs = params.toString() ? `?${params}` : '';

      const [statsRes, budgetRes] = await Promise.all([
        fetch(`${API}/api/admin/shadow/stats${qs}`, { credentials: 'include' }),
        fetch(`${API}/api/admin/shadow/cost-budget`, { credentials: 'include' }),
      ]);
      if (!statsRes.ok) throw new Error(`stats HTTP ${statsRes.status}`);
      if (!budgetRes.ok) throw new Error(`budget HTTP ${budgetRes.status}`);
      const [statsJson, budgetJson] = await Promise.all([
        statsRes.json(), budgetRes.json(),
      ]);
      setStats(statsJson);
      setBudget(budgetJson);
      setError(null);
    } catch (e) {
      setError(String(e.message || e));
    }
  }, [hoursFilter]);

  useEffect(() => {
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, [load]);

  if (error) {
    return (
      <div
        className="rounded-xl border border-red-700/40 bg-red-950/30 p-5 text-red-300 text-sm"
        data-testid="shadow-accuracy-error"
      >
        Shadow stats unavailable: {error}
      </div>
    );
  }

  if (!stats || !budget) {
    return (
      <div
        className="rounded-xl border border-slate-700 bg-slate-900/40 p-6 text-slate-500 text-sm"
        data-testid="shadow-accuracy-loading"
      >
        Loading shadow stats…
      </div>
    );
  }

  const buckets = stats.buckets || [];
  const isDormant = buckets.length === 0;

  return (
    <div className="space-y-4" data-testid="shadow-accuracy-panel">
      <Header isDormant={isDormant} hoursFilter={hoursFilter} setHoursFilter={setHoursFilter} />
      {isDormant ? (
        <DormantBanner />
      ) : (
        <BucketGrid buckets={buckets} minSamples={stats.min_dissent_samples_required} />
      )}
      <CostBudgetStrip budget={budget} />
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

const Header = ({ isDormant, hoursFilter, setHoursFilter }) => (
  <div className="flex items-center justify-between gap-3 text-xs">
    <div className="flex items-center gap-2">
      <span className={`w-2.5 h-2.5 rounded-full ${isDormant ? 'bg-slate-500' : 'bg-cyan-400'}`} />
      <span className="text-slate-300 font-semibold text-sm uppercase tracking-wider">
        Research Shadow — Disagreement Accuracy
      </span>
    </div>
    <select
      value={hoursFilter}
      onChange={(e) => setHoursFilter(e.target.value)}
      className="bg-slate-900 border border-slate-700 text-slate-300 rounded px-2 py-1"
      data-testid="shadow-hours-filter"
    >
      <option value="">All-time</option>
      <option value="24">Last 24h</option>
      <option value="168">Last 7 days</option>
      <option value="720">Last 30 days</option>
    </select>
  </div>
);

const DormantBanner = () => (
  <div
    className="rounded-xl border border-slate-700 bg-slate-900/60 p-5"
    data-testid="shadow-dormant-banner"
  >
    <div className="text-slate-300 font-semibold text-sm mb-2">
      DORMANT — no shadow decisions logged yet
    </div>
    <p className="text-slate-500 text-xs leading-relaxed">
      Set <code className="text-slate-400 bg-slate-800 px-1 rounded">CRYPTO_RESEARCH_SHADOW_ENGINE=adversarial</code>{' '}
      (crypto fleet) or{' '}
      <code className="text-slate-400 bg-slate-800 px-1 rounded">bot.shadow_engine = "council"</code>{' '}
      (per equity bot) to start observation. Tier-3 firewall enforced —
      shadow writes only to <code className="text-slate-400">research_shadow_decisions</code>.
    </p>
  </div>
);

const BucketGrid = ({ buckets, minSamples }) => (
  <div className="grid grid-cols-1 lg:grid-cols-2 gap-3" data-testid="shadow-bucket-grid">
    {buckets.map((b) => (
      <BucketCard key={`${b.shadow_engine}-${b.asset_type}`} bucket={b} minSamples={minSamples} />
    ))}
  </div>
);

const BucketCard = ({ bucket, minSamples }) => {
  const winPct = bucket.win_rate !== null && bucket.win_rate !== undefined
    ? `${(bucket.win_rate * 100).toFixed(1)}%`
    : '—';
  const deltaSign = (bucket.total_delta_usd || 0) >= 0 ? '+' : '';
  const deltaTone = (bucket.total_delta_usd || 0) > 0
    ? 'text-emerald-300'
    : (bucket.total_delta_usd || 0) < 0
      ? 'text-rose-300'
      : 'text-slate-400';

  return (
    <div
      className="rounded-xl border border-slate-700 bg-slate-900/60 p-4"
      data-testid={`shadow-bucket-${bucket.shadow_engine}-${bucket.asset_type}`}
    >
      {/* Bucket header */}
      <div className="flex items-center justify-between mb-3">
        <div className="text-slate-200 font-semibold text-sm uppercase tracking-wider">
          {bucket.shadow_engine} · {bucket.asset_type}
        </div>
        <ActionableBadge actionable={bucket.actionable} scored={bucket.scored_dissent_count} minSamples={minSamples} />
      </div>

      {/* Three primary columns */}
      <div className="grid grid-cols-3 gap-3 text-center">
        <Metric label="Dissents" value={bucket.dissent_count} sub={`${bucket.scored_dissent_count} scored`} />
        <Metric
          label="Win rate"
          value={winPct}
          sub={bucket.actionable ? 'actionable' : 'too few samples'}
          valueTone={bucket.actionable ? 'text-cyan-300' : 'text-slate-500'}
        />
        <Metric
          label="Δ $ total"
          value={`${deltaSign}$${(bucket.total_delta_usd || 0).toFixed(2)}`}
          sub="after fill costs"
          valueTone={deltaTone}
        />
      </div>

      {/* Footnote: total decisions for context */}
      <div className="text-slate-600 text-xs mt-3 pt-2 border-t border-slate-800">
        {bucket.total_decisions} cycles observed · {bucket.dissent_count} dissents (
        {bucket.total_decisions > 0
          ? ((bucket.dissent_count / bucket.total_decisions) * 100).toFixed(0)
          : '0'}
        % disagreement rate)
      </div>
    </div>
  );
};

const Metric = ({ label, value, sub, valueTone = 'text-slate-100' }) => (
  <div>
    <div className="text-slate-500 text-xs uppercase tracking-wider mb-1">{label}</div>
    <div className={`text-xl font-bold ${valueTone}`}>{value}</div>
    <div className="text-slate-600 text-xs mt-0.5">{sub}</div>
  </div>
);

const ActionableBadge = ({ actionable, scored, minSamples }) => {
  if (actionable) {
    return (
      <span
        className="px-2 py-0.5 rounded-full text-[10px] uppercase tracking-wider font-semibold border border-emerald-500/40 bg-emerald-950/40 text-emerald-300"
        data-testid="shadow-actionable-badge"
      >
        Actionable
      </span>
    );
  }
  const remaining = Math.max(0, (minSamples || 30) - (scored || 0));
  return (
    <span
      className="px-2 py-0.5 rounded-full text-[10px] uppercase tracking-wider font-semibold border border-slate-700 bg-slate-900/60 text-slate-400"
      data-testid="shadow-pending-badge"
    >
      Need {remaining} more
    </span>
  );
};

const CostBudgetStrip = ({ budget }) => {
  const bots = budget.bots || [];
  const ceiling = budget.ceiling_usd_per_day || 0;

  // Pick the worst tier across all bots — that's what the banner tone
  // reflects (one paused bot pages the operator faster than five
  // healthy ones look reassuring).
  const worstTier = bots.reduce((acc, b) => {
    if (b.tier === 'paused') return 'paused';
    if (b.tier === 'degraded' && acc !== 'paused') return 'degraded';
    return acc;
  }, 'full');

  const banner = {
    paused: { tone: 'border-rose-700/40 bg-rose-950/30 text-rose-300', label: 'BUDGET EXHAUSTED — shadows paused' },
    degraded: { tone: 'border-amber-700/40 bg-amber-950/30 text-amber-300', label: 'DEGRADED — entry/exit only' },
    full: { tone: 'border-slate-700 bg-slate-900/60 text-slate-300', label: 'WITHIN BUDGET' },
  }[worstTier];

  return (
    <div className={`rounded-xl border p-4 ${banner.tone}`} data-testid="shadow-cost-budget-strip">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs uppercase tracking-wider font-semibold">{banner.label}</span>
        <span className="text-xs text-slate-500">
          ceiling ${ceiling.toFixed(2)}/day · degraded @ {((budget.degraded_frac || 0.8) * 100).toFixed(0)}%
        </span>
      </div>
      {bots.length === 0 ? (
        <div className="text-slate-600 text-xs">No LLM-backed shadows running yet (Adversarial v1 is free; Council v1 is rule-based).</div>
      ) : (
        <table className="w-full text-xs" data-testid="shadow-cost-budget-table">
          <thead>
            <tr className="text-slate-500 uppercase tracking-wider text-[10px] border-b border-slate-800">
              <th className="text-left py-1">Bot</th>
              <th className="text-left py-1">Engine</th>
              <th className="text-right py-1">24h cycles</th>
              <th className="text-right py-1">24h spend</th>
              <th className="text-right py-1">Tier</th>
            </tr>
          </thead>
          <tbody>
            {bots.map((b) => (
              <tr
                key={`${b.bot_id}-${b.engine}`}
                className="border-b border-slate-900"
                data-testid={`shadow-cost-row-${b.bot_id}`}
              >
                <td className="py-1 text-slate-300">{b.bot_id}</td>
                <td className="py-1 text-slate-400">{b.engine}</td>
                <td className="py-1 text-right text-slate-300">{b.decisions_24h}</td>
                <td className="py-1 text-right text-slate-300">${b.spend_24h_usd.toFixed(4)}</td>
                <td className="py-1 text-right">
                  <TierPill tier={b.tier} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
};

const TierPill = ({ tier }) => {
  const colors = {
    paused: 'border-rose-500/40 bg-rose-950/40 text-rose-300',
    degraded: 'border-amber-500/40 bg-amber-950/40 text-amber-300',
    full: 'border-emerald-500/40 bg-emerald-950/40 text-emerald-300',
  }[tier] || 'border-slate-700 bg-slate-900 text-slate-400';
  return (
    <span className={`px-2 py-0.5 rounded-full text-[10px] uppercase tracking-wider font-semibold border ${colors}`}>
      {tier}
    </span>
  );
};
