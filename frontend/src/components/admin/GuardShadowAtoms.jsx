import React, { useState } from 'react';
import { ShieldAlert, ShieldCheck } from 'lucide-react';

// Extracted from GuardShadowPanel.jsx (2026-05-03) to keep the main
// panel under ~570 lines. These atoms are purely presentational — no
// API calls, no closures over parent state, safe to live in their own
// module. Used only by GuardShadowPanel today; if another admin view
// needs them later, promote to /components/ui/.

export const Stat = ({ label, value, sub, tone, testId }) => {
  const valueClass =
    tone === 'pos'
      ? 'text-emerald-400'
      : tone === 'caution'
      ? 'text-amber-300'
      : tone === 'warn'
      ? 'text-red-400'
      : 'text-white';
  return (
    <div
      className="p-3 rounded-lg bg-slate-800/40 border border-slate-700/40"
      data-testid={testId}
    >
      <div className="text-[10px] text-slate-400 uppercase tracking-wider">
        {label}
      </div>
      <div className={`text-lg font-semibold font-mono mt-1 ${valueClass}`}>
        {value}
      </div>
      {sub && <div className="text-[10px] text-slate-500 mt-1">{sub}</div>}
    </div>
  );
};

export const Field = ({ label, value }) => (
  <div className="flex items-baseline justify-between gap-2 text-[11px]">
    <span className="text-slate-500 uppercase tracking-wider text-[9px]">
      {label}
    </span>
    <span className="text-slate-200 font-mono">{value}</span>
  </div>
);

export const DecisionRow = ({ decision: d }) => {
  const [open, setOpen] = useState(false);
  const ts = d.created_at ? d.created_at.slice(0, 19).replace('T', ' ') : '?';
  const blocked = !d.would_allow;
  const Icon = blocked ? ShieldAlert : ShieldCheck;
  const reasonsLine = (d.reasons || []).slice(0, 2).join(' · ');

  return (
    <div
      className={`text-xs rounded-md bg-slate-900/40 border ${
        blocked ? 'border-red-500/30' : 'border-slate-700/30'
      } overflow-hidden`}
      data-testid={`guard-shadow-decision-${d.entity_id}`}
    >
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full text-left p-2 flex items-center justify-between hover:bg-slate-800/40 transition-colors"
      >
        <div className="flex items-center gap-2 min-w-0">
          <Icon
            className={`w-3.5 h-3.5 flex-shrink-0 ${
              blocked ? 'text-red-400' : 'text-emerald-400'
            }`}
          />
          <span className="font-mono text-slate-200 truncate">
            {d.entity_id}
          </span>
          <span className="text-slate-500 text-[10px]">{d.source}</span>
        </div>
        <div className="flex items-center gap-3 text-[11px] text-slate-400">
          <span>{ts}</span>
          <span
            className={`font-mono ${
              blocked ? 'text-red-400' : 'text-emerald-400'
            }`}
          >
            {d.would_action || '—'}
          </span>
        </div>
      </button>
      {reasonsLine && (
        <div className="px-2 pb-2 text-[10px] text-slate-500 truncate">
          {reasonsLine}
        </div>
      )}
      {open && (
        <div className="border-t border-slate-700/40 p-2 space-y-1.5 bg-slate-900/60">
          <Field label="Would notional" value={`$${(d.would_notional ?? 0).toFixed(2)}`} />
          <Field label="Executed action" value={d.executed_action ?? '—'} />
          <Field
            label="Executed notional"
            value={
              d.executed_notional != null
                ? `$${Number(d.executed_notional).toFixed(2)}`
                : '—'
            }
          />
          <Field
            label="Risk multiplier"
            value={(d.would_risk_multiplier ?? 0).toFixed(2)}
          />
          {d.proof_hashes && d.proof_hashes.length > 0 && (
            <Field
              label="Proof hashes"
              value={
                <span className="font-mono text-[10px] break-all">
                  {(d.proof_hashes[d.proof_hashes.length - 1] || '').slice(0, 24)}…
                </span>
              }
            />
          )}
          {d.context && Object.keys(d.context).length > 0 && (
            <details className="text-[10px] text-slate-500">
              <summary className="cursor-pointer hover:text-slate-300">
                context
              </summary>
              <pre className="mt-1 p-2 bg-slate-950/60 rounded overflow-auto">
                {JSON.stringify(d.context, null, 2)}
              </pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
};
