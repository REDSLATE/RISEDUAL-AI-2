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
          {/* Top metric strip */}
          <div className="grid grid-cols-4 gap-2">
            <Metric label="Samples" value={fmtNum(data.total)} testid="fv-samples" />
            <Metric
              label="Would-veto"
              value={fmtPct(data.would_veto_rate)}
              sub={fmtNum(data.would_veto_count)}
              testid="fv-would-veto-rate"
            />
            <Metric
              label="Council agree"
              value={fmtPct(data.council_agreement?.rate)}
              sub={`${data.council_agreement?.agree ?? 0}/${(data.council_agreement?.agree ?? 0) + (data.council_agreement?.disagree ?? 0)}`}
              testid="fv-agreement"
            />
            <Metric
              label="Latency p50"
              value={fmtUs(data.latency_us?.p50)}
              sub={`p95 ${fmtUs(data.latency_us?.p95)}`}
              testid="fv-latency"
            />
          </div>

          {/* Reason histogram */}
          {Object.keys(data.reason_counts || {}).length > 0 && (
            <div data-testid="fv-reason-histogram">
              <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">
                Veto reasons
              </div>
              <div className="space-y-1">
                {Object.entries(data.reason_counts)
                  .sort((a, b) => b[1] - a[1])
                  .map(([reason, count]) => {
                    const pct = data.would_veto_count
                      ? count / data.would_veto_count
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
              Promotion checklist
            </div>
            <div className="space-y-1">
              <ChecklistRow
                label="≥ 500 samples"
                pass={data.promotion_checklist?.samples_500_plus?.pass}
                detail={fmtNum(data.promotion_checklist?.samples_500_plus?.value)}
              />
              <ChecklistRow
                label="False-veto rate < 3%"
                pass={data.promotion_checklist?.false_veto_rate_under_3pct?.pass}
                detail={fmtPct(
                  data.promotion_checklist?.false_veto_rate_under_3pct?.value,
                )}
              />
              <ChecklistRow
                label="Median latency < 1ms"
                pass={data.promotion_checklist?.median_latency_under_1ms?.pass}
                detail={fmtUs(
                  data.promotion_checklist?.median_latency_under_1ms?.value,
                )}
              />
            </div>
            {data.promotion_checklist?.ready_to_enforce && (
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

          {/* Recent rows */}
          {data.rows?.length > 0 && (
            <div data-testid="fv-recent-rows">
              <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">
                Last {data.rows.length}
              </div>
              <div className="max-h-48 overflow-auto rounded border border-slate-700/40">
                <table className="w-full text-[11px] font-mono">
                  <thead className="sticky top-0 bg-slate-900/80 text-slate-500">
                    <tr>
                      <th className="text-left px-2 py-1">Symbol</th>
                      <th className="text-left px-2 py-1">Veto</th>
                      <th className="text-left px-2 py-1">Reason</th>
                      <th className="text-right px-2 py-1">Latency</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.rows.map((r, i) => (
                      <tr
                        key={`${r.created_at}-${i}`}
                        className="border-t border-slate-800/60"
                      >
                        <td className="px-2 py-0.5 text-slate-300">
                          {r.symbol || '—'}
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
