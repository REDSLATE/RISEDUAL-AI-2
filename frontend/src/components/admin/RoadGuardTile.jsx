/**
 * RoadGuardTile — Health-panel diagnostic for the shared
 * execution safety governor (services/roadguard.py).
 *
 * Reads from /api/admin/roadguard/stats. Mirrors the FastVetoTile
 * pattern: lane summary header, scope toggle, decision counters,
 * reason histogram, recent rows, promotion checklist.
 *
 * Authority: NONE. Read-only. Cannot flip
 * ROADGUARD_ENFORCE_ENABLED, cannot promote, cannot mutate any
 * RoadGuard state. The "ready_to_enforce" badge only INSTRUCTS
 * the operator how to flip the env flag manually.
 */
import { useCallback, useEffect, useState } from 'react';
import { Card } from '../ui/card';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import {
  RefreshCw,
  Shield,
  AlertTriangle,
  CheckCircle2,
} from 'lucide-react';

const API = process.env.REACT_APP_BACKEND_URL;

const REASON_LABELS = {
  BROKER_HEALTH_DEGRADED: 'Broker health degraded',
  MAX_DAILY_LOSS_REACHED: 'Daily loss limit',
  MAX_TOTAL_EXPOSURE: 'Total exposure cap',
  MAX_EQUITY_EXPOSURE: 'Equity exposure cap',
  MAX_CRYPTO_EXPOSURE: 'Crypto exposure cap',
  MAX_OPEN_POSITIONS_TOTAL: 'Max positions (total)',
  MAX_OPEN_POSITIONS_PER_LANE: 'Max positions (per-lane)',
  DUPLICATE_SYMBOL: 'Duplicate symbol',
};

const fmtNum = (n) => (n == null ? '—' : Number(n).toLocaleString());
const fmtUsd = (n) => (n == null ? '—' : `$${Number(n).toLocaleString()}`);

