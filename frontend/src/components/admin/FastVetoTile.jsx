/**
 * FastVetoTile — Health-panel diagnostic for the Tier 1 Fast Veto
 * Shadow Layer.
 *
 * Reads from `/api/admin/fast-veto/stats` and renders the metrics
 * needed to evaluate the promotion-evidence checklist:
 *   • sample count vs 500 target
 *   • veto-reason histogram
 *   • Council-agreement rate
 *   • latency p50 / p95
 *   • per-row table (last 25)
 *
 * Authority: NONE. Read-only mirror of the shadow stream. Even when
 * `ready_to_enforce: true`, this tile cannot flip the env flag —
 * that stays an explicit operator action.
 */
import { useCallback, useEffect, useState } from 'react';
import { Card } from '../ui/card';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { RefreshCw, ShieldOff, AlertTriangle, CheckCircle2, Clock } from 'lucide-react';

const API = process.env.REACT_APP_BACKEND_URL;

const REASON_LABELS = {
  FAST_VETO_DRAWDOWN_BREACH: 'Drawdown breach',
  FAST_VETO_WIDE_SPREAD: 'Wide spread',
  FAST_VETO_VOL_SPIKE_LOW_CONFIDENCE: 'Vol spike + low conf',
  FAST_VETO_LOW_LIQUIDITY: 'Low liquidity',
  FAST_VETO_MODEL_CONSENSUS: 'Model consensus',
  FAST_VETO_PASS_SHADOW: 'Pass (no veto)',
};

const fmtPct = (n) => (n == null ? '—' : `${(n * 100).toFixed(1)}%`);
const fmtNum = (n) => (n == null ? '—' : Number(n).toLocaleString());
const fmtUs = (n) => (n == null ? '—' : `${Math.round(n)}µs`);

