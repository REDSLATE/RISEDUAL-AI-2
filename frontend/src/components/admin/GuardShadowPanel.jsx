import React, { useCallback, useEffect, useState } from 'react';
import { Eye, RefreshCw, Filter, ShieldCheck, ShieldAlert, AlertTriangle } from 'lucide-react';
import { authFetch } from '../../contexts/AuthContext';
import { getApiBase } from '../../utils/apiBase';
import { Input } from '../ui/input';

const API = `${getApiBase()}/api/admin/guard-shadow`;

/**
 * GuardShadowPanel — operator surface for the Decision Pipeline Guard
 * shadow rollout (Step 5 of the IP rollout playbook).
 *
 * Two panes:
 *   1. Summary card — would-block rate, by-source breakdown, top
 *      blocking reasons over the last N hours.
 *   2. Decision feed — paginated raw rows with optional source +
 *      "only blocked" filters.
 *
 * Owner-only. `/api/admin/guard-shadow/*` 403s non-owners.
 */
const GuardShadowPanel = () => {
  const [summary, setSummary] = useState(null);
  const [decisions, setDecisions] = useState([]);
  const [windowHours, setWindowHours] = useState(168); // 7 days default
  const [source, setSource] = useState('');
  const [onlyBlocked, setOnlyBlocked] = useState(false);
  const [entitySubstr, setEntitySubstr] = useState('');
  const [loading, setLoading] = useState(false);

  const loadSummary = useCallback(async () => {
    try {
      const r = await authFetch(`${API}/summary?hours=${windowHours}`);
      if (r.ok) setSummary(await r.json());
    } catch {
      /* non-critical */
    }
  }, [windowHours]);

  const loadDecisions = useCallback(async () => {
    setLoading(true);
    try {
      const url = new URL(`${API}/decisions`);
      url.searchParams.set('limit', '50');
      if (source) url.searchParams.set('source', source);
      if (onlyBlocked) url.searchParams.set('only_blocked', 'true');
      if (entitySubstr) url.searchParams.set('entity_substr', entitySubstr);
      const r = await authFetch(url.toString());
      if (r.ok) {
        const data = await r.json();
        setDecisions(data.decisions || []);
      }
    } catch {
      /* non-critical */
    } finally {
      setLoading(false);
    }
  }, [source, onlyBlocked, entitySubstr]);

  useEffect(() => {
    loadSummary();
    loadDecisions();
  }, [loadSummary, loadDecisions]);

  const handleRefresh = () => {
    loadSummary();
    loadDecisions();
  };

  const blockRatePct =
    summary && summary.total > 0
      ? Math.round((summary.would_block_rate || 0) * 1000) / 10
      : 0;

  return (
    <div
      className="space-y-6 p-1"
      data-testid="guard-shadow-panel"
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-white tracking-wide flex items-center gap-2">
            <Eye className="w-4 h-4 text-[#3DE8D9]" />
            Decision Pipeline Guard — Shadow Mode
          </h2>
          <p className="text-[11px] text-slate-400 mt-1">
            What the IP contract WOULD block vs what actually executed.
            Compare before flipping per-patent enforcement on.
          </p>
        </div>
        <button
          onClick={handleRefresh}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-slate-700/40 hover:bg-slate-700/60 border border-slate-600/40 text-slate-200 text-xs font-medium transition-colors"
          data-testid="guard-shadow-refresh"
        >
          <RefreshCw className="w-3 h-3" />
          Refresh
        </button>
      </div>

      {/* Window selector */}
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <span className="uppercase tracking-wider text-[10px]">Window:</span>
        {[24, 72, 168, 720].map((h) => (
          <button
            key={h}
            onClick={() => setWindowHours(h)}
            className={`px-2 py-1 rounded-md border text-[10px] font-medium transition-colors ${
              windowHours === h
                ? 'bg-[#3DE8D9]/10 border-[#3DE8D9]/30 text-[#3DE8D9]'
                : 'bg-slate-800/40 border-slate-700/40 text-slate-300 hover:border-slate-600/60'
            }`}
            data-testid={`guard-shadow-window-${h}`}
          >
            {h === 24 ? '24h' : h === 72 ? '3d' : h === 168 ? '7d' : '30d'}
          </button>
        ))}
      </div>

      {/* Summary grid */}
      {summary && (
        <div
          className="grid grid-cols-1 md:grid-cols-3 gap-3"
          data-testid="guard-shadow-summary"
        >
          <Stat
            label="Shadow Mode"
            value={
              summary.shadow_mode_enabled ? (
                <span className="flex items-center gap-1.5 text-amber-300">
                  <Eye className="w-3.5 h-3.5" /> ON
                </span>
              ) : (
                <span className="flex items-center gap-1.5 text-emerald-400">
                  <ShieldCheck className="w-3.5 h-3.5" /> ENFORCING
                </span>
              )
            }
            sub={
              summary.shadow_mode_enabled
                ? 'GUARD_SHADOW_MODE=1 — verdicts logged, not enforced'
                : 'GUARD_SHADOW_MODE=0 — guard verdicts are live'
            }
            testId="guard-shadow-stat-mode"
          />
          <Stat
            label="Decisions Logged"
            value={summary.total ?? 0}
            sub={`${summary.would_allow ?? 0} allow · ${
              summary.would_block ?? 0
            } block`}
            testId="guard-shadow-stat-total"
          />
          <Stat
            label="Would-Block Rate"
            value={`${blockRatePct}%`}
            tone={
              blockRatePct >= 25
                ? 'warn'
                : blockRatePct >= 10
                ? 'caution'
                : 'pos'
            }
            sub={
              blockRatePct >= 25
                ? 'Aggressive — review before enforcing'
                : blockRatePct >= 10
                ? 'Moderate'
                : 'Low — safe to enforce'
            }
            testId="guard-shadow-stat-block-rate"
          />
        </div>
      )}

      {/* By source */}
      {summary && summary.by_source && Object.keys(summary.by_source).length > 0 && (
        <div
          className="p-3 rounded-lg bg-slate-800/40 border border-slate-700/40"
          data-testid="guard-shadow-by-source"
        >
          <div className="text-[10px] text-slate-400 uppercase tracking-wider mb-2">
            By Source
          </div>
          <div className="space-y-1.5">
            {Object.entries(summary.by_source).map(([src, b]) => {
              const total = (b.would_allow || 0) + (b.would_block || 0);
              const blockPct =
                total > 0
                  ? Math.round(((b.would_block || 0) / total) * 100)
                  : 0;
              return (
                <div
                  key={src}
                  className="flex items-center justify-between text-xs"
                  data-testid={`guard-shadow-source-${src}`}
                >
                  <span className="text-slate-200 font-mono">{src}</span>
                  <div className="flex items-center gap-3">
                    <span className="text-emerald-400">
                      {b.would_allow || 0} ✓
                    </span>
                    <span className="text-red-400">
                      {b.would_block || 0} ✗
                    </span>
                    <span className="text-slate-400 w-12 text-right">
                      {blockPct}% blk
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Top block reasons */}
      {summary && summary.top_block_reasons && summary.top_block_reasons.length > 0 && (
        <div
          className="p-3 rounded-lg bg-slate-800/40 border border-slate-700/40"
          data-testid="guard-shadow-top-reasons"
        >
          <div className="text-[10px] text-slate-400 uppercase tracking-wider mb-2 flex items-center gap-1.5">
            <AlertTriangle className="w-3 h-3 text-amber-400" />
            Top Blocking Reasons
          </div>
          <div className="space-y-1">
            {summary.top_block_reasons.map((r) => (
              <div
                key={r.reason}
                className="flex items-center justify-between text-xs"
              >
                <span className="text-slate-300 font-mono truncate">
                  {r.reason}
                </span>
                <span className="text-amber-300 ml-2">{r.count}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-end gap-2 pt-2 border-t border-slate-700/40">
        <div className="flex items-center gap-1.5 text-[10px] text-slate-400 uppercase tracking-wider">
          <Filter className="w-3 h-3" />
          Filter
        </div>
        <Input
          placeholder="source (e.g. crypto_bot)"
          value={source}
          onChange={(e) => setSource(e.target.value)}
          className="bg-slate-800 border-slate-600 text-white h-8 text-xs w-44"
          data-testid="guard-shadow-filter-source"
        />
        <Input
          placeholder="entity contains…"
          value={entitySubstr}
          onChange={(e) => setEntitySubstr(e.target.value)}
          className="bg-slate-800 border-slate-600 text-white h-8 text-xs w-44"
          data-testid="guard-shadow-filter-entity"
        />
        <label className="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={onlyBlocked}
            onChange={(e) => setOnlyBlocked(e.target.checked)}
            className="accent-[#3DE8D9]"
            data-testid="guard-shadow-filter-only-blocked"
          />
          only blocked
        </label>
        <button
          onClick={loadDecisions}
          disabled={loading}
          className="px-3 py-1.5 rounded-md bg-[#3DE8D9] hover:bg-[#7AEEE0] disabled:opacity-40 text-slate-900 text-xs font-semibold transition-colors"
          data-testid="guard-shadow-apply-filters"
        >
          Apply
        </button>
      </div>

      {/* Decisions list */}
      <div
        className="space-y-1.5"
        data-testid="guard-shadow-decisions-list"
      >
        {decisions.length === 0 && (
          <div className="text-xs text-slate-500 italic p-3">
            No decisions match the current filters.
            {!summary?.shadow_mode_enabled && (
              <>
                {' '}
                Set <code className="font-mono text-[#3DE8D9]">GUARD_SHADOW_MODE=1</code>{' '}
                in <code className="font-mono">backend/.env</code> and trigger a bot run
                to populate this view.
              </>
            )}
          </div>
        )}
        {decisions.map((d, i) => (
          <DecisionRow
            key={`${d.entity_id}-${d.created_at}-${i}`}
            decision={d}
          />
        ))}
      </div>
    </div>
  );
};

// ── Sub-components ────────────────────────────────────────────────────

const Stat = ({ label, value, sub, tone, testId }) => {
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

const DecisionRow = ({ decision: d }) => {
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

const Field = ({ label, value }) => (
  <div className="flex items-baseline justify-between gap-2 text-[11px]">
    <span className="text-slate-500 uppercase tracking-wider text-[9px]">
      {label}
    </span>
    <span className="text-slate-200 font-mono">{value}</span>
  </div>
);

export default GuardShadowPanel;