export const RoadGuardTile = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [scope, setScope] = useState('aggregate');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch(`${API}/api/admin/roadguard/stats?limit=25`, {
        credentials: 'include',
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setData(await resp.json());
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const slice =
    !data
      ? null
      : scope === 'aggregate'
        ? {
            total: data.total,
            decision_counts: data.decision_counts,
            reason_counts: data.reason_counts,
          }
        : data.by_lane?.[scope] || null;

  return (
    <Card
      className="p-4 bg-slate-900/40 border-slate-700/50"
      data-testid="roadguard-tile"
    >
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Shield className="w-4 h-4 text-emerald-400" />
          <h3 className="text-sm font-semibold text-slate-100">
            RoadGuard · Shared Execution Safety
          </h3>
          {data && (
            <Badge
              className={
                data.enforce_enabled
                  ? 'bg-amber-500/20 text-amber-300 border-amber-500/40'
                  : 'bg-cyan-500/20 text-cyan-300 border-cyan-500/40'
              }
              data-testid="roadguard-mode-badge"
            >
              {data.enforce_enabled ? 'ENFORCE' : 'SHADOW'}
            </Badge>
          )}
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={load}
          disabled={loading}
          data-testid="roadguard-refresh"
          className="h-7 text-xs"
        >
          <RefreshCw className={`w-3 h-3 mr-1 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      {error && (
        <div
          className="mb-3 rounded-lg border border-rose-500/40 bg-rose-500/10 p-2 text-xs text-rose-200"
          data-testid="roadguard-error"
        >
          {error}
        </div>
      )}

      {!data ? (
        <div className="text-xs text-slate-400">Loading…</div>
      ) : data.total === 0 ? (
        <div
          className="rounded-lg border border-slate-700/50 bg-slate-800/30 p-3 text-xs text-slate-300"
          data-testid="roadguard-empty"
        >
          No RoadGuard decisions yet. Layer is{' '}
          {data.shadow_enabled ? 'enabled' : 'DISABLED'}; decisions will start
          appearing here once a signal flows through <code>execute_signal</code>.
          {data.config && (
            <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[10px] font-mono text-slate-400">
              <div>total cap: {fmtUsd(data.config.max_total_exposure_usd)}</div>
              <div>equity cap: {fmtUsd(data.config.max_equity_exposure_usd)}</div>
              <div>crypto cap: {fmtUsd(data.config.max_crypto_exposure_usd)}</div>
              <div>daily loss: {fmtUsd(data.config.max_daily_loss_usd)}</div>
              <div>max positions: {data.config.max_open_positions_total} total / {data.config.max_open_positions_per_lane} per-lane</div>
              <div>broker min: {data.config.broker_health_min}</div>
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          {/* Per-lane summary header */}
          <div
            className="grid grid-cols-3 gap-2"
            data-testid="rg-lane-summary"
          >
            <LaneSummary
              label="Equity"
              bucket={data.by_lane?.equity}
              active={scope === 'equity'}
              onClick={() => setScope(scope === 'equity' ? 'aggregate' : 'equity')}
              testid="rg-lane-equity"
              dotColor="bg-amber-400"
            />
            <LaneSummary
              label="Crypto"
              bucket={data.by_lane?.crypto}
              active={scope === 'crypto'}
              onClick={() => setScope(scope === 'crypto' ? 'aggregate' : 'crypto')}
              testid="rg-lane-crypto"
              dotColor="bg-cyan-400"
            />
            <LaneSummary
              label="Unknown"
              bucket={data.by_lane?.unknown}
              active={false}
              onClick={() => {}}
              testid="rg-lane-unknown"
              dotColor="bg-slate-500"
              dim
            />
          </div>

          {/* Scope toggle */}
          <div
            className="flex items-center gap-2 text-[11px]"
            data-testid="rg-scope-toggle"
          >
            <span className="text-slate-500">Showing:</span>
            <ScopeButton
              label="Aggregate"
              active={scope === 'aggregate'}
              onClick={() => setScope('aggregate')}
              testid="rg-scope-aggregate"
            />
            <ScopeButton
              label="Equity"
              active={scope === 'equity'}
              onClick={() => setScope('equity')}
              testid="rg-scope-equity"
              disabled={!data.by_lane?.equity?.total}
            />
            <ScopeButton
              label="Crypto"
              active={scope === 'crypto'}
              onClick={() => setScope('crypto')}
              testid="rg-scope-crypto"
              disabled={!data.by_lane?.crypto?.total}
            />
          </div>

          {/* Decision counters */}
          {slice && slice.total === 0 ? (
            <div
              className="rounded-lg border border-slate-700/40 bg-slate-800/20 p-3 text-[11px] text-slate-400"
              data-testid="rg-slice-empty"
            >
              No <strong>{scope}</strong> decisions yet. Switch back to{' '}
              <button
                className="underline text-slate-200 hover:text-white"
                onClick={() => setScope('aggregate')}
                data-testid="rg-slice-empty-back"
              >
                aggregate
              </button>
              .
            </div>
          ) : slice && (
          <>
          <div className="grid grid-cols-4 gap-2" data-testid="rg-decision-counters">
            <Metric
              label="Total"
              value={fmtNum(slice.total)}
              testid="rg-total"
            />
            <Metric
              label="ALLOW"
              value={fmtNum(slice.decision_counts?.ALLOW)}
              accent="emerald"
              testid="rg-allow-count"
            />
            <Metric
              label="BLOCK"
              value={fmtNum(slice.decision_counts?.BLOCK)}
              accent="rose"
              testid="rg-block-count"
            />
            <Metric
              label="PAUSE_LANE"
              value={fmtNum(slice.decision_counts?.PAUSE_LANE)}
              accent="amber"
              testid="rg-pause-count"
            />
          </div>

          {/* Reason histogram */}
          {Object.keys(slice.reason_counts || {}).length > 0 ? (
            <div data-testid="rg-reason-histogram">
              <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">
                Block reasons
              </div>
              <div className="space-y-1">
                {Object.entries(slice.reason_counts)
                  .sort((a, b) => b[1] - a[1])
                  .map(([reason, count]) => {
                    const nonAllow =
                      (slice.decision_counts?.BLOCK ?? 0) +
                      (slice.decision_counts?.PAUSE_LANE ?? 0);
                    const pct = nonAllow ? count / nonAllow : 0;
                    return (
                      <div key={reason} className="text-xs">
                        <div className="flex justify-between text-slate-300">
                          <span>{REASON_LABELS[reason] || reason}</span>
                          <span className="font-mono text-slate-400">
                            {count} · {(pct * 100).toFixed(0)}%
                          </span>
                        </div>
                        <div className="h-1 bg-slate-800/60 rounded mt-0.5">
                          <div
                            className="h-1 bg-rose-500/60 rounded"
                            style={{ width: `${Math.min(100, pct * 100)}%` }}
                          />
                        </div>
                      </div>
                    );
                  })}
              </div>
            </div>
          ) : (
            <div
              className="text-[11px] text-slate-500 italic"
              data-testid="rg-no-blocks"
            >
              No BLOCK / PAUSE_LANE decisions in this scope yet.
            </div>
          )}
          </>
          )}

          {/* Promotion checklist (always reads from aggregate — promotion is account-wide) */}
          <div data-testid="rg-promotion-checklist">
            <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">
              Promotion checklist
            </div>
            <div className="space-y-1">
              <ChecklistRow
                label="Shadow enabled"
                pass={data.promotion_checklist?.shadow_enabled?.pass}
                detail={String(data.promotion_checklist?.shadow_enabled?.value)}
              />
              <ChecklistRow
                label="Enforce currently disabled"
                pass={data.promotion_checklist?.enforce_disabled?.pass}
                detail={data.promotion_checklist?.enforce_disabled?.value
                  ? 'enforce ON' : 'enforce OFF'}
              />
              <ChecklistRow
                label="≥ 500 decisions observed"
                pass={data.promotion_checklist?.samples_500_plus?.pass}
                detail={fmtNum(data.promotion_checklist?.samples_500_plus?.value)}
              />
              <ChecklistRow
                label="Zero false BLOCKs marked"
                pass={data.promotion_checklist?.zero_false_blocks?.pass}
                detail={fmtNum(data.promotion_checklist?.zero_false_blocks?.value)}
              />
              <ChecklistRow
                label="Broker-health rule observed"
                pass={data.promotion_checklist?.broker_health_rule_observed?.pass}
                detail={fmtNum(data.promotion_checklist?.broker_health_rule_observed?.value)}
              />
              <ChecklistRow
                label="Duplicate-symbol rule observed"
                pass={data.promotion_checklist?.duplicate_symbol_rule_observed?.pass}
                detail={fmtNum(data.promotion_checklist?.duplicate_symbol_rule_observed?.value)}
              />
              <ChecklistRow
                label="Exposure-cap rule observed"
                pass={data.promotion_checklist?.exposure_cap_rule_observed?.pass}
                detail={fmtNum(data.promotion_checklist?.exposure_cap_rule_observed?.value)}
              />
            </div>
            {data.promotion_checklist?.ready_to_enforce && (
              <div
                className="mt-2 rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-2 text-[11px] text-emerald-200"
                data-testid="rg-ready-to-enforce"
              >
                <CheckCircle2 className="inline w-3 h-3 mr-1" />
                All criteria met — flip{' '}
                <code className="text-emerald-100">ROADGUARD_ENFORCE_ENABLED=true</code>{' '}
                in <code className="text-emerald-100">.env</code> + restart backend to promote.
                <span className="block mt-1 text-emerald-300/70">
                  Recommend at least one full week of clean shadow data first.
                </span>
              </div>
            )}
          </div>

          {/* Recent rows (filtered by scope) */}
          {data.rows?.length > 0 && (
            <div data-testid="rg-recent-rows">
              <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">
                Last {(scope === 'aggregate'
                  ? data.rows
                  : data.rows.filter((r) => r.lane === scope)).length}
                {scope !== 'aggregate' && (
                  <span className="text-slate-600 normal-case"> · {scope}</span>
                )}
              </div>
              <div className="max-h-48 overflow-auto rounded border border-slate-700/40">
                <table className="w-full text-[11px] font-mono">
                  <thead className="sticky top-0 bg-slate-900/80 text-slate-500">
                    <tr>
                      <th className="text-left px-2 py-1">Symbol</th>
                      <th className="text-left px-2 py-1">Lane</th>
                      <th className="text-left px-2 py-1">Decision</th>
                      <th className="text-left px-2 py-1">Reason</th>
                      <th className="text-left px-2 py-1">Enforced</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(scope === 'aggregate'
                      ? data.rows
                      : data.rows.filter((r) => r.lane === scope)
                    ).map((r, i) => (
                      <tr
                        key={`${r.created_at}-${i}`}
                        className="border-t border-slate-800/60"
                      >
                        <td className="px-2 py-0.5 text-slate-300">
                          {r.symbol || '—'}
                        </td>
                        <td className="px-2 py-0.5 text-slate-500">
                          {r.lane || '—'}
                        </td>
                        <td className="px-2 py-0.5">
                          <DecisionBadge value={r.decision} />
                        </td>
                        <td className="px-2 py-0.5 text-slate-400 truncate max-w-[180px]">
                          {r.reason ? (REASON_LABELS[r.reason] || r.reason) : '—'}
                        </td>
                        <td className="px-2 py-0.5 text-slate-500">
                          {r.enforced ? 'yes' : 'no'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
};

const Metric = ({ label, value, sub, accent, testid }) => {
  const accentClass =
    accent === 'emerald'
      ? 'text-emerald-300'
      : accent === 'rose'
        ? 'text-rose-300'
        : accent === 'amber'
          ? 'text-amber-300'
          : 'text-slate-100';
  return (
    <div
      className="rounded-lg border border-slate-700/40 bg-slate-800/30 p-2"
      data-testid={testid}
    >
      <div className="text-[10px] uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div className={`text-base font-mono ${accentClass}`}>{value}</div>
      {sub && <div className="text-[10px] text-slate-500 font-mono">{sub}</div>}
    </div>
  );
};

const LaneSummary = ({ label, bucket, active, onClick, testid, dotColor, dim }) => {
  const total = bucket?.total || 0;
  const blocks = bucket?.decision_counts?.BLOCK || 0;
  const pauses = bucket?.decision_counts?.PAUSE_LANE || 0;
  const blockRate = total ? (blocks + pauses) / total : 0;
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testid}
      disabled={dim || !total}
      className={[
        'rounded-lg border p-2 text-left transition-colors',
        active
          ? 'border-emerald-500/60 bg-emerald-500/10'
          : 'border-slate-700/40 bg-slate-800/30 hover:bg-slate-800/60',
        dim ? 'opacity-50 cursor-default' : 'cursor-pointer',
        !total && !dim ? 'opacity-60' : '',
      ].join(' ')}
    >
      <div className="flex items-center gap-2">
        <span className={`w-1.5 h-1.5 rounded-full ${dotColor}`} />
        <span className="text-[10px] uppercase tracking-wide text-slate-400">
          {label}
        </span>
      </div>
      <div className="mt-1 flex items-baseline justify-between">
        <span className="text-base font-mono text-slate-100">
          {fmtNum(total)}
        </span>
        <span className="text-[10px] font-mono text-slate-500">
          {(blockRate * 100).toFixed(0)}% block
        </span>
      </div>
      {total > 0 && (
        <div className="text-[10px] font-mono text-slate-500">
          A {bucket?.decision_counts?.ALLOW ?? 0} · B {blocks} · P {pauses}
        </div>
      )}
    </button>
  );
};

const ScopeButton = ({ label, active, onClick, testid, disabled }) => (
  <button
    type="button"
    onClick={onClick}
    disabled={disabled}
    data-testid={testid}
    className={[
      'px-2 py-0.5 rounded font-mono transition-colors',
      active
        ? 'bg-emerald-500/20 text-emerald-200 border border-emerald-500/40'
        : 'text-slate-400 border border-transparent hover:bg-slate-800/60',
      disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer',
    ].join(' ')}
  >
    {label}
  </button>
);

const DecisionBadge = ({ value }) => {
  if (value === 'ALLOW') {
    return <span className="text-emerald-300">ALLOW</span>;
  }
  if (value === 'BLOCK') {
    return <span className="text-rose-300">BLOCK</span>;
  }
  if (value === 'PAUSE_LANE') {
    return <span className="text-amber-300">PAUSE</span>;
  }
  return <span className="text-slate-400">{value || '—'}</span>;
};

const ChecklistRow = ({ label, pass, detail }) => (
  <div className="flex items-center justify-between text-xs">
    <span className="flex items-center gap-2 text-slate-300">
      {pass ? (
        <CheckCircle2 className="w-3 h-3 text-emerald-400" />
      ) : (
        <AlertTriangle className="w-3 h-3 text-amber-400" />
      )}
      {label}
    </span>
    <span className="font-mono text-slate-400">{detail}</span>
  </div>
);

export default RoadGuardTile;
