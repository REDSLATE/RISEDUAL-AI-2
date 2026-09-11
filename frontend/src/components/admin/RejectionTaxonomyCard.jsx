import React, { useCallback, useEffect, useState } from 'react';
import {
  ShieldCheck, AlertOctagon, Clock3, GitBranch, HelpCircle, RefreshCw,
} from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';

const TAX_API = `${getApiBase()}/api/admin/alpha-daytrader/rejection-taxonomy?since_seconds=86400`;

/**
 * RejectionTaxonomyCard — surfaces the classification the operator
 * needs to safely turn Alpha loose: protection (correct refusal),
 * infrastructure (plumbing broken), session (market state),
 * concurrency (Alpha's own locks), unknown (missing classification).
 *
 * The core operator principle: never loosen protection when the
 * real problem is infrastructure. This tile makes that distinction
 * visible at a glance.
 */
const CLASS_META = {
  protection: {
    label: 'Protection',
    icon: ShieldCheck,
    tone: 'text-emerald-300 border-emerald-500/40 bg-emerald-500/5',
    caption: 'Alpha refused on purpose',
  },
  infrastructure: {
    label: 'Infrastructure',
    icon: AlertOctagon,
    tone: 'text-rose-300 border-rose-500/40 bg-rose-500/5',
    caption: 'Plumbing failed — investigate',
  },
  session: {
    label: 'Session',
    icon: Clock3,
    tone: 'text-slate-300 border-slate-500/40 bg-slate-500/5',
    caption: 'Market/config state',
  },
  concurrency: {
    label: 'Concurrency',
    icon: GitBranch,
    tone: 'text-amber-300 border-amber-500/40 bg-amber-500/5',
    caption: 'Alpha lock protocol',
  },
  unknown: {
    label: 'Unknown',
    icon: HelpCircle,
    tone: 'text-fuchsia-300 border-fuchsia-500/40 bg-fuchsia-500/5',
    caption: 'Update taxonomy',
  },
};

const HINT_TONE = {
  alert:   'text-rose-300 border-rose-500/40 bg-rose-500/10',
  warn:    'text-amber-300 border-amber-500/40 bg-amber-500/10',
  ok:      'text-emerald-300 border-emerald-500/40 bg-emerald-500/10',
  neutral: 'text-slate-300 border-slate-500/40 bg-slate-500/5',
};

const ReasonRow = ({ reason, count }) => (
  <div
    data-testid={`rejection-reason-row-${reason}`}
    className="flex items-center justify-between gap-2 py-0.5 text-[11px]"
  >
    <span className="truncate text-zinc-300 font-mono">{reason}</span>
    <span className="tabular-nums font-semibold text-zinc-100">{count}</span>
  </div>
);

const ClassCard = ({ klass, data }) => {
  const meta = CLASS_META[klass] || CLASS_META.unknown;
  const Icon = meta.icon;
  const total = data?.total || 0;
  const reasons = Object.entries(data?.reasons || {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6);

  return (
    <div
      data-testid={`rejection-class-${klass}`}
      className={`rounded-lg border p-3 ${meta.tone} ${total === 0 ? 'opacity-50' : ''}`}
    >
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <Icon size={14} />
          <span className="text-xs font-semibold uppercase tracking-wide">{meta.label}</span>
        </div>
        <span data-testid={`rejection-class-total-${klass}`} className="tabular-nums text-lg font-bold">
          {total}
        </span>
      </div>
      <p className="text-[10px] opacity-70 mb-2 leading-tight">{meta.caption}</p>
      {reasons.length ? (
        <div className="space-y-0.5">
          {reasons.map(([r, c]) => (<ReasonRow key={r} reason={r} count={c} />))}
        </div>
      ) : (
        <div className="text-[10px] text-zinc-500 italic">No rejections</div>
      )}
    </div>
  );
};

const RejectionTaxonomyCard = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await authFetch(TAX_API);
      if (r.ok) setData(await r.json());
    } catch (_e) {
      // Silent — dashboard tolerates transient fetch errors.
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 45_000);
    return () => clearInterval(t);
  }, [load]);

  const taxonomy = data?.taxonomy || {};
  const hint = data?.health_hint || { tone: 'neutral', message: '' };
  const total = data?.total ?? 0;

  return (
    <div
      data-testid="rejection-taxonomy-card"
      className="rounded-xl border border-zinc-800/80 bg-zinc-950/60 p-4 space-y-3"
    >
      <div className="flex items-start justify-between">
        <div>
          <h3 className="text-sm font-semibold text-zinc-100">Why Trades Didn't Reach the Broker</h3>
          <p className="text-[11px] text-zinc-500">
            24h · {total} total rejections · protection vs infrastructure vs session
          </p>
        </div>
        <button
          data-testid="rejection-taxonomy-refresh"
          className="rounded-md p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition"
          onClick={load}
          title="Refresh"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {hint?.message ? (
        <div
          data-testid="rejection-taxonomy-hint"
          data-tone={hint.tone}
          className={`rounded-md border px-3 py-2 text-xs leading-snug ${HINT_TONE[hint.tone] || HINT_TONE.neutral}`}
        >
          {hint.message}
        </div>
      ) : null}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-2">
        {['infrastructure', 'protection', 'session', 'concurrency', 'unknown'].map((k) => (
          <ClassCard key={k} klass={k} data={taxonomy[k]} />
        ))}
      </div>
    </div>
  );
};

export default RejectionTaxonomyCard;