export const FastVetoTile = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // Lane scope: which slice to render in the main metric strip +
  // reason histogram. Defaults to ``aggregate`` so the v1 layout
  // is preserved for muscle memory.
  const [scope, setScope] = useState('aggregate'); // 'aggregate' | 'equity' | 'crypto'

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch(`${API}/api/admin/fast-veto/stats?limit=25`, {
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

  // Resolve the active slice based on the lane toggle.
  // ``aggregate`` returns the legacy top-level numbers; lane
  // selections drill into ``data.by_lane[scope]``. Both shapes
  // are identical so the renderers stay simple.
  const slice =
    !data
      ? null
      : scope === 'aggregate'
        ? {
            total: data.total,
            would_veto_count: data.would_veto_count,
            would_veto_rate: data.would_veto_rate,
            reason_counts: data.reason_counts,
            council_agreement: data.council_agreement,
            latency_us: data.latency_us,
            promotion_checklist: data.promotion_checklist,
          }
        : data.by_lane?.[scope] || null;

  return (
    <Card
      className="p-4 bg-slate-900/40 border-slate-700/50"
      data-testid="fast-veto-tile"
    >
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <ShieldOff className="w-4 h-4 text-cyan-400" />
          <h3 className="text-sm font-semibold text-slate-100">
            Tier 1 · Fast Veto Shadow
          </h3>
          {data && (
            <Badge
              className={
                data.enforce_enabled
                  ? 'bg-amber-500/20 text-amber-300 border-amber-500/40'
                  : 'bg-cyan-500/20 text-cyan-300 border-cyan-500/40'
              }
              data-testid="fast-veto-mode-badge"
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
          data-testid="fast-veto-refresh"
          className="h-7 text-xs"
        >
          <RefreshCw className={`w-3 h-3 mr-1 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      {error && (
        <div
          className="mb-3 rounded-lg border border-rose-500/40 bg-rose-500/10 p-2 text-xs text-rose-200"
          data-testid="fast-veto-error"
        >
          {error}
        </div>
      )}

      {!data ? (
        <div className="text-xs text-slate-400">Loading…</div>
      ) : data.total === 0 ? (
        <div
          className="rounded-lg border border-slate-700/50 bg-slate-800/30 p-3 text-xs text-slate-300"
          data-testid="fast-veto-empty"
        >
          No shadow samples yet. Layer is{' '}
          {data.shadow_enabled ? 'enabled' : 'DISABLED'}; signals will start
          appearing here once <code>execute_signal</code> is called.
        </div>
      ) : (
        <div className="space-y-3">
          {/* Per-lane summary header — always visible so the
              operator can spot lane-specific anomalies at a
              glance even before clicking into a scope. */}
          <div
            className="grid grid-cols-3 gap-2"
            data-testid="fv-lane-summary"
          >
            <LaneSummary
              label="Equity"
              bucket={data.by_lane?.equity}
              active={scope === 'equity'}
              onClick={() => setScope(scope === 'equity' ? 'aggregate' : 'equity')}
              testid="fv-lane-equity"
              dotColor="bg-amber-400"
            />
            <LaneSummary
              label="Crypto"
              bucket={data.by_lane?.crypto}
              active={scope === 'crypto'}
              onClick={() => setScope(scope === 'crypto' ? 'aggregate' : 'crypto')}
              testid="fv-lane-crypto"
              dotColor="bg-cyan-400"
            />
            <LaneSummary
              label="Unknown"
              bucket={data.by_lane?.unknown}
              active={false}
              // Unknown bucket is informational only — pre-lane-tagging
              // docs. Kept un-clickable so it doesn't pull the operator
              // into an empty-by-design slice.
              onClick={() => {}}
              testid="fv-lane-unknown"
              dotColor="bg-slate-500"
              dim
            />
          </div>

          {/* Scope toggle row. Reads as breadcrumbs: aggregate
              ▸ equity / crypto. The active scope determines which
              numbers fill the main metric strip + histogram. */}
          <div
            className="flex items-center gap-2 text-[11px]"
            data-testid="fv-scope-toggle"
          >
            <span className="text-slate-500">Showing:</span>
            <ScopeButton
              label="Aggregate"
              active={scope === 'aggregate'}
              onClick={() => setScope('aggregate')}
              testid="fv-scope-aggregate"
            />
            <ScopeButton
              label="Equity"
              active={scope === 'equity'}
              onClick={() => setScope('equity')}
              testid="fv-scope-equity"
              disabled={!data.by_lane?.equity?.total}
            />
            <ScopeButton
              label="Crypto"
              active={scope === 'crypto'}
              onClick={() => setScope('crypto')}
              testid="fv-scope-crypto"
              disabled={!data.by_lane?.crypto?.total}
            />
          </div>

          {/* Empty-slice notice — when a lane has no samples yet
              we explicitly say so instead of rendering all-zero
              metrics that look like "calibrated and ready". */}
          {slice && slice.total === 0 ? (
            <div
              className="rounded-lg border border-slate-700/40 bg-slate-800/20 p-3 text-[11px] text-slate-400"
              data-testid="fv-slice-empty"
            >
              No <strong>{scope}</strong> samples yet. Switch back to{' '}
              <button
                className="underline text-slate-200 hover:text-white"
                onClick={() => setScope('aggregate')}
                data-testid="fv-slice-empty-back"
              >
                aggregate
              </button>{' '}
              or wait for the next <code>execute_signal</code> call on this lane.
            </div>
          ) : slice && (
          <>
          {/* Top metric strip */}
          <div className="grid grid-cols-4 gap-2">
            <Metric label="Samples" value={fmtNum(slice.total)} testid="fv-samples" />
            <Metric
              label="Would-veto"
              value={fmtPct(slice.would_veto_rate)}
              sub={fmtNum(slice.would_veto_count)}
              testid="fv-would-veto-rate"
            />
            <Metric
              label="Council agree"
              value={fmtPct(slice.council_agreement?.rate)}
              sub={`${slice.council_agreement?.agree ?? 0}/${(slice.council_agreement?.agree ?? 0) + (slice.council_agreement?.disagree ?? 0)}`}
              testid="fv-agreement"
            />
            <Metric
              label="Latency p50"
              value={fmtUs(slice.latency_us?.p50)}
              sub={`p95 ${fmtUs(slice.latency_us?.p95)}`}
              testid="fv-latency"
            />
          </div>

          {/* Reason histogram */}
          {Object.keys(slice.reason_counts || {}).length > 0 && (
            <div data-testid="fv-reason-histogram">
              <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">
                Veto reasons
              </div>
              <div className="space-y-1">
                {Object.entries(slice.reason_counts)
                  .sort((a, b) => b[1] - a[1])
                  .map(([reason, count]) => {
                    const pct = slice.would_veto_count
                      ? count / slice.would_veto_count
                      : 0;
                    return (
                      <div key={reason} className="text-xs">
                        <div className="flex justify-between text-slate-300">
                          <span>{REASON_LABELS[reason] || reason}</span>
                          <span className="font-mono text-slate-400">
                            {count} · {fmtPct(pct)}
                          </span>
                        </div>
                        <div className="h-1 bg-slate-800/60 rounded mt-0.5">
                          <div
                            className="h-1 bg-cyan-500/60 rounded"
                            style={{ width: `${Math.min(100, pct * 100)}%` }}
                          />
                        </div>
                      </div>
                    );
                  })}
              </div>
            </div>
          )}

          {/* Promotion checklist */}
          <div data-testid="fv-promotion-checklist">
            <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">
              Promotion checklist {scope !== 'aggregate' && (
                <span className="text-slate-600 normal-case">· {scope}</span>
              )}
            </div>
            <div className="space-y-1">
              <ChecklistRow
                label="≥ 500 samples"
                pass={slice.promotion_checklist?.samples_500_plus?.pass}
                detail={fmtNum(slice.promotion_checklist?.samples_500_plus?.value)}
              />
              <ChecklistRow
                label="False-veto rate < 3%"
                pass={slice.promotion_checklist?.false_veto_rate_under_3pct?.pass}
                detail={fmtPct(
                  slice.promotion_checklist?.false_veto_rate_under_3pct?.value,
                )}
              />
              <ChecklistRow
                label="Median latency < 1ms"
                pass={slice.promotion_checklist?.median_latency_under_1ms?.pass}
                detail={fmtUs(
                  slice.promotion_checklist?.median_latency_under_1ms?.value,
                )}
              />
            </div>
            {slice.promotion_checklist?.ready_to_enforce && (
              <div
                className="mt-2 rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-2 text-[11px] text-emerald-200"
                data-testid="fv-ready-to-enforce"
              >
                <CheckCircle2 className="inline w-3 h-3 mr-1" />
                All criteria met — flip{' '}
                <code className="text-emerald-100">FAST_VETO_ENFORCE_ENABLED=true</code>{' '}
                in <code className="text-emerald-100">.env</code> + restart backend to promote.
              </div>
            )}
          </div>
          </>
          )}

          {/* Recent rows — filtered by scope when a lane is
              selected so the table reflects what the metric
              strip is showing. Always shown (even when slice is
              empty) for cross-lane spot-checking. */}
          {data.rows?.length > 0 && (
            <div data-testid="fv-recent-rows">
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
                      <th className="text-left px-2 py-1">Veto</th>
                      <th className="text-left px-2 py-1">Reason</th>
                      <th className="text-right px-2 py-1">Latency</th>
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
                          {r.would_veto ? (
                            <span className="text-rose-300">VETO</span>
                          ) : (
                            <span className="text-emerald-300">PASS</span>
                          )}
                        </td>
                        <td className="px-2 py-0.5 text-slate-400 truncate max-w-[180px]">
                          {REASON_LABELS[r.reason] || r.reason}
                        </td>
                        <td className="px-2 py-0.5 text-right text-slate-400">
                          {fmtUs(r.latency_us)}
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

const Metric = ({ label, value, sub, testid }) => (
  <div
    className="rounded-lg border border-slate-700/40 bg-slate-800/30 p-2"
    data-testid={testid}
  >
    <div className="text-[10px] uppercase tracking-wide text-slate-500">
      {label}
    </div>
    <div className="text-base font-mono text-slate-100">{value}</div>
    {sub && <div className="text-[10px] text-slate-500 font-mono">{sub}</div>}
  </div>
);

const LaneSummary = ({ label, bucket, active, onClick, testid, dotColor, dim }) => {
  const total = bucket?.total || 0;
  const wouldVetoRate = bucket?.would_veto_rate || 0;
  const latencyP50 = bucket?.latency_us?.p50;
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testid}
      disabled={dim || !total}
      className={[
        'rounded-lg border p-2 text-left transition-colors',
        active
          ? 'border-cyan-500/60 bg-cyan-500/10'
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
          {fmtPct(wouldVetoRate)} veto
        </span>
      </div>
      {latencyP50 != null && total > 0 && (
        <div className="text-[10px] font-mono text-slate-500">
          p50 {fmtUs(latencyP50)}
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
        ? 'bg-cyan-500/20 text-cyan-200 border border-cyan-500/40'
        : 'text-slate-400 border border-transparent hover:bg-slate-800/60',
      disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer',
    ].join(' ')}
  >
    {label}
  </button>
);

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

export default FastVetoTile;
